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
from html import escape

load_dotenv()  # loads .env when running locally; harmless in Actions

SNAPSHOT_DIR = Path(os.environ.get("SNAPSHOT_DIR", "snapshots"))
SNAPSHOT_DIR.mkdir(exist_ok=True)

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
    """One-line animal summary."""
    animal_id = rec.get("animal_id", "?")
    name = rec.get("name", "?")

    # Species
    raw_species = rec.get("species") or "?"
    species = raw_species.strip().title()

    # Sex: map MALE/FEMALE → M/F, otherwise title-case
    sex_raw = (rec.get("sex") or rec.get("sex_key") or "?").strip()
    sex_norm = sex_raw.lower()
    sex_map = {
        "male": "M",
        "female": "F",
    }
    sex = sex_map.get(sex_norm, sex_raw.title() if sex_raw else "?")

    # Age & size
    age = (rec.get("age_key") or "?").strip().title()
    size = (rec.get("size_key") or "?").strip()
    
    bold_label = f"**[{animal_id}] {name}**"
    return f"{bold_label} ({species}, {sex}, {age}, {size})"

def compute_bio_delta(old_desc: str | None, new_desc: str | None) -> float:
    """
    Approximate % difference between old and new bios (0–100),
    but treat extremely small diffs (<0.1%) as zero.
    """
    old_text = " ".join((old_desc or "").split())
    new_text = " ".join((new_desc or "").split())

    if not old_text and not new_text:
        return 0.0

    sm = difflib.SequenceMatcher(None, old_text, new_text)
    ratio = sm.quick_ratio() or sm.ratio()
    delta = (1 - ratio) * 100

    # Ignore tiny differences caused by whitespace or encoding quirks
    if delta < 0.1:
        return 0.0

    return round(delta, 1)

def classify_changes(diff: dict):
    """Common classification of changes used by both text and HTML builders."""
    summary = diff.get("summary", {})
    animals_added = diff.get("animals_added", [])
    animals_removed = diff.get("animals_removed", [])
    animals_changed = diff.get("animals_changed", [])

    # --- Classify changed animals ---
    trait_changes: list[dict] = []
    bio_changes_raw: list[dict] = []
    location_changes: list[dict] = []

    for rec in animals_changed:
        added = rec.get("characteristics_added") or []
        removed = rec.get("characteristics_removed") or []
        desc_changed = bool(rec.get("description_changed"))
        has_trait_change = bool(added or removed)
        has_location_change = bool(
            rec.get("location_changed") or rec.get("location_change_type")
        )

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
            if delta == 0.0:
                continue
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

    return {
        "summary": summary,
        "animals_added": animals_added,
        "animals_removed": animals_removed,
        "animals_changed": animals_changed,
        "traits_added": traits_added,
        "traits_removed": traits_removed,
        "bios_added": bios_added,
        "bios_removed": bios_removed,
        "bios_changed": bios_changed,
        "location_changes": location_changes,
        "went_to_foster": went_to_foster,
        "returned_from_foster": returned_from_foster,
        "kennel_moves": kennel_moves,
        "other_loc": other_loc,
    }

def build_email_body(diff: dict) -> str:
    classified = classify_changes(diff)
    summary = classified["summary"]
    animals_added = classified["animals_added"]
    animals_removed = classified["animals_removed"]
    traits_added = classified["traits_added"]
    traits_removed = classified["traits_removed"]
    bios_added = classified["bios_added"]
    bios_removed = classified["bios_removed"]
    bios_changed = classified["bios_changed"]
    location_changes = classified["location_changes"]
    went_to_foster = classified["went_to_foster"]
    returned_from_foster = classified["returned_from_foster"]
    kennel_moves = classified["kennel_moves"]
    other_loc = classified["other_loc"]

    old_label = extract_date_from_filename(diff.get("old_snapshot", ""))
    new_label = extract_date_from_filename(diff.get("new_snapshot", ""))

    lines: list[str] = []

    # --- Header & summary ---
    lines.append("AAC Adopets Profile Changes")
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
        lines.append("- None")
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
        lines.append("- None")
        lines.append("")
    lines.append("=" * 60)
    lines.append("")

    # --- Trait changes ---
    lines.append("Trait Changes:")
    if traits_added or traits_removed:
        # Traits added
        lines.append("  Traits ADDED:")
        if traits_added:
            for rec in sorted(traits_added, key=species_sort_key):
                added = rec.get("characteristics_added") or []
                added_str = ", ".join(sorted(added))
                lines.append(f"  - {one_line(rec)}")
                lines.append(f"    Added: {added_str}")
        else:
            lines.append("  - None")
        lines.append("")

        # Traits removed
        lines.append("  Traits REMOVED:")
        if traits_removed:
            for rec in sorted(traits_removed, key=species_sort_key):
                removed = rec.get("characteristics_removed") or []
                removed_str = ", ".join(sorted(removed))
                lines.append(f"  - {one_line(rec)}")
                lines.append(f"    Removed: {removed_str}")
        else:
            lines.append("  - None")
        lines.append("")
    else:
        lines.append("- None")
        lines.append("")
    lines.append("=" * 60)
    lines.append("")

    # --- Bio changes ---
    lines.append("Bio Changes:")

    # Bios removed
    lines.append("  Bios REMOVED:")
    if bios_removed:
        for rec in sorted(bios_removed, key=species_sort_key):
            lines.append(f"  - {one_line(rec)}")
        lines.append("")
    else:
        lines.append("  - None")
        lines.append("")

    # Bios added
    lines.append("  Bios ADDED:")
    if bios_added:
        for rec in sorted(bios_added, key=species_sort_key):
            lines.append(f"  - {one_line(rec)}")
        lines.append("")
    else:
        lines.append("  - None")
        lines.append("")

    # Bios changed
    lines.append("  Bios CHANGED:")
    if bios_changed:
        for rec in sorted(bios_changed, key=species_sort_key):
            pct = rec.get("bio_delta_pct")
            pct_str = f" ~{pct}%" if pct is not None else ""
            lines.append(f"  - {one_line(rec)}{pct_str}")
        lines.append("")
    else:
        lines.append("  - None")
        lines.append("")
    lines.append("=" * 60)
    lines.append("")

    # --- Location changes ---
    lines.append("Location Changes:")
    if location_changes:

        def add_loc_bucket(title: str, records: list[dict]):
            lines.append(f"  {title}:")
            if records:
                for r in sorted(records, key=species_sort_key):
                    old_loc = r.get("location_old") or "Unknown"
                    new_loc = r.get("location_new") or "Unknown"
                    lines.append(
                        f"  - {one_line(r)} -- Location: {old_loc} \u2192 {new_loc}"
                    )
            else:
                lines.append("  - None")
            lines.append("")

        add_loc_bucket("Went to foster", went_to_foster)
        add_loc_bucket("Returned from foster", returned_from_foster)
        add_loc_bucket("Kennel changes", kennel_moves)
        add_loc_bucket("Other / uncategorized location changes", other_loc)

    else:
        lines.append("  - None")
        lines.append("")
    lines.append("=" * 60)
    lines.append("")
    lines.append("END")

    return "\n".join(lines)

def build_html_body(diff: dict) -> str:
    classified = classify_changes(diff)
    summary = classified["summary"]
    animals_added = classified["animals_added"]
    animals_removed = classified["animals_removed"]
    traits_added = classified["traits_added"]
    traits_removed = classified["traits_removed"]
    bios_added = classified["bios_added"]
    bios_removed = classified["bios_removed"]
    bios_changed = classified["bios_changed"]
    location_changes = classified["location_changes"]
    went_to_foster = classified["went_to_foster"]
    returned_from_foster = classified["returned_from_foster"]
    kennel_moves = classified["kennel_moves"]
    other_loc = classified["other_loc"]

    old_label = extract_date_from_filename(diff.get("old_snapshot", ""))
    new_label = extract_date_from_filename(diff.get("new_snapshot", ""))

    def html_line(rec: dict) -> str:
        """HTML-safe version of one_line with slight emphasis."""
        animal_id = rec.get("animal_id", "?")
        name = rec.get("name", "?")
        species = (rec.get("species") or "?").strip().title()
        sex_raw = (rec.get("sex") or rec.get("sex_key") or "?").strip()
        sex_norm = sex_raw.lower()
        sex_map = {"male": "M", "female": "F"}
        sex = sex_map.get(sex_norm, sex_raw.title() if sex_raw else "?")
        age = (rec.get("age_key") or "?").strip().title()
        size = (rec.get("size_key") or "?").strip()

        return (
            f"<strong>[{escape(str(animal_id))}] {escape(name)}</strong> "
            f"({escape(species)}, {escape(sex)}, {escape(age)}, {escape(size)})"
        )


    def html_section_title(text: str) -> str:
        return f"<h2>{escape(text)}</h2>"

    lines: list[str] = []

    lines.append("<html>")
    lines.append("<head>")
    lines.append(
        "<meta charset='utf-8'>"
        "<style>"
        "body { font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; color: #111827; }"
        "h1 { font-size: 20px; color: #111827; margin-bottom: 4px; }"
        "h2 { font-size: 16px; color: #1d4ed8; margin-top: 18px; margin-bottom: 6px; }"
        "h3 { font-size: 14px; color: #111827; margin-top: 10px; margin-bottom: 4px; }"
        "ul { margin-top: 4px; margin-bottom: 8px; padding-left: 18px; }"
        "li { margin-bottom: 2px; }"
        ".summary { background: #f9fafb; border-radius: 8px; padding: 8px 10px; border: 1px solid #e5e7eb; }"
        ".tagline { font-size: 12px; color: #6b7280; margin-bottom: 12px; }"
        ".section-divider { margin: 16px 0; border-top: 1px solid #e5e7eb; }"
        ".trait-added { color: #15803d; }"
        ".trait-removed { color: #b91c1c; }"
        ".bio-changed { color: #7c2d12; }"
        ".location { color: #4b5563; font-size: 12px; }"
        "</style>"
    )
    lines.append("</head>")
    lines.append("<body>")

    # Header
    lines.append("<h1>AAC Adopets Profile Changes</h1>")
    lines.append(
        f"<div class='tagline'>From <strong>{escape(old_label)}</strong> "
        f"to <strong>{escape(new_label)}</strong></div>"
    )

    # Summary
    lines.append("<div class='summary'>")
    lines.append("<strong>Summary</strong>")
    lines.append("<ul>")
    lines.append(f"<li>Old total: {summary.get('total_old', 0)}</li>")
    lines.append(f"<li>New total: {summary.get('total_new', 0)}</li>")
    lines.append(f"<li>Profiles ADDED: {summary.get('animals_added', 0)}</li>")
    lines.append(f"<li>Profiles REMOVED: {summary.get('animals_removed', 0)}</li>")
    lines.append(f"<li>Profiles CHANGED: {summary.get('animals_changed', 0)}</li>")
    lines.append("</ul>")
    lines.append("</div>")

    lines.append("<div class='section-divider'></div>")

    # Profiles added
    lines.append(html_section_title("Profiles ADDED to Adopets"))
    if animals_added:
        lines.append("<ul>")
        for rec in sorted(animals_added, key=species_sort_key):
            lines.append(f"<li>{html_line(rec)}</li>")
        lines.append("</ul>")
    else:
        lines.append("<ul><li>None</li></ul>")

    # Profiles removed
    lines.append(html_section_title("Profiles REMOVED from Adopets"))
    if animals_removed:
        lines.append("<ul>")
        for rec in sorted(animals_removed, key=species_sort_key):
            outcome = rec.get("outcome_status")
            extra = f" <span style='color:#6b7280'>(Outcome: {escape(outcome)})</span>" if outcome else ""
            lines.append(f"<li>{html_line(rec)}{extra}</li>")
        lines.append("</ul>")
    else:
        lines.append("<ul><li>None</li></ul>")

    lines.append("<div class='section-divider'></div>")

    # Trait changes
    lines.append(html_section_title("Trait Changes"))

    # Traits added
    lines.append("<h3>Traits ADDED</h3>")
    if traits_added:
        lines.append("<ul>")
        for rec in sorted(traits_added, key=species_sort_key):
            added = rec.get("characteristics_added") or []
            added_str = ", ".join(sorted(added))
            lines.append(
                f"<li>{html_line(rec)}"
                f"<div class='trait-added'>Added: {escape(added_str)}</div>"
                f"</li>"
            )
        lines.append("</ul>")
    else:
        lines.append("<ul><li>None</li></ul>")

    # Traits removed
    lines.append("<h3>Traits REMOVED</h3>")
    if traits_removed:
        lines.append("<ul>")
        for rec in sorted(traits_removed, key=species_sort_key):
            removed = rec.get("characteristics_removed") or []
            removed_str = ", ".join(sorted(removed))
            lines.append(
                f"<li>{html_line(rec)}"
                f"<div class='trait-removed'>Removed: {escape(removed_str)}</div>"
                f"</li>"
            )
        lines.append("</ul>")
    else:
        lines.append("<ul><li>None</li></ul>")

    lines.append("<div class='section-divider'></div>")

    # Bio changes
    lines.append(html_section_title("Bio Changes"))

    # Bios added
    lines.append("<h3>Bios ADDED</h3>")
    if bios_added:
        lines.append("<ul>")
        for rec in sorted(bios_added, key=species_sort_key):
            lines.append(f"<li>{html_line(rec)}</li>")
        lines.append("</ul>")
    else:
        lines.append("<ul><li>None</li></ul>")

    # Bios removed
    lines.append("<h3>Bios REMOVED</h3>")
    if bios_removed:
        lines.append("<ul>")
        for rec in sorted(bios_removed, key=species_sort_key):
            lines.append(f"<li>{html_line(rec)}</li>")
        lines.append("</ul>")
    else:
        lines.append("<ul><li>None</li></ul>")

    # Bios changed
    lines.append("<h3>Bios CHANGED</h3>")
    if bios_changed:
        lines.append("<ul>")
        for rec in sorted(bios_changed, key=species_sort_key):
            pct = rec.get("bio_delta_pct")
            pct_str = f"~{pct}%" if pct is not None else ""

            lines.append(
                f"<li>{html_line(rec)}"
                f"<div class='bio-changed'>Bio changed by {escape(pct_str)}</div>"
                f"</li>"
            )
        lines.append("</ul>")
    else:
        lines.append("<ul><li>None</li></ul>")

    lines.append("<div class='section-divider'></div>")

    # Location changes
    lines.append(html_section_title("Location Changes"))

    def add_loc_bucket(title: str, records: list[dict]):
        lines.append(f"<h3>{escape(title)}</h3>")
        if records:
            lines.append("<ul>")
            for r in sorted(records, key=species_sort_key):
                old_loc = r.get("location_old") or "Unknown"
                new_loc = r.get("location_new") or "Unknown"
                lines.append(
                    "<li>"
                    f"{html_line(r)}"
                    f"<div class='location'>Location: {escape(old_loc)} \u2192 {escape(new_loc)}</div>"
                    "</li>"
                )
            lines.append("</ul>")
        else:
            lines.append("<ul><li>None</li></ul>")

    if location_changes:
        add_loc_bucket("Went to foster", went_to_foster)
        add_loc_bucket("Returned from foster", returned_from_foster)
        add_loc_bucket("Kennel changes", kennel_moves)
        add_loc_bucket("Other / uncategorized location changes", other_loc)
    else:
        lines.append("<li><ul><em>No location changes</em></li></ul>")

    lines.append("<p style='margin-top:18px; font-size:12px; color:#9ca3af;'>End of report.</p>")
    lines.append("</body></html>")

    return "\n".join(lines)

def send_email(
    subject: str,
    text_body: str,
    html_body: str | None,
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

    # Plain-text fallback
    msg.set_content(text_body)

    # HTML alternative
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(username, password)
        server.send_message(msg)

def main():
    parser = argparse.ArgumentParser(
        description="Email a human-readable summary of Adopets changes."
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
    text_body = build_email_body(diff)
    html_body = build_html_body(diff)

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
        text_body=text_body,
        html_body=html_body,
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