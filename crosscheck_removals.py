# crosscheck_removals.py

"""
Script for checking if adopets profiles removed in the last day correspond to outcomes. 
    1) Reads most recent diff file (diff_*json) from the snapshots directory
    2) Loads outcomes between the two snapshot timestamps.
    3) For each removed Adopets profile:
            a. Looks for outcomes with matching Animal ID.
            b. Tags the removal with:
                - "outcome_status" (e.g. "Adopted", "Transferred", None)
    4) Writes the enriched diff JSON back in-place and prints a small summary.
"""

import os
from datetime import datetime
import requests
import json
from pathlib import Path
import re
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# Use SNAPSHOT_DIR from env if provided; default to "snapshots"
SNAPSHOT_DIR = Path(os.environ.get("SNAPSHOT_DIR", "snapshots"))
SNAPSHOT_DIR.mkdir(exist_ok=True)

############################### FUNCTIONS ######################################


def getOutcomes(start_datetime, end_datetime):
    # pull outcome data from database:
    OUTCOMES_API = "https://data.austintexas.gov/resource/gsvs-ypi7.json"
    params = {  # outcomes in the selected datetime range
        "$where": f"outcome_date between '{start_datetime}' and '{end_datetime}'",
        "$limit": 5000,
    }
    response = requests.get(OUTCOMES_API, params=params)
    data = response.json()
    df = pd.DataFrame(data)  # this df contains all outcomes data

    # Desired columns to ensure are present:
    expected_columns = [
        "outcome_status",
        "type",
        "name",
        "animal_id",
        "primary_breed",
        "days_in_shelter",
        "date_of_birth",
        "outcome_date",
        "euthanasia_reason",
    ]

    # Add any missing columns as empty strings
    for col in expected_columns:
        if col not in df.columns:
            df[col] = ""

    # Unify 'adopted altered'/'adopted unaltered'/'adopted' outcomes:
    df["outcome_status"] = (
        df["outcome_status"]
        .str.lower()
        .replace(
            {
                "adopted altered": "adopted",
                "adopted unaltered": "adopted",
                "adopted offsite(altered)": "adopted offsite",
                "adopted offsite(unaltered)": "adopted offsite",
            }
        )
        .str.capitalize()
    )

    # For animals with more than one outcome in the range, keep only most recent:
    df = (
        df.sort_values(by="outcome_date", ascending=False)
        .drop_duplicates(subset="animal_id", keep="first")
    )
    return df


def format_age(row, decimals: int = 1, ref_col: str = "outcome_date"):
    """
    Compute age (in years) from date_of_birth.
    - Uses the row's outcome_date (or another ref_col) as the 'as of' date when present,
      otherwise falls back to the current time.
    - Returns a float rounded to `decimals`, or '' if we can't compute a valid age.
    """
    dob = pd.to_datetime(row.get("date_of_birth"), errors="coerce")
    ref = pd.to_datetime(row.get(ref_col), errors="coerce")

    # Fall back to "now" if no reference date in the row
    if pd.isna(ref):
        ref = pd.Timestamp.now(tz=None)

    # If no DOB or DOB is after reference date, leave blank
    if pd.isna(dob) or dob > ref:
        return ""

    years = (ref - dob).days / 365.2425  # mean tropical year
    return round(years, decimals)


def formatSpeciesDF(df):
    df = df.copy()  # prevents SettingWithCopyWarning

    # Create a readable age column
    df["age"] = df.apply(format_age, axis=1)

    # Trim down to columns of interest:
    columns = [
        "outcome_status",
        "type",
        "name",
        "animal_id",
        "primary_breed",
        "age",
        "days_in_shelter",
        "euthanasia_reason",
    ]
    df = df[columns]

    # Rename columns so html output is more readable:
    df.rename(
        columns={
            "outcome_status": "Outcome",
            "type": "Species",
            "name": "Name",
            "animal_id": "ID",
            "primary_breed": "Primary Breed",
            "age": "Age (Years)",
            "days_in_shelter": "Days in Shelter",
            "euthanasia_reason": "Euthanasia Reason",
        },
        inplace=True,
    )

    # Replace NaNs with empty strings:
    df = df.fillna("")

    # Custom outcome order: everything except "Returned", which goes last
    outcomes_present = df["Outcome"].unique().tolist()
    outcome_order = sorted(
        [o for o in outcomes_present if o != "Returned to AAC"]
    ) + ["Returned to AAC"]

    # Only apply categorical if Outcome is not empty
    if df["Outcome"].ne("").any():
        df["Outcome"] = pd.Categorical(
            df["Outcome"], categories=outcome_order, ordered=True
        )
    df = df.sort_values(by="Outcome")

    return df


SNAP_TS_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})")


def parse_snapshot_dt(path_str: str) -> datetime:
    """
    Extract a datetime from a snapshot filename such as:
      'snapshots/2025-12-03T09-17-03.json'
    Returns a datetime object.
    """
    m = SNAP_TS_PATTERN.search(path_str)
    if not m:
        raise ValueError(f"Could not parse timestamp from snapshot path: {path_str}")
    date_str, hh, mm, ss = m.groups()
    iso = f"{date_str}T{hh}:{mm}:{ss}"
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S")


def find_latest_diff(snapshots_dir: Path | None = None) -> Path | None:
    """
    Find the most recent diff_*.json in the given snapshots directory by filename.
    If snapshots_dir is None, uses SNAPSHOT_DIR.
    """
    if snapshots_dir is None:
        snapshots_dir = SNAPSHOT_DIR
    diffs = sorted(snapshots_dir.glob("diff_*.json"))
    if not diffs:
        return None
    return diffs[-1]


def load_diff(diff_path: Path) -> dict:
    with diff_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def attach_outcome_status(diff: dict, outcomes_df: pd.DataFrame) -> dict:
    """
    For each removed animal in the diff, look up outcomes with matching Animal ID
    and attach an 'outcome_status' field:

      - If no rows: outcome_status = None
      - If one unique type: that type (e.g. 'Adopted')
      - If multiple types: joined string 'Type1 / Type2'
    """
    removed = diff.get("animals_removed", [])
    if not removed:
        print("No removed animals in this diff.")
        return diff

    # Group outcomes by animal_id -> list of unique outcome_status
    grouped = (
        outcomes_df.groupby("animal_id")["outcome_status"]
        .apply(lambda s: sorted(set(s.dropna().tolist())))
        .to_dict()
    )

    counts = {
        "total_removed": len(removed),
        "with_outcome": 0,
        "without_outcome": 0,
    }

    for rec in removed:
        animal_id = str(rec.get("animal_id", "")).strip()
        types = grouped.get(animal_id, [])

        if not types:
            rec["outcome_status"] = None
            counts["without_outcome"] += 1
        else:
            if len(types) == 1:
                status = types[0]
            else:
                status = " / ".join(types)
            rec["outcome_status"] = status
            counts["with_outcome"] += 1

    diff["removal_outcome_summary_simple"] = counts
    return diff


################################################################################
################################################################################
################################################################################


def main():
    # 1) Get most recent diff file from SNAPSHOT_DIR
    diff_path = find_latest_diff()
    if diff_path is None:
        raise SystemExit(
            f"No diff_*.json files found in snapshots directory: {SNAPSHOT_DIR}"
        )
    print(f"Using diff file: {diff_path.name}")

    # 2) Load diff JSON
    diff = load_diff(diff_path)
    old_snap = diff.get("old_snapshot")
    new_snap = diff.get("new_snapshot")
    if not old_snap or not new_snap:
        raise RuntimeError(
            "Diff file is missing 'old_snapshot' or 'new_snapshot' at top level.\n"
            f"Keys present: {list(diff.keys())}"
        )

    # 3) Parse dates from snapshot filenames
    old_dt = parse_snapshot_dt(old_snap)
    new_dt = parse_snapshot_dt(new_snap)

    # Convert to strings like '2025-12-02T00:00:00' for getOutcomes:
    start_str = old_dt.strftime("%Y-%m-%dT%H:%M:%S")
    end_str = new_dt.strftime("%Y-%m-%dT%H:%M:%S")

    # 4) Get outcomes between these datetimes
    print(f"Fetching outcomes from city DB between {start_str} and {end_str}...")
    outcomes_df = getOutcomes(start_str, end_str)
    print(f"Got {len(outcomes_df)} outcome rows.")

    enriched = attach_outcome_status(diff, outcomes_df)

    # 5) Save back in-place (so everything else just reads the same diff)
    with diff_path.open("w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)

    # 6) Generate summary
    summary = enriched.get("removal_outcome_summary_simple", {})
    print("Cross-check complete.")
    print(f"- Total removed: {summary.get('total_removed', 0)}")
    print(f"- Removed with outcome: {summary.get('with_outcome', 0)}")
    print(f"- Removed without outcome: {summary.get('without_outcome', 0)}")
    print(f"Updated diff saved to: {diff_path}")


if __name__ == "__main__":
    main()