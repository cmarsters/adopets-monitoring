# render_diff_report.py

import json
from pathlib import Path
import os
import argparse
from textwrap import shorten  # still imported, fine if unused
from datetime import datetime
import re
import difflib
from dotenv import load_dotenv 

load_dotenv()
SNAPSHOT_DIR = Path(os.environ.get("SNAPSHOT_DIR", "snapshots"))
SNAPSHOT_DIR.mkdir(exist_ok=True)

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


def species_sort_key(rec: dict):
    """
    Sort key that orders by species, then name, then animal_id:
      - Dogs first
      - Cats second
      - Everything else last
    """
    species_raw = (rec.get("species") or "").strip()
    species = species_raw.lower()
    if species == "dog":
        grp = 0
    elif species == "cat":
        grp = 1
    else:
        grp = 2
    name = rec.get("name") or ""
    animal_id = rec.get("animal_id") or ""
    return (grp, name.lower(), str(animal_id))


def format_animal_line(rec: dict) -> str:
    """One-line summary for an animal."""
    animal_id = rec.get("animal_id", "?")
    name = rec.get("name", "?")

    species = rec.get("species") or "?"
    species = species.strip().title()

    sex = rec.get("sex") or rec.get("sex_key") or "?"
    sex_map = {
        "MALE": "M",
        "FEMALE": "F",
    }
    sex = sex_map.get(sex or "?")
    
    age = rec.get("age_key") or "?"
    age = age.strip().title()
    
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
    return f"[{animal_id}] {name} ({species}, {sex}, {age}, {size})"


def summarize_bio_change(old_desc: str | None, new_desc: str | None, context: int = 120):
    """
    Return (delta_pct, old_snip, new_snip) where:
      - delta_pct is an approximate % difference between old and new bios
      - old_snip/new_snip are short snippets around the first changed region

    This keeps the report compact but still shows the meaningful change,
    even if it's not at the beginning of the bio.
    """
    old_text = " ".join((old_desc or "").split())
    new_text = " ".join((new_desc or "").split())

    if not old_text and not new_text:
        return 0.0, "", ""

    sm = difflib.SequenceMatcher(None, old_text, new_text)
    # quick_ratio is fast; ratio is more exact if needed
    ratio = sm.quick_ratio() or sm.ratio()
    delta_pct = round((1 - ratio) * 100, 1)

    old_snip = ""
    new_snip = ""

    # Find the first non-equal block and grab some context around it
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue

        start_old = max(i1 - context // 2, 0)
        end_old = min(i2 + context // 2, len(old_text))
        start_new = max(j1 - context // 2, 0)
        end_new = min(j2 + context // 2, len(new_text))

        old_snip = old_text[start_old:end_old]
        new_snip = new_text[start_new:end_new]

        if start_old > 0:
            old_snip = "..." + old_snip
        if end_old < len(old_text):
            old_snip += "..."
        if start_new > 0:
            new_snip = "..." + new_snip
        if end_new < len(new_text):
            new_snip += "..."
        break  # just first changed region

    return delta_pct, old_snip, new_snip


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
    lines.append("-------------------------------------------------------------")
    lines.append("## Summary")
    lines.append(f"- Old total: {summary.get('total_old', 0)}")
    lines.append(f"- New total: {summary.get('total_new', 0)}")
    lines.append(f"- Profiles added: {summary.get('animals_added', 0)}")
    lines.append(f"- Profiles removed: {summary.get('animals_removed', 0)}")
    lines.append(f"- Profiles changed: {summary.get('animals_changed', 0)}")
    lines.append("-------------------------------------------------------------")    
    lines.append("")

    # Added
    lines.append("-------------------------------------------------------------")
    lines.append("## Profiles ADDED to Adopets")
    if animals_added:
        for rec in sorted(animals_added, key=species_sort_key):
            lines.append(format_animal_line(rec))
    else:
        lines.append("- None")
    lines.append("")

    # Removed
    lines.append("## Profiles REMOVED from Adopets")
    if animals_removed:
        for rec in sorted(animals_removed, key=species_sort_key):
            lines.append(format_animal_line(rec))
    else:
        lines.append("- None")
    lines.append("-------------------------------------------------------------")
    lines.append("")

    # Split changed animals into:
    #  - trait changes
    #  - bio changes
    #  - location changes
    trait_changes = []
    bio_changes = []
    location_changes = []

    for rec in animals_changed:
        has_trait_change = bool(
            rec.get("characteristics_added") or rec.get("characteristics_removed")
        )
        has_bio_change = bool(rec.get("description_changed"))
        has_location_change = bool(rec.get("location_changed") or rec.get("location_change_type"))

        if has_trait_change:
            trait_changes.append(rec)
        if has_bio_change:
            bio_changes.append(rec)
        if has_location_change:
            location_changes.append(rec)

    # Trait changes
    lines.append("-------------------------------------------------------------")
    if trait_changes:
        # Split into two views: traits added vs traits removed
        traits_added = []
        traits_removed = []

        for rec in trait_changes:
            added = rec.get("characteristics_added") or []
            removed = rec.get("characteristics_removed") or []

            if added:
                traits_added.append(rec)
            if removed:
                traits_removed.append(rec)

        # --- Traits ADDED ---
        lines.append("## Traits ADDED to Adopets")
        if traits_added:
            for rec in sorted(traits_added, key=species_sort_key):
                lines.append(format_animal_line(rec))
                added = rec.get("characteristics_added") or []
                added_str = ", ".join(added)
                lines.append(f"  - **Added traits:** {added_str}")
            lines.append("")
        else:
            lines.append("- None")
            lines.append("")
            
        # --- Traits REMOVED ---
        lines.append("## Traits REMOVED from Adopets")
        if traits_removed:
            for rec in sorted(traits_removed, key=species_sort_key):
                lines.append(format_animal_line(rec))
                removed = rec.get("characteristics_removed") or []
                removed_str = ", ".join(removed)
                lines.append(f"  - **Removed traits:** {removed_str}")
            # lines.append("")
        else:
            lines.append("- None")
            lines.append("")
        

    else:
        lines.append("- None")
    lines.append("-------------------------------------------------------------")
    lines.append("")
    
    # Bio changes → now split into three buckets
    lines.append("-------------------------------------------------------------")

    bios_added: list[dict] = []
    bios_removed: list[dict] = []
    bios_changed: list[dict] = []

    for rec in bio_changes:
        old_desc = rec.get("description_old") or ""
        new_desc = rec.get("description_new") or ""

        old_has = bool(old_desc.strip())
        new_has = bool(new_desc.strip())

        if not old_has and new_has:
            # New bio added
            bios_added.append(rec)
        elif old_has and not new_has:
            # Bio removed
            bios_removed.append(rec)
        elif old_has and new_has:
            # Bio edited (both non-empty)
            delta_pct, _, _ = summarize_bio_change(old_desc, new_desc)
            # Make a shallow copy so we don't mutate the original diff structure
            rec_copy = dict(rec)
            rec_copy["bio_delta_pct"] = delta_pct
            bios_changed.append(rec_copy)
        # If both empty, technically something flipped around, but we already
        # know description_changed=True so you probably won't see this case much.

    # Bios added
    lines.append("## Bios ADDED to Adopets")
    if bios_added:
        for rec in sorted(bios_added, key=species_sort_key):
            lines.append(format_animal_line(rec))
        lines.append("")
    else:
        lines.append("- None")
        lines.append("")
        
    # Bios removed
    lines.append("## Bios REMOVED from Adopets")
    if bios_removed:
        for rec in sorted(bios_removed, key=species_sort_key):
            lines.append(format_animal_line(rec))
        lines.append("")
    else:
        lines.append("- None")
        lines.append("")

    # Bios changed (edited / rewritten)
    lines.append("## Bios CHANGED in Adopets")
    if bios_changed:
        for rec in sorted(bios_changed, key=species_sort_key):
            pct = rec.get("bio_delta_pct")
            pct_str = f" (Bio changed by ~{pct}%)" if pct is not None else ""
            lines.append(format_animal_line(rec) + pct_str)
        lines.append("")
    else:
        lines.append("- None")
        lines.append("-------------------------------------------------------------")
        lines.append("")

    # Location changes
    lines.append("-------------------------------------------------------------")
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
            lines.append(f"## {title}")
            if records:
                for r in sorted(records, key=species_sort_key):
                    lines.append(format_animal_line(r))
                    old_loc = r.get("location_old") or "Unknown"
                    new_loc = r.get("location_new") or "Unknown"
                    lines.append(f"  - **Location:** {old_loc} → {new_loc}")
                lines.append("")
            else:
                lines.append("- None")
                lines.append("")

        lines.append("-------------------------------------------------------------")
        add_location_section("LOCATION CHANGE: Went to Foster", went_to_foster)
        add_location_section("LOCATION CHANGE: Returned from Foster", returned_from_foster)
        add_location_section("LOCATION CHANGE: Moved Kennels", kennel_moves)
        add_location_section("LOCATION CHANGE: Other", other_loc)
        lines.append("-------------------------------------------------------------")
        lines.append("")

    else:
        lines.append("- None")
        lines.append("-------------------------------------------------------------")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Render a human-readable report from an Adopets diff JSON."
    )
    parser.add_argument(
        "diff_json",
        type=Path,
        help=(
            "Path to diff JSON file. If a relative name is given and not found, "
            "the script will also look inside SNAPSHOT_DIR "
            f"({SNAPSHOT_DIR})."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output Markdown file (default: same folder, .md)",
    )

    args = parser.parse_args()
    diff_path: Path = args.diff_json

    # If the given path isn't a file, and it's relative, try SNAPSHOT_DIR / name
    if not diff_path.is_file():
        if not diff_path.is_absolute():
            candidate = SNAPSHOT_DIR / diff_path.name
            if candidate.is_file():
                diff_path = candidate

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