"""CLI for Track Beta (registry contact domain recon) and unified Alpha/Beta dashboard."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from ngo_mail_catch.core.store import (
    open_db,
    sync_alpha_bridge,
    unified_dashboard,
    upsert_beta_contact,
)
from ngo_mail_catch.parser.extract import filter_registry_contacts


def default_db_path() -> str:
    return os.environ.get("BETA_RECON_DB", "private/beta_contacts.sqlite")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ngo-mail-catch")
    ap.add_argument("--db", default=default_db_path(), help="SQLite database path")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="Check SQLite schema readiness")
    sub.add_parser("status", help="Show Track Beta summary")

    p_ing = sub.add_parser("ingest-contacts", help="Ingest JSON array of public registry contact records")
    p_ing.add_argument("--file", required=True)

    p_sync = sub.add_parser("sync-alpha", help="Import Track Alpha bridge JSON snapshot")
    p_sync.add_argument("--bridge-file", default="../ngo-domain-claim/private/alpha_bridge.json")

    sub.add_parser("unified-dashboard", help="Render combined Track Alpha + Track Beta progress")

    args = ap.parse_args(argv)
    db = open_db(args.db)

    if args.cmd == "doctor":
        dash = unified_dashboard(db)
        print(json.dumps({
            "ok": True,
            "db": args.db,
            "alpha_synced": dash["track_alpha_website_surface"]["total_synced"],
            "beta_contacts": dash["track_beta_contact_surface"]["total_contacts"],
        }, indent=2))
        return 0

    if args.cmd == "status":
        dash = unified_dashboard(db)
        print(json.dumps(dash["track_beta_contact_surface"], indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "ingest-contacts":
        raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
        filtered = filter_registry_contacts(raw)
        added = sum(1 for r in filtered if upsert_beta_contact(db, r))
        print(json.dumps({"raw_records": len(raw), "custom_domain_records": len(filtered), "added": added}, indent=2))
        return 0

    if args.cmd == "sync-alpha":
        payload = json.loads(Path(args.bridge_file).read_text(encoding="utf-8"))
        count = sync_alpha_bridge(db, payload)
        print(json.dumps({"synced_from": args.bridge_file, "alpha_records": count}, indent=2))
        return 0

    if args.cmd == "unified-dashboard":
        print(json.dumps(unified_dashboard(db), indent=2, ensure_ascii=False))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
