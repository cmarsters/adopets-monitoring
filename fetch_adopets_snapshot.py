# fetch_adopets_snapshot.py
import copy
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
import os
import requests
from dotenv import load_dotenv
from adopets_client import AdopetsClient

load_dotenv()
SNAPSHOT_DIR = Path(os.environ.get("SNAPSHOT_DIR", "snapshots"))
SNAPSHOT_DIR.mkdir(exist_ok=True)

# === CONFIG ===
FIND_URL = "https://service.api.prd.adopets.app/adopter/pet/find?lang=en"
DETAIL_URL = "https://service.api.prd.adopets.app/adopter/pet/get?lang=en"


DETAIL_PAYLOAD_TEMPLATE = {
    "pet_uuid": None,  # we'll fill this in per dog
    "search": {
        "pet_characteristics": {
            "with": {
                "deleted": False,
                "_fields": ["pet_id", "id", "characteristic_id"],
                "characteristic": {
                    "with": {
                        "_fields": [
                            "characteristic_group_id",
                            "id",
                            "uuid",
                            "key",
                            "name",
                            "description",
                            "alias",
                        ],
                        "group": {
                            "with": {
                                "_fields": ["id", "uuid", "name", "description"]
                            }
                        },
                        "deleted": False,
                    }
                },
            }
        }
    },
    "tracker_uuid": "20726cce-8281-4909-9c4e-0272c989bc19",
}

SHELTER_UUID = "8a047e71-c644-45e3-9a9c-e7b83d18c48f"


def fetch_list(client: AdopetsClient, limit=700):
    """Fetch the list of adoptable animals (summary data) using AdopetsClient."""
    return client.fetch_all_pets(batch_size=limit)


def fetch_detail(uuid: str, client: AdopetsClient):
    """Fetch full detail (including characteristics) for one dog."""
    payload = copy.deepcopy(DETAIL_PAYLOAD_TEMPLATE)
    payload["pet_uuid"] = uuid

    token = client.get_token()
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    resp = requests.post(DETAIL_URL, json=payload, headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    if data.get("data") is None:
        raise RuntimeError(f"Detail API error: {data.get('message')}")

    return data["data"]


def normalize_record(list_item: dict, detail_data: dict) -> dict:
    org_pet_list = list_item["organization_pet"]
    org_pet_detail = detail_data.get("organization_pet", {})

    # Characteristics live under organization_pet["_extends"]["pet_characteristics"]
    extends = org_pet_detail.get("_extends", {}) or {}
    pet_chars = extends.get("pet_characteristics", []) or []

    char_keys = []
    char_names = []

    for c in pet_chars:
        pc = None

        if isinstance(c, dict):
            if "public_characteristic" in c:
                pc = c["public_characteristic"]
            elif "characteristic" in c:
                pc = c["characteristic"]

        if not pc:
            continue

        key = pc.get("key")
        name = pc.get("name")
        if key:
            char_keys.append(key)
        if name:
            char_names.append(name)

    # Compute a friendly location label
    foster = org_pet_list.get("foster")
    kennel = org_pet_list.get("kennel_number")

    if foster:
        location_label = "Foster"
    elif kennel:
        location_label = f"Kennel {kennel}"
    else:
        location_label = "Unspecified"

    return {
        "uuid": org_pet_list.get("uuid"),
        "animal_id": org_pet_list.get("code"),
        "name": org_pet_list.get("name"),

        # basic info
        "species": org_pet_list.get("specie_name"),
        "sex": org_pet_list.get("sex_key"),
        "age_key": org_pet_list.get("age_key"),
        "size_key": org_pet_list.get("size_key"),
        "breed_primary_name": org_pet_list.get("breed_primary_name"),

        # location and availability
        "status": org_pet_list.get("status_key"), 
        "foster": foster,
        "kennel_number": kennel,
        "location": location_label,

        # media + description 
        "picture": org_pet_list.get("picture"),
        "description_html": (
            org_pet_detail.get("description")
            or org_pet_list.get("description")
        ),
        "characteristic_keys": char_keys,
        "characteristic_names": char_names,
    }


#########################################################
#########################################################


def main():
    client = AdopetsClient(shelter_uuid=SHELTER_UUID)

    list_items = fetch_list(client)
    print(f"Got {len(list_items)} animals from Adopets")

    records = [None] * len(list_items)

    def build_record(idx, item):
        uuid = item["organization_pet"]["uuid"]
        detail = fetch_detail(uuid, client)
        return idx, normalize_record(item, detail)

    # Use threads to fetch pet details concurrently for better throughput.
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {
            executor.submit(build_record, idx, item): idx
            for idx, item in enumerate(list_items)
        }
        for future in as_completed(futures):
            idx, record = future.result()
            records[idx] = record

    now = datetime.now()
    stamp = now.strftime("%Y-%m-%dT%H-%M-%S")  # e.g. 2025-12-03T14-52-10
    out_path = SNAPSHOT_DIR / f"{stamp}.json"

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(records)} animal records to: {out_path}")


if __name__ == "__main__":
    main()
