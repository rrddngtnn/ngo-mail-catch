"""CLI for Track Beta (registry contact domain OSINT) and unified Alpha/Beta dashboard."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from ngo_mail_catch.core.config import load_config
from ngo_mail_catch.core.store import (
    open_db,
    sync_alpha_bridge,
    unified_dashboard,
    upsert_beta_contact,
)
from ngo_mail_catch.correlator.verifier import scan_contact_source
from ngo_mail_catch.parser.extract import filter_registry_contacts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ngo-mail-catch")
    ap.add_argument("--config", default=None, help="Path to TOML config (overrides BETA_CONFIG)")
    ap.add_argument("--db", default=None, help="SQLite database path")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="Check SQLite schema and resolved configuration")
    sub.add_parser("status", help="Show Track Beta summary")

    p_scan = sub.add_parser("scan-source", help="Run automated OSINT pull -> freemail filter -> DNS -> IANA RDAP")
    p_scan.add_argument("--source-kind", default=None, choices=["odata_json", "csv_file", "json_file", "text_regex"])
    p_scan.add_argument("--url", default=None)
    p_scan.add_argument("--file", default=None)
    p_scan.add_argument("--iso3", default=None)
    p_scan.add_argument("--country", default=None)
    p_scan.add_argument("--registry-code", default=None)
    p_scan.add_argument("--no-doh", action="store_true")
    p_scan.add_argument("--directory-check", action="store_true")
    p_scan.add_argument("--dry-run", action="store_true")

    p_ing = sub.add_parser("ingest-contacts", help="Ingest JSON array of public registry contact records")
    p_ing.add_argument("--file", required=True)

    p_sync = sub.add_parser("sync-alpha", help="Import Track Alpha bridge JSON snapshot")
    p_sync.add_argument("--bridge-file", default=None)

    sub.add_parser("unified-dashboard", help="Render combined Track Alpha + Track Beta progress")

    args = ap.parse_args(argv)
    overrides: dict[str, object] = {}
    if args.db:
        overrides["db"] = args.db
    if args.cmd == "scan-source":
        if args.source_kind:
            overrides["source_kind"] = args.source_kind
        if args.url:
            overrides["url"] = args.url
        if args.file:
            overrides["file"] = args.file
        if args.iso3:
            overrides["iso3"] = args.iso3
        if args.country:
            overrides["country"] = args.country
        if args.registry_code:
            overrides["registry_code"] = args.registry_code
        if args.no_doh:
            overrides["enable_doh"] = False
        if args.directory_check:
            overrides["enable_directory_check"] = True
    if args.cmd == "sync-alpha" and args.bridge_file:
        overrides["bridge_file"] = args.bridge_file

    cfg = load_config(args.config, cli_overrides=overrides)
    db = open_db(cfg.db_path)

    if args.cmd == "doctor":
        dash = unified_dashboard(db, alpha_repo_path=cfg.alpha_repo_path)
        print(json.dumps({
            "ok": True,
            "db_path": cfg.db_path,
            "bridge_file": cfg.bridge_file,
            "source_kind": cfg.source.kind,
            "directory_check": cfg.correlator.enable_directory_check,
            "doh_enabled": cfg.correlator.enable_doh,
            "alpha_synced": dash["track_alpha_website_surface"]["total_synced"],
            "beta_contacts": dash["track_beta_contact_surface"]["total_contacts"],
        }, indent=2))
        return 0

    if args.cmd == "status":
        dash = unified_dashboard(db, alpha_repo_path=cfg.alpha_repo_path)
        print(json.dumps(dash["track_beta_contact_surface"], indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "scan-source":
        report = scan_contact_source(cfg, db, dry_run=args.dry_run)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "ingest-contacts":
        raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
        filtered = filter_registry_contacts(raw)
        added = sum(1 for r in filtered if upsert_beta_contact(db, r))
        print(json.dumps({"raw_records": len(raw), "custom_domain_records": len(filtered), "added": added}, indent=2))
        return 0

    if args.cmd == "sync-alpha":
        payload = json.loads(Path(cfg.bridge_file).read_text(encoding="utf-8"))
        count = sync_alpha_bridge(db, payload)
        print(json.dumps({"synced_from": cfg.bridge_file, "alpha_records": count}, indent=2))
        return 0

    if args.cmd == "unified-dashboard":
        print(json.dumps(unified_dashboard(db, alpha_repo_path=cfg.alpha_repo_path), indent=2, ensure_ascii=False))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
