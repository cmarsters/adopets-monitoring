# email_adopets_changes.py

import json
from pathlib import Path
import argparse
import os
from dotenv import load_dotenv
import smtplib
from email.message import EmailMessage
from datetime import datetime
import re
import difflib

load_dotenv()  # loads .env when running locally; harmless in Actions

def extract_date_from_filename(path: str) -> str:
    """
    Extract YYYY-MM-DD from a filename and format it as 'Dec 2, 2025'.
    If no date found, return the original path.
    """
    match = re.search(r"\d{4}-\d{2}-\d{2}", path)
    if not match:
        return path

    date_str = match.group(0)
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%b %d, %Y")
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

def one_line(rec: dict) -> str:
    """One-line animal summary (matches the markdown style)."""
    animal_id = rec.get("animal_id", "?")
    name = rec.get("name", "?")

    species = rec.get("species") or "Dog"
    sex = rec.get("sex") or rec.get("sex_key") or "?"
    age = rec.get("age_key") or "?"
    size = rec.get("size_key") or "?"
    breed = rec.get("breed_primary_name") or "Unknown breed"

    status = rec.get("status") or rec.get("status_new") or rec.get("status_old") or "?"
    location = (
        rec.get("location")
        or rec.get("location_new")
        or rec.get("location_old")
        or "?"
    )

    return f"[{animal_id}] {name} ({species}, {sex}, {age}, {size}) [{breed}, {status}, {location}]"

def compute_bio_delta(old_desc: str | None, new_desc: str | None) -> float:
    """
    Approximate % difference between old and new bios (0–100).
    """
    old_text = " ".join((old_desc or "").split())
    new_text = " ".join((new_desc or "").split())

    if not old_text and not new_text:
        return 0.0

    sm = difflib.SequenceMatcher(None, old_text, new_text)
    ratio = sm.quick_ratio() or sm.ratio()
    return round((1 - ratio) * 100, 1)

def build_email_body(diff: dict) -> str:
    summary = diff.get("summary", {})
    animals_added = diff.get("animals_added", [])
    animals_removed = diff.get("animals_removed", [])
    animals_changed = diff.get("animals_changed", [])

    old_label = extract_date_from_filename(diff.get("old_snapshot", ""))
    new_label = extract_date_from_filename(diff.get("new_snapshot", ""))

    # --- Classify changed animals ---
    trait_changes: list[dict] = []
    bio_changes_raw: list[dict] = []
    location_changes: list[dict] = []

    for rec in animals_changed:
        added = rec.get("characteristics_added") or []
        removed = rec.get("characteristics_removed") or []
        desc_changed = bool(rec.get("description_changed"))
        has_trait_change = bool(added or removed)
        has_location_change = bool(rec.get("location_changed") or rec.get("location_change_type"))

        if has_trait_change:
            trait_changes.append(rec)
        if desc_changed:
            bio_changes_raw.append(rec)
        if has_location_change:
            location_changes.append(rec)

    # --- Split trait changes into added vs removed ---
    traits_added: list[dict] = []
    traits_removed: list[dict] = []

    for rec in trait_changes:
        added = rec.get("characteristics_added") or []
        removed = rec.get("characteristics_removed") or []
        if added:
            traits_added.append(rec)
        if removed:
            traits_removed.append(rec)

    # --- Bio buckets: removed / added / changed ---
    bios_added: list[dict] = []
    bios_removed: list[dict] = []
    bios_changed: list[dict] = []

    for rec in bio_changes_raw:
        old_desc = rec.get("description_old") or ""
        new_desc = rec.get("description_new") or ""

        old_has = bool(old_desc.strip())
        new_has = bool(new_desc.strip())

        if not old_has and new_has:
            bios_added.append(rec)
        elif old_has and not new_has:
            bios_removed.append(rec)
        elif old_has and new_has:
            delta = compute_bio_delta(old_desc, new_desc)
            rec_copy = dict(rec)
            rec_copy["bio_delta_pct"] = delta
            bios_changed.append(rec_copy)

    # --- Location buckets ---
    went_to_foster: list[dict] = []
    returned_from_foster: list[dict] = []
    kennel_moves: list[dict] = []
    other_loc: list[dict] = []

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

    lines: list[str] = []

    # --- Header & summary ---
    lines.append("Adopets profile changes for AAC")
    lines.append(f"From {old_label} to {new_label}")
    lines.append("")
    lines.append("Summary:")
    lines.append(f"- Old total: {summary.get('total_old', 0)}")
    lines.append(f"- New total: {summary.get('total_new', 0)}")
    lines.append(f"- Profiles added: {summary.get('animals_added', 0)}")
    lines.append(f"- Profiles removed: {summary.get('animals_removed', 0)}")
    lines.append(f"- Profiles changed: {summary.get('animals_changed', 0)}")
    lines.append("")
    lines.append("=" * 60)
    lines.append("")

    # --- Animals ADDED ---
    lines.append("Profiles ADDED to Adopets:")
    if animals_added:
        for rec in sorted(animals_added, key=species_sort_key):
            lines.append(f"- {one_line(rec)}")
        lines.append("")
    else:
        lines.append("  None.")
        lines.append("")

    # --- Animals REMOVED ---
    lines.append("Profiles REMOVED from Adopets:")
    if animals_removed:
        for rec in sorted(animals_removed, key=species_sort_key):
            line = f"- {one_line(rec)}"
            outcome = rec.get("outcome_status")
            if outcome:
                line += f" (Outcome: {outcome})"
            lines.append(line)
        lines.append("")
    else:
        lines.append("  None.")
        lines.append("")
    lines.append("=" * 60)
    lines.append("")

    # --- Trait changes ---
    lines.append("TRAIT CHANGES:")
    if trait_changes:
        # Traits ADDED
        lines.append("  Traits ADDED:")
        if traits_added:
            for rec in sorted(traits_added, key=species_sort_key):
                added = rec.get("characteristics_added") or []
                added_str = ", ".join(sorted(added))
                lines.append(f"  - {one_line(rec)}")
                lines.append(f"    Added traits: {added_str}")
        else:
            lines.append("  - None")
        lines.append("")

        # Traits REMOVED
        lines.append("  Traits REMOVED:")
        if traits_removed:
            for rec in sorted(traits_removed, key=species_sort_key):
                removed = rec.get("characteristics_removed") or []
                removed_str = ", ".join(sorted(removed))
                lines.append(f"  - {one_line(rec)}")
                lines.append(f"    Removed traits: {removed_str}")
        else:
            lines.append("  - None")
        lines.append("")
    else:
        lines.append("  None.")
        lines.append("")
    lines.append("=" * 60)
    lines.append("")

    # --- Bio changes ---
    lines.append("BIO CHANGES:")

    # Bios REMOVED
    lines.append("  Bios REMOVED:")
    if bios_removed:
        for rec in sorted(bios_removed, key=species_sort_key):
            lines.append(f"  - {one_line(rec)}")
        lines.append("")
    else:
        lines.append("  - None")
        lines.append("")

    # Bios ADDED
    lines.append("  Bios ADDED:")
    if bios_added:
        for rec in sorted(bios_added, key=species_sort_key):
            lines.append(f"  - {one_line(rec)}")
        lines.append("")
    else:
        lines.append("  - None")
        lines.append("")

    # Bios CHANGED
    lines.append("  Bios CHANGED:")
    if bios_changed:
        for rec in sorted(bios_changed, key=species_sort_key):
            pct = rec.get("bio_delta_pct")
            pct_str = f" (~{pct}% difference)" if pct is not None else ""
            lines.append(f"  - {one_line(rec)}{pct_str}")
        lines.append("")
    else:
        lines.append("  - None")
        lines.append("")
    lines.append("=" * 60)
    lines.append("")

    # --- Location changes ---
    lines.append("LOCATION CHANGES:")
    if location_changes:

        def add_loc_bucket(title: str, records: list[dict]):
            lines.append(f"  {title}:")
            if records:
                for r in sorted(records, key=species_sort_key):
                    old_loc = r.get("location_old") or "Unknown"
                    new_loc = r.get("location_new") or "Unknown"
                    lines.append(f"  - {one_line(r)}")
                    lines.append(f"    Location: {old_loc} \u2192 {new_loc}")
            else:
                lines.append("  - None")
            lines.append("")

        add_loc_bucket("Went to foster", went_to_foster)
        add_loc_bucket("Returned from foster", returned_from_foster)
        add_loc_bucket("Kennel changes", kennel_moves)
        add_loc_bucket("Other / uncategorized location changes", other_loc)
    else:
        lines.append("  None.")
        lines.append("")
    lines.append("=" * 60)
    lines.append("")
    lines.append(" END ")
    return "\n".join(lines)

def send_email(
    subject: str,
    body: str,
    from_addr: str,
    to_addrs: list[str],
    smtp_host: str,
    smtp_port: int,
    username: str,
    password: str,
):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(username, password)
        server.send_message(msg)


def main():
    parser = argparse.ArgumentParser(
        description="Email a human-readable summary of Adopets changes."
    )
    parser.add_argument("diff_json", type=Path, help="Path to diff JSON file")

    args = parser.parse_args()
    diff_path: Path = args.diff_json

    if not diff_path.is_file():
        raise SystemExit(f"Diff file not found: {diff_path}")

    diff = load_diff(diff_path)
    body = build_email_body(diff)

    # Email configuration (loaded from env)
    from_addr = os.environ.get("AAC_EMAIL_FROM") or os.environ.get("EMAIL_USER")
    to_env = os.environ.get("AAC_EMAIL_TO") or os.environ.get("EMAIL_TO")
    username = os.environ.get("AAC_EMAIL_USER") or from_addr
    password = os.environ.get("AAC_EMAIL_PASS") or os.environ.get("EMAIL_PASS")
    subject_prefix = os.environ.get("AAC_EMAIL_SUBJECT_PREFIX", "[AAC Adopets]")

    if not from_addr or not to_env or not password:
        raise SystemExit(
            "Missing email configuration. Please set either "
            "(AAC_EMAIL_FROM, AAC_EMAIL_TO, AAC_EMAIL_PASS) or "
            "(EMAIL_USER, EMAIL_TO, EMAIL_PASS) in your environment."
        )

    to_addrs = [addr.strip() for addr in to_env.split(",") if addr.strip()]

    old_label = extract_date_from_filename(diff.get("old_snapshot", ""))
    new_label = extract_date_from_filename(diff.get("new_snapshot", ""))
    subject = f"{subject_prefix} Changes from {old_label} to {new_label}"

    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))

    send_email(
        subject=subject,
        body=body,
        from_addr=from_addr,
        to_addrs=to_addrs,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        username=username,
        password=password,
    )

    print("Email sent.")
    print(f"Subject: {subject}")
    print(f"To: {', '.join(to_addrs)}")


if __name__ == "__main__":
    main()