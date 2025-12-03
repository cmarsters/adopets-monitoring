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
        pet_id = rec.get("id")
        if not pet_id:
            continue
        by_id[pet_id] = rec
    return by_id


def normalize_html(text: str | None) -> str:
    """Normalize description text for comparison (very simple for now)."""
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
                "id": rec.get("id"),
                "code": rec.get("code"),
                "name": rec.get("name"),
                "status": rec.get("status"),
                "sex": rec.get("sex"),
                "age_key": rec.get("age_key"),
                "size_key": rec.get("size_key"),
            }
        )

    # Removed animals: use old snapshot info
    for pid in removed_ids:
        rec = old_data[pid]
        animals_removed.append(
            {
                "id": rec.get("id"),
                "code": rec.get("code"),
                "name": rec.get("name"),
                "status": rec.get("status"),
                "sex": rec.get("sex"),
                "age_key": rec.get("age_key"),
                "size_key": rec.get("size_key"),
            }
        )

    # Changed animals: compare traits + bios
    for pid in common_ids:
        old = old_data[pid]
        new = new_data[pid]

        # Compare characteristics as sets of keys
        old_keys = set(old.get("characteristic_keys") or [])
        new_keys = set(new.get("characteristic_keys") or [])

        char_added = sorted(new_keys - old_keys)
        char_removed = sorted(old_keys - new_keys)

        # Compare descriptions (normalized)
        old_desc = old.get("description_html")
        new_desc = new.get("description_html")

        old_norm = normalize_html(old_desc)
        new_norm = normalize_html(new_desc)

        description_changed = old_norm != new_norm

        if char_added or char_removed or description_changed:
            animals_changed.append(
                {
                    "uuid": pid,
                    "animal_id": new.get("code", old.get("code")),
                    "name": new.get("name", old.get("name")),
                    "status_old": old.get("status"),
                    "status_new": new.get("status"),
                    "characteristics_added": char_added,
                    "characteristics_removed": char_removed,
                    "description_changed": description_changed,
                    # include full bios so you can diff them later if you want
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