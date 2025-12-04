# render_diff_report.py

import json
from pathlib import Path
import argparse
from textwrap import shorten
from datetime import datetime
import re
import difflib

def extract_date_from_filename(path: str) -> str:
    """
    Extract YYYY-MM-DD from a filename and format it as 'Dec 2, 2025'.
    If no date found, return the original filename.
    """
    match = re.search(r"\d{4}-\d{2}-\d{2}", path)
    if not match:
        return path

    date_str = match.group(0)
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%b %d, %Y")  # Example: Dec 02, 2025
    except ValueError:
        return date_str

def load_diff(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def format_animal_line(rec: dict) -> str:
    """One-line summary for an animal."""
    animal_id = rec.get("animal_id", "?")
    name = rec.get("name", "?")

    species = rec.get("species") or "Dog"
    sex = rec.get("sex") or rec.get("sex_key") or "?"
    age = rec.get("age_key") or "?"
    size = rec.get("size_key") or "?"
    breed = rec.get("breed_primary_name") or "Unknown breed"
    
    status = rec.get("status") or rec.get("status_new") or rec.get("status_old") or "?"
    # For added/removed records we have 'location';
    # for changed records we may only have location_old/location_new.
    location = (
        rec.get("location")
        or rec.get("location_new")
        or rec.get("location_old")
        or "?"
    )

    return (
        f"- [{animal_id}] {name} "
        f"({species}, {sex}, {age}, {size}) "
        f"[{breed}, {status}, {location}]"
    )

def make_markdown_report(diff: dict) -> str:
    summary = diff.get("summary", {})
    animals_added = diff.get("animals_added", [])
    animals_removed = diff.get("animals_removed", [])
    animals_changed = diff.get("animals_changed", [])

    lines: list[str] = []

    # Header
    lines.append("# Adopets Change Report")
    old_label = extract_date_from_filename(diff.get("old_snapshot", ""))
    new_label = extract_date_from_filename(diff.get("new_snapshot", ""))

    lines.append(f"From **{old_label}** to **{new_label}**")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append(f"- Old total: {summary.get('total_old', 0)}")
    lines.append(f"- New total: {summary.get('total_new', 0)}")
    lines.append(f"- Profiles added: {summary.get('animals_added', 0)}")
    lines.append(f"- Profiles removed: {summary.get('animals_removed', 0)}")
    lines.append(f"- Profiles changed: {summary.get('animals_changed', 0)}")
    lines.append("")

    # Added
    lines.append("## Animals ADDED to Adopets")
    if animals_added:
        for rec in animals_added:
            lines.append(format_animal_line(rec))
    else:
        lines.append("- None")
    lines.append("")

    # Removed
    lines.append("## Animals REMOVED from Adopets")
    if animals_removed:
        for rec in animals_removed:
            lines.append(format_animal_line(rec))
    else:
        lines.append("- None")
    lines.append("")

    # Split changed animals into:
    #  - trait changes
    #  - bio changes
    #  - location changes
    trait_changes = []
    bio_changes = []
    location_changes = []

    for rec in animals_changed:
        has_trait_change = bool(rec.get("characteristics_added") or rec.get("characteristics_removed"))
        has_bio_change = bool(rec.get("description_changed"))
        has_location_change = bool(rec.get("location_changed") or rec.get("location_change_type"))

        if has_trait_change:
            trait_changes.append(rec)
        if has_bio_change:
            bio_changes.append(rec)
        if has_location_change:
            location_changes.append(rec)

    # Trait changes
    lines.append("## Trait Changes")
    if trait_changes:
        for rec in trait_changes:
            animal_id = rec.get("animal_id", "?")
            name = rec.get("name", "?")
            species = rec.get("species", "")
            lines.append(f"### [{animal_id}] {name}, ({species})")

            added = rec.get("characteristics_added") or []
            removed = rec.get("characteristics_removed") or []

            if added:
                added_str = ", ".join(added)
                lines.append(f"- **Added:** {added_str}")
            if removed:
                removed_str = ", ".join(removed)
                lines.append(f"- **Removed:** {removed_str}")
            if not added and not removed:
                lines.append("- (No net trait change?)")

            lines.append("")  # blank line after each animal
    else:
        lines.append("- None")
        lines.append("")

    # Location changes
    lines.append("## Location Changes")
    if location_changes:
        # Bucket by type for easier reading
        went_to_foster = []
        returned_from_foster = []
        kennel_moves = []
        other_loc = []

        for rec in location_changes:
            change_type = rec.get("location_change_type")
            if change_type == "went_to_foster":
                went_to_foster.append(rec)
            elif change_type == "returned_from_foster":
                returned_from_foster.append(rec)
            elif change_type == "kennel_move":
                kennel_moves.append(rec)
            else:
                other_loc.append(rec)

        def add_location_section(title: str, records: list[dict]):
            lines.append(f"### {title}")
            if records:
                for r in records:
                    lines.append(format_animal_line(r))
                    old_loc = r.get("location_old") or "Unknown"
                    new_loc = r.get("location_new") or "Unknown"
                    lines.append(f"  - **Location:** {old_loc} → {new_loc}")
                lines.append("")
            else:
                lines.append("- None")
                lines.append("")

        add_location_section("Went to foster", went_to_foster)
        add_location_section("Returned from foster", returned_from_foster)
        add_location_section("Kennel moves", kennel_moves)
        add_location_section("Other / uncategorized location changes", other_loc)

    else:
        lines.append("- None")
        lines.append("")
    
    # Bio changes
    lines.append("## Bio Changes")
    if bio_changes:
        for rec in bio_changes:
            animal_id = rec.get("animal_id", "?")
            name = rec.get("name", "?")
            species = rec.get("species", "")
            lines.append(f"### [{animal_id}] {name}, ({species})")

            old_desc = rec.get("description_old") or ""
            new_desc = rec.get("description_new") or ""

            # Shorten to something readable; adjust width as you like
            old_snip = shorten(" ".join(old_desc.split()), width=300, placeholder="...")
            new_snip = shorten(" ".join(new_desc.split()), width=300, placeholder="...")

            lines.append("- **Bio changed:** yes")
            if old_snip:
                lines.append("  - Old (first 300 chars):")
                lines.append(f"    > {old_snip}")
            if new_snip:
                lines.append("  - New (first 300 chars):")
                lines.append(f"    > {new_snip}")
            lines.append("")
    else:
        lines.append("- None")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Render a human-readable report from an Adopets diff JSON."
    )
    parser.add_argument("diff_json", type=Path, help="Path to diff JSON file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output Markdown file (default: same folder, .md)",
    )

    args = parser.parse_args()
    diff_path: Path = args.diff_json

    if not diff_path.is_file():
        raise SystemExit(f"Diff file not found: {diff_path}")

    diff = load_diff(diff_path)
    report_md = make_markdown_report(diff)

    # Decide output path
    if args.output is not None:
        out_path = args.output
    else:
        out_name = diff_path.stem + ".md"  # e.g., diff_2025-12-02_to_2025-12-03.md
        out_path = diff_path.with_name(out_name)

    with out_path.open("w", encoding="utf-8") as f:
        f.write(report_md)

    # Print a tiny summary to terminal
    summary = diff.get("summary", {})
    print("Report generated.")
    print(f"- Diff file: {diff_path}")
    print(f"- Old total: {summary.get('total_old', 0)}")
    print(f"- New total: {summary.get('total_new', 0)}")
    print(f"- Added: {summary.get('animals_added', 0)}")
    print(f"- Removed: {summary.get('animals_removed', 0)}")
    print(f"- Changed: {summary.get('animals_changed', 0)}")
    print(f"- Markdown report: {out_path}")


if __name__ == "__main__":
    main()