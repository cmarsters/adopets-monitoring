"""
Adopets API Client

A simple client for fetching pet data from the Adopets API with automatic token management.

Usage:
    from adopets_client import AdopetsClient

    client = AdopetsClient(shelter_uuid="8a047e71-c644-45e3-9a9c-e7b83d18c48f")
    pets = client.fetch_pets(limit=12)

    for pet in pets:
        print(pet["name"])
"""

import requests
import time
import base64
import json
from typing import Optional
from dataclasses import dataclass


@dataclass
class TokenInfo:
    access_key: str
    issued_at: float
    expires_at: float


class AdopetsClient:
    API_BASE = "https://service.api.prd.adopets.app"
    SYSTEM_API_KEY = "3543d587-7395-4f56-a3d5-00340826ad4c"
    TOKEN_BUFFER_SECONDS = 3600  # Refresh token 1 hour before expiry
    TOKEN_LIFETIME_SECONDS = 14 * 24 * 60 * 60  # ~2 weeks

    def __init__(self, shelter_uuid: str):
        self.shelter_uuid = shelter_uuid
        self._token_info: Optional[TokenInfo] = None

    def _parse_jwt_payload(self, token: str) -> dict:
        """Decode the JWT payload (without verification)."""
        payload_b64 = token.split(".")[1]
        # Add padding if needed
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload_json = base64.urlsafe_b64decode(payload_b64)
        return json.loads(payload_json)

    def _fetch_new_token(self) -> TokenInfo:
        """Fetch a fresh token from the API."""
        response = requests.post(
            f"{self.API_BASE}/adopter/auth/session-request",
            params={"lang": "en"},
            json={"system_api_key": self.SYSTEM_API_KEY},
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()

        data = response.json()
        access_key = data["data"]["access_key"]

        # Parse the JWT to get issued-at time
        payload = self._parse_jwt_payload(access_key)
        issued_at = payload.get("iat", time.time())
        expires_at = issued_at + self.TOKEN_LIFETIME_SECONDS

        return TokenInfo(
            access_key=access_key,
            issued_at=issued_at,
            expires_at=expires_at,
        )

    def get_token(self) -> str:
        """Get a valid token, refreshing if necessary."""
        now = time.time()

        if self._token_info is None:
            self._token_info = self._fetch_new_token()
        elif now >= self._token_info.expires_at - self.TOKEN_BUFFER_SECONDS:
            self._token_info = self._fetch_new_token()

        return self._token_info.access_key

    def fetch_pets(
        self,
        limit: int = 12,
        offset: int = 0,
        specie_uuids: Optional[list[str]] = None,
        breed_uuids: Optional[list[str]] = None,
        size_keys: Optional[list[str]] = None,
        sex_keys: Optional[list[str]] = None,
        age_keys: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Fetch pets from the shelter.

        Args:
            limit: Maximum number of pets to return (default 12)
            offset: Pagination offset (default 0)
            specie_uuids: Filter by species UUIDs
            breed_uuids: Filter by breed UUIDs
            size_keys: Filter by size (e.g., ["SMALL", "MEDIUM", "LARGE"])
            sex_keys: Filter by sex (e.g., ["MALE", "FEMALE"])
            age_keys: Filter by age (e.g., ["BABY", "YOUNG", "ADULT", "SENIOR"])

        Returns:
            List of pet dictionaries
        """
        token = self.get_token()

        payload = {
            "limit": limit,
            "offset": offset,
            "shelter_uuid": self.shelter_uuid,
            "origin_key": "ORGANIZATION_PAGE",
            "organization_pet": {
                "specie_uuid": specie_uuids or [],
                "breed_uuid": breed_uuids or [],
                "size_key": size_keys or [],
                "sex_key": sex_keys or [],
                "age_key": age_keys or [],
            },
            "user_interaction": False,
        }

        response = requests.post(
            f"{self.API_BASE}/adopter/pet/find",
            params={"lang": "en"},
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        response.raise_for_status()

        data = response.json()
        return data.get("data", {}).get("result", [])

    def fetch_all_pets(self, batch_size: int = 50) -> list[dict]:
        """
        Fetch all pets from the shelter using pagination.

        Args:
            batch_size: Number of pets to fetch per request (default 50)

        Returns:
            List of all pet dictionaries
        """
        all_pets = []
        offset = 0

        while True:
            pets = self.fetch_pets(limit=batch_size, offset=offset)
            if not pets:
                break
            all_pets.extend(pets)
            offset += batch_size

            # Safety check to avoid infinite loops
            if len(pets) < batch_size:
                break

        return all_pets
