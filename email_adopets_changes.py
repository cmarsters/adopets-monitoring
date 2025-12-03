# email_adopets_changes.py

import json
from pathlib import Path
import argparse
import os
import smtplib
from email.message import EmailMessage
from datetime import datetime
from textwrap import shorten


def load_diff(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def extract_date_from_filename(path: str) -> str:
    """
    Extract YYYY-MM-DD from a filename and format it as 'Dec 2, 2025'.
    If no date found, return the original path.
    """
    import re

    match = re.search(r"\d{4}-\d{2}-\d{2}", path)
    if not match:
        return path

    date_str = match.group(0)
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%b %d, %Y")
    except ValueError:
        return date_str


def one_line(rec: dict) -> str:
    """One-line dog summary."""
    code = rec.get("code", "?")
    name = rec.get("name", "?")
    sex = rec.get("sex") or rec.get("sex_key") or "?"
    age = rec.get("age_key") or "?"
    size = rec.get("size_key") or "?"
    status = rec.get("status") or rec.get("status_new") or rec.get("status_old") or "?"
    return f"[{code}] {name} ({sex}, {age}, {size}, {status})"


def build_email_body(diff: dict) -> str:
    summary = diff.get("summary", {})
    animals_added = diff.get("animals_added", [])
    animals_removed = diff.get("animals_removed", [])
    animals_changed = diff.get("animals_changed", [])

    old_label = extract_date_from_filename(diff.get("old_snapshot", ""))
    new_label = extract_date_from_filename(diff.get("new_snapshot", ""))

    # Split changed into buckets
    trait_loss = []      # dogs that LOST at least one trait
    trait_gain_only = [] # dogs that only gained traits
    bio_changes = []     # dogs with bio changes

    for rec in animals_changed:
        added = set(rec.get("characteristics_added") or [])
        removed = set(rec.get("characteristics_removed") or [])
        desc_changed = bool(rec.get("description_changed"))

        if removed:
            trait_loss.append(rec)
        elif added:
            trait_gain_only.append(rec)
        if desc_changed:
            bio_changes.append(rec)

    lines = []

    lines.append(f"Adopets profile changes for AAC")
    lines.append(f"From {old_label} to {new_label}")
    lines.append("")
    lines.append("Summary:")
    lines.append(f"- Old total: {summary.get('total_old', 0)}")
    lines.append(f"- New total: {summary.get('total_new', 0)}")
    lines.append(f"- Animals added: {summary.get('animals_added', 0)}")
    lines.append(f"- Animals removed: {summary.get('animals_removed', 0)}")
    lines.append(f"- Animals with any changes: {summary.get('animals_changed', 0)}")
    lines.append("")
    lines.append("=" * 60)
    lines.append("")

    # 1) Trait losses (main thing you care about)
    lines.append("DOGS THAT LOST TRAITS (most important):")
    if trait_loss:
        for rec in trait_loss:
            lines.append(f"- {one_line(rec)}")
            removed = ", ".join(sorted(rec.get("characteristics_removed") or []))
            added = ", ".join(sorted(rec.get("characteristics_added") or []))
            lines.append(f"    Removed: {removed}")
            if added:
                lines.append(f"    Added:   {added}")
        lines.append("")
    else:
        lines.append("  None 🥳")
        lines.append("")

    lines.append("=" * 60)
    lines.append("")

    # 2) New dogs
    lines.append("NEW DOGS ADDED:")
    if animals_added:
        for rec in animals_added:
            lines.append(f"- {one_line(rec)}")
        lines.append("")
    else:
        lines.append("  None.")
        lines.append("")

    # 3) Dogs removed (adopted, transferred, etc.)
    lines.append("DOGS REMOVED FROM THIS SNAPSHOT:")
    if animals_removed:
        for rec in animals_removed:
            lines.append(f"- {one_line(rec)}")
        lines.append("")
    else:
        lines.append("  None.")
        lines.append("")

    # 4) Dogs with only trait gains (nice to know, but less urgent)
    lines.append("DOGS THAT ONLY GAINED TRAITS:")
    if trait_gain_only:
        for rec in trait_gain_only:
            lines.append(f"- {one_line(rec)}")
            added = ", ".join(sorted(rec.get("characteristics_added") or []))
            lines.append(f"    Added: {added}")
        lines.append("")
    else:
        lines.append("  None.")
        lines.append("")

    # 5) Bio changes (just show a snippet of the new bio)
    lines.append("DOGS WITH BIO CHANGES:")
    if bio_changes:
        for rec in bio_changes:
            lines.append(f"- {one_line(rec)}")
            new_desc = rec.get("description_new") or ""
            if new_desc:
                snip = shorten(" ".join(new_desc.split()), width=200, placeholder="...")
                lines.append(f"    New bio (first 200 chars): {snip}")
        lines.append("")
    else:
        lines.append("  None.")
        lines.append("")

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

    # --- CONFIG via environment variables ---
    # These should be set in your shell or venv activation:
    #   export AAC_EMAIL_FROM="youremail@gmail.com"
    #   export AAC_EMAIL_TO="you@example.com,other@example.com"
    #   export AAC_EMAIL_USER="youremail@gmail.com"
    #   export AAC_EMAIL_PASS="your_app_password"
    # Optionally:
    #   export AAC_EMAIL_SUBJECT_PREFIX="[AAC Adopets]"
    from_addr = os.environ.get("AAC_EMAIL_FROM")
    to_env = os.environ.get("AAC_EMAIL_TO")
    username = os.environ.get("AAC_EMAIL_USER", from_addr)
    password = os.environ.get("AAC_EMAIL_PASS")
    subject_prefix = os.environ.get("AAC_EMAIL_SUBJECT_PREFIX", "[AAC Adopets]")

    if not from_addr or not to_env or not password:
        raise SystemExit(
            "Missing email configuration. Please set AAC_EMAIL_FROM, "
            "AAC_EMAIL_TO, and AAC_EMAIL_PASS in your environment."
        )

    to_addrs = [addr.strip() for addr in to_env.split(",") if addr.strip()]

    old_label = extract_date_from_filename(diff.get("old_snapshot", ""))
    new_label = extract_date_from_filename(diff.get("new_snapshot", ""))
    subject = f"{subject_prefix} Changes from {old_label} to {new_label}"

    # For Gmail; adjust if you use another provider
    smtp_host = "smtp.gmail.com"
    smtp_port = 587

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