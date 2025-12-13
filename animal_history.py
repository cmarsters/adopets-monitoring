# animal_history.py

"""
Usage:
  python animal_history.py 17360
  python animal_history.py 17360 --snapshot-dir snapshots
  python animal_history.py 17360 --format md --output dakota_history.md
  python animal_history.py 17360 --format json --output dakota_history.json
  python animal_history.py 17360 --show-bio

What it does:
  - Scans all snapshot JSON files in SNAPSHOT_DIR
  - Extracts the record for the given animal_id from each snapshot
  - Sorts chronologically by filename timestamp (YYYY-MM-DDTHH-MM-SS.json)
  - Produces a compact change log + a restore pack (latest non-empty bio/traits)
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

SNAP_TS_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})")


def parse_snapshot_dt(path: Path) -> datetime:
    """
    Extract datetime from snapshot filename like:
      2025-12-04T23-22-46.json
    """
    m = SNAP_TS_PATTERN.search(path.name)
    if not m:
        raise ValueError(f"Could not parse timestamp from snapshot filename: {path.name}")
    date_str, hh, mm, ss = m.groups()
    iso = f"{date_str}T{hh}:{mm}:{ss}"
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S")


def normalize_text(s: Optional[str]) -> str:
    """Whitespace-normalize text (good for bio comparisons)."""
    return " ".join((s or "").split())


def safe_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def as_str(x: Any) -> str:
    return "" if x is None else str(x)


@dataclass
class SnapshotHit:
    path: Path
    dt: datetime
    record: Dict[str, Any]


def load_snapshot_records(path: Path) -> List[Dict[str, Any]]:
    """
    Snapshot format expected: list[dict]
    (Your fetch script writes a list of normalized records.)
    """
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Snapshot {path} is not a list; got {type(data)}")
    # only keep dict records
    return [r for r in data if isinstance(r, dict)]


def find_animal_in_snapshot(path: Path, animal_id: str) -> Optional[Dict[str, Any]]:
    """
    Returns the record dict if present, else None.
    """
    records = load_snapshot_records(path)
    for rec in records:
        if as_str(rec.get("animal_id")).strip() == animal_id:
            return rec
    return None


def snapshot_files(snapshot_dir: Path) -> List[Path]:
    snaps = []
    for p in snapshot_dir.glob("*.json"):
        # skip diff artifacts if any live here
        if p.name.startswith("diff_") or p.name in ("latest_diff.json",):
            continue
        # ensure it matches your timestamped naming
        if SNAP_TS_PATTERN.search(p.name):
            snaps.append(p)
    snaps.sort(key=lambda p: parse_snapshot_dt(p))
    return snaps


def pick_fields(rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fields we care about for change history.
    Add/remove fields here as you like.
    """
    return {
        "uuid": rec.get("uuid"),
        "animal_id": rec.get("animal_id"),
        "name": rec.get("name"),
        "species": rec.get("species"),
        "sex": rec.get("sex"),
        "age_key": rec.get("age_key"),
        "size_key": rec.get("size_key"),
        "breed_primary_name": rec.get("breed_primary_name"),
        "status": rec.get("status"),
        "location": rec.get("location"),
        "foster": rec.get("foster"),
        "kennel_number": rec.get("kennel_number"),
        "picture": rec.get("picture"),
        "characteristic_keys": safe_list(rec.get("characteristic_keys")),
        "characteristic_names": safe_list(rec.get("characteristic_names")),
        "description_html": rec.get("description_html"),
    }


def diff_records(prev: Dict[str, Any], cur: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a compact diff dict describing what changed between snapshots.
    """
    changes: Dict[str, Any] = {}

    # scalar fields
    scalar_fields = [
        "name", "species", "sex", "age_key", "size_key",
        "breed_primary_name", "status", "location", "kennel_number"
    ]
    for k in scalar_fields:
        a = prev.get(k)
        b = cur.get(k)
        if a != b:
            changes[k] = {"old": a, "new": b}

    # foster boolean-ish
    prev_foster = bool(prev.get("foster"))
    cur_foster = bool(cur.get("foster"))
    if prev_foster != cur_foster:
        changes["foster"] = {"old": prev_foster, "new": cur_foster}

    # traits
    prev_keys = set(safe_list(prev.get("characteristic_keys")))
    cur_keys = set(safe_list(cur.get("characteristic_keys")))
    if prev_keys != cur_keys:
        changes["traits"] = {
            "added": sorted(cur_keys - prev_keys),
            "removed": sorted(prev_keys - cur_keys),
        }

    # bio
    prev_bio = normalize_text(prev.get("description_html"))
    cur_bio = normalize_text(cur.get("description_html"))
    if prev_bio != cur_bio:
        # quick classification
        if not prev_bio and cur_bio:
            bio_type = "added"
        elif prev_bio and not cur_bio:
            bio_type = "removed"
        else:
            bio_type = "changed"
        changes["bio"] = {"type": bio_type}

    return changes


def latest_nonempty_bio(hits: List[SnapshotHit]) -> Optional[Tuple[datetime, str]]:
    for hit in reversed(hits):
        bio = normalize_text(hit.record.get("description_html"))
        if bio:
            return hit.dt, bio
    return None


def latest_nonempty_traits(hits: List[SnapshotHit]) -> Optional[Tuple[datetime, List[str], List[str]]]:
    for hit in reversed(hits):
        keys = safe_list(hit.record.get("characteristic_keys"))
        names = safe_list(hit.record.get("characteristic_names"))
        if keys or names:
            return hit.dt, [as_str(k) for k in keys], [as_str(n) for n in names]
    return None


def render_md(animal_id: str, hits: List[SnapshotHit], show_bio: bool) -> str:
    if not hits:
        return f"# Animal History\n\nNo snapshots contained animal_id `{animal_id}`.\n"

    first = hits[0]
    last = hits[-1]

    lines: List[str] = []
    lines.append(f"# Animal History: **[{animal_id}] {first.record.get('name','?')}**")
    lines.append("")
    lines.append(f"- Snapshots found: **{len(hits)}**")
    lines.append(f"- First seen: **{first.dt}** ({first.path.name})")
    lines.append(f"- Last seen: **{last.dt}** ({last.path.name})")
    lines.append("")

    # baseline summary (latest record)
    lines.append("## Latest profile (from most recent snapshot)")
    latest = last.record
    def v(k): return latest.get(k) if latest.get(k) not in ("", None, []) else "—"
    lines.append(f"- Name: **{v('name')}**")
    lines.append(f"- Species: {v('species')}")
    lines.append(f"- Sex / Age / Size: {v('sex')} / {v('age_key')} / {v('size_key')}")
    lines.append(f"- Breed: {v('breed_primary_name')}")
    lines.append(f"- Status: {v('status')}")
    lines.append(f"- Location: {v('location')}")
    lines.append("")

    # change log
    lines.append("## Change log")
    prev = hits[0].record
    lines.append(f"### {hits[0].dt} — baseline ({hits[0].path.name})")
    lines.append("")

    for hit in hits[1:]:
        cur = hit.record
        changes = diff_records(prev, cur)
        if not changes:
            prev = cur
            continue

        lines.append(f"### {hit.dt} ({hit.path.name})")
        # scalars
        for k, ch in changes.items():
            if k in ("traits", "bio"):
                continue
            old = ch.get("old")
            new = ch.get("new")
            lines.append(f"- **{k}**: `{old}` → `{new}`")

        # traits
        if "traits" in changes:
            t = changes["traits"]
            if t["added"]:
                lines.append(f"- **traits added**: {', '.join(t['added'])}")
            if t["removed"]:
                lines.append(f"- **traits removed**: {', '.join(t['removed'])}")

        # bio
        if "bio" in changes:
            bt = changes["bio"]["type"]
            lines.append(f"- **bio**: {bt}")
            if show_bio:
                old_bio = normalize_text(prev.get("description_html"))
                new_bio = normalize_text(cur.get("description_html"))
                if old_bio:
                    lines.append("")
                    lines.append("  **Old bio:**")
                    lines.append("")
                    lines.append(f"  > {old_bio[:1200]}{'…' if len(old_bio)>1200 else ''}")
                if new_bio:
                    lines.append("")
                    lines.append("  **New bio:**")
                    lines.append("")
                    lines.append(f"  > {new_bio[:1200]}{'…' if len(new_bio)>1200 else ''}")
                lines.append("")

        lines.append("")
        prev = cur

    # restore pack
    lines.append("## Restore pack")
    bio = latest_nonempty_bio(hits)
    traits = latest_nonempty_traits(hits)

    if bio:
        dt, text = bio
        lines.append(f"- Latest non-empty bio: **{dt}**")
        lines.append("")
        lines.append("```")
        lines.append(text)
        lines.append("```")
    else:
        lines.append("- Latest non-empty bio: —")

    if traits:
        dt, keys, names = traits
        lines.append(f"- Latest non-empty traits: **{dt}**")
        if keys:
            lines.append(f"  - Keys: {', '.join(keys)}")
        if names:
            lines.append(f"  - Names: {', '.join(names)}")
    else:
        lines.append("- Latest non-empty traits: —")

    lines.append("")
    return "\n".join(lines)


def render_json(animal_id: str, hits: List[SnapshotHit]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "animal_id": animal_id,
        "snapshots_found": len(hits),
        "history": [],
        "restore_pack": {},
    }
    if not hits:
        return out

    prev = hits[0].record
    out["history"].append({
        "dt": hits[0].dt.isoformat(timespec="seconds"),
        "snapshot": hits[0].path.name,
        "record": prev,
        "changes_from_prev": None,
    })

    for hit in hits[1:]:
        cur = hit.record
        changes = diff_records(prev, cur)
        out["history"].append({
            "dt": hit.dt.isoformat(timespec="seconds"),
            "snapshot": hit.path.name,
            "record": cur,
            "changes_from_prev": changes or None,
        })
        prev = cur

    bio = latest_nonempty_bio(hits)
    traits = latest_nonempty_traits(hits)
    if bio:
        dt, text = bio
        out["restore_pack"]["latest_nonempty_bio"] = {
            "dt": dt.isoformat(timespec="seconds"),
            "text": text,
        }
    if traits:
        dt, keys, names = traits
        out["restore_pack"]["latest_nonempty_traits"] = {
            "dt": dt.isoformat(timespec="seconds"),
            "characteristic_keys": keys,
            "characteristic_names": names,
        }

    return out


def main():
    parser = argparse.ArgumentParser(description="Show full history of an animal across Adopets snapshots.")
    parser.add_argument("animal_id", help="Animal ID (Adopets code), e.g. 17360")
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=Path(os.environ.get("SNAPSHOT_DIR", "snapshots")),
        help="Directory containing snapshots (default: SNAPSHOT_DIR env var or 'snapshots')",
    )
    parser.add_argument("--format", choices=["md", "json"], default="md", help="Output format")
    parser.add_argument("--output", type=Path, default=None, help="Write output to file instead of stdout")
    parser.add_argument("--show-bio", action="store_true", help="Include old/new bio text in change log (can be long)")
    args = parser.parse_args()

    snapshot_dir: Path = args.snapshot_dir
    if not snapshot_dir.exists():
        raise SystemExit(f"Snapshot dir not found: {snapshot_dir}")

    animal_id = str(args.animal_id).strip()

    snaps = snapshot_files(snapshot_dir)
    hits: List[SnapshotHit] = []

    for snap in snaps:
        rec = find_animal_in_snapshot(snap, animal_id)
        if rec is None:
            continue
        hits.append(SnapshotHit(path=snap, dt=parse_snapshot_dt(snap), record=pick_fields(rec)))

    if args.format == "json":
        payload = render_json(animal_id, hits)
        text = json.dumps(payload, indent=2, ensure_ascii=False)
    else:
        text = render_md(animal_id, hits, show_bio=args.show_bio)

    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"Wrote {args.format} history to: {args.output}")
    else:
        print(text)


if __name__ == "__main__":
    main()