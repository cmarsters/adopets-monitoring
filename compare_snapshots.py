# compare_snapshots.py

import json
from pathlib import Path
from datetime import datetime
import argparse


def load_snapshot(path: Path):
    """Load a snapshot JSON file and return a dict mapping id -> record."""
    with path.open("r", encoding="utf-8") as f:
        records = json.load(f)

    by_id = {}
    for rec in records:
        pet_id = rec.get("animal_id")
        if not pet_id:
            continue
        by_id[pet_id] = rec
    return by_id


def normalize_html(text: str | None) -> str:
    """Normalize description text for comparison."""
    if text is None:
        return ""
    # Strip leading/trailing whitespace and collapse internal whitespace a bit
    return " ".join(text.split())


def compare_snapshots(old_path: Path, new_path: Path) -> dict:
    old_data = load_snapshot(old_path)
    new_data = load_snapshot(new_path)

    old_ids = set(old_data.keys())
    new_ids = set(new_data.keys())

    added_ids = sorted(new_ids - old_ids)
    removed_ids = sorted(old_ids - new_ids)
    common_ids = sorted(old_ids & new_ids)

    animals_added = []
    animals_removed = []
    animals_changed = []

    # Added animals: use new snapshot info
    for pid in added_ids:
        rec = new_data[pid]
        animals_added.append(
            {
                "uuid": rec.get("uuid"),
                "animal_id": rec.get("animal_id"),
                "name": rec.get("name"),
                "species": rec.get("species"),
                "sex": rec.get("sex"),
                "age_key": rec.get("age_key"),
                "size_key": rec.get("size_key"),
                "breed_primary_name": rec.get("breed_primary_name"),
                "status": rec.get("status"),
                "location": rec.get("location")
            }
        )

    # Removed animals: use old snapshot info
    for pid in removed_ids:
        rec = old_data[pid]
        animals_removed.append(
            {
                "uuid": rec.get("uuid"),
                "animal_id": rec.get("animal_id"),
                "name": rec.get("name"),
                "species": rec.get("species"),
                "sex": rec.get("sex"),
                "age_key": rec.get("age_key"),
                "size_key": rec.get("size_key"),
                "breed_primary_name": rec.get("breed_primary_name"),
                "status": rec.get("status"),
                "location": rec.get("location")
            }
        )

    # Changed animals: compare traits + bios + location
    for pid in common_ids:
        old = old_data[pid]
        new = new_data[pid]

        # 1) Characteristics as sets of keys
        old_keys = set(old.get("characteristic_keys") or [])
        new_keys = set(new.get("characteristic_keys") or [])

        char_added = sorted(new_keys - old_keys)
        char_removed = sorted(old_keys - new_keys)

        # 2) Descriptions (normalized)
        old_desc = old.get("description_html")
        new_desc = new.get("description_html")

        old_norm = normalize_html(old_desc)
        new_norm = normalize_html(new_desc)

        description_changed = old_norm != new_norm

        # 3) Location / foster changes
        old_loc = old.get("location")
        new_loc = new.get("location")
        old_foster = bool(old.get("foster"))
        new_foster = bool(new.get("foster"))

        location_changed = (old_loc != new_loc) or (old_foster != new_foster)

        location_change_type = None
        if location_changed:
            if (not old_foster) and new_foster:
                location_change_type = "went_to_foster"
            elif old_foster and (not new_foster):
                location_change_type = "returned_from_foster"
            elif old_loc != new_loc:
                location_change_type = "kennel_move"
            else:
                location_change_type = "other"


        # 4) If *anything* changed, record it
        if char_added or char_removed or description_changed or location_changed:
            animals_changed.append(
                {
                    "uuid": pid,  # keep this as-is for backward compatibility
                    "animal_id": new.get("animal_id", old.get("animal_id")),
                    "name": new.get("name", old.get("name")),

                    # basic info for nicer reports / emails
                    "species": new.get("species", old.get("species")),
                    "sex": new.get("sex", old.get("sex")),
                    "age_key": new.get("age_key", old.get("age_key")),
                    "size_key": new.get("size_key", old.get("size_key")),
                    "breed_primary_name": new.get("breed_primary_name", old.get("breed_primary_name")),

                    # status
                    "status_old": old.get("status"),
                    "status_new": new.get("status"),
                    
                    # location / foster changes
                    "location_old": old_loc,
                    "location_new": new_loc,
                    "foster_old": old_foster,
                    "foster_new": new_foster,
                    "location_changed": location_changed,
                    "location_change_type": location_change_type,

                    # traits
                    "characteristics_added": char_added,
                    "characteristics_removed": char_removed,

                    # bios
                    "description_changed": description_changed,
                    "description_old": old_desc,
                    "description_new": new_desc,
                }
            )


    diff = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "old_snapshot": str(old_path),
        "new_snapshot": str(new_path),
        "summary": {
            "total_old": len(old_ids),
            "total_new": len(new_ids),
            "animals_added": len(animals_added),
            "animals_removed": len(animals_removed),
            "animals_changed": len(animals_changed),
        },
        "animals_added": animals_added,
        "animals_removed": animals_removed,
        "animals_changed": animals_changed,
    }

    return diff


def main():
    parser = argparse.ArgumentParser(
        description="Compare two Adopets snapshot JSON files."
    )
    parser.add_argument("old_snapshot", type=Path, help="Path to older snapshot JSON")
    parser.add_argument("new_snapshot", type=Path, help="Path to newer snapshot JSON")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output diff JSON file (default: diff_old_to_new.json)",
    )

    args = parser.parse_args()

    old_path: Path = args.old_snapshot
    new_path: Path = args.new_snapshot

    if not old_path.is_file():
        raise SystemExit(f"Old snapshot not found: {old_path}")
    if not new_path.is_file():
        raise SystemExit(f"New snapshot not found: {new_path}")

    diff = compare_snapshots(old_path, new_path)

    # Decide on output path
    if args.output is not None:
        out_path = args.output
    else:
        out_name = f"diff_{old_path.stem}_to_{new_path.stem}.json"
        out_path = old_path.parent / out_name

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(diff, f, indent=2, ensure_ascii=False)

    summary = diff["summary"]
    print("Comparison complete.")
    print(f"Old snapshot: {diff['old_snapshot']}")
    print(f"New snapshot: {diff['new_snapshot']}")
    print(
        f"Old total: {summary['total_old']} | "
        f"New total: {summary['total_new']}"
    )
    print(
        f"Added: {summary['animals_added']} | "
        f"Removed: {summary['animals_removed']} | "
        f"Changed: {summary['animals_changed']}"
    )
    print(f"Diff saved to: {out_path}")


if __name__ == "__main__":
    main()