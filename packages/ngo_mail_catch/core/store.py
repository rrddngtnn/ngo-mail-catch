"""SQLite store for Track Beta (registry contact domain targets) and Track Alpha bridge mirror."""
from __future__ import annotations

import datetime
import os
from pathlib import Path
import sqlite3
from typing import Any

BETA_STAGES = (
    "extracted",
    "gs_matched_web_null",
    "dns_dead",
    "rdap_available",
    "verified_candidate",
    "domain_registered",
    "mailbox_ready",
    "claimed_goodstack",
    "approved_claude",
    "archived",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS beta_contacts (
  id              INTEGER PRIMARY KEY,
  registry_code   TEXT NOT NULL,
  registry_id     TEXT NOT NULL,
  iso3            TEXT NOT NULL,
  country         TEXT NOT NULL,
  name            TEXT NOT NULL,
  contact_email   TEXT NOT NULL,
  email_domain    TEXT NOT NULL,
  gs_id           TEXT,
  gs_website_null INTEGER NOT NULL DEFAULT 1,
  rdap_status     TEXT NOT NULL DEFAULT 'UNCHECKED',
  stage           TEXT NOT NULL DEFAULT 'extracted',
  notes           TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  UNIQUE(registry_code, registry_id, email_domain)
);

CREATE TABLE IF NOT EXISTS alpha_mirror (
  gs_id             TEXT PRIMARY KEY,
  iso3              TEXT NOT NULL,
  country           TEXT NOT NULL,
  name              TEXT NOT NULL,
  registry_code     TEXT NOT NULL,
  registry_id       TEXT NOT NULL,
  root_domain       TEXT NOT NULL,
  confidence        TEXT NOT NULL,
  stage             TEXT NOT NULL,
  score             INTEGER NOT NULL,
  wayback_snapshots INTEGER DEFAULT 0,
  synced_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_beta_dom   ON beta_contacts(email_domain);
CREATE INDEX IF NOT EXISTS ix_beta_stage ON beta_contacts(stage);
CREATE TABLE IF NOT EXISTS verification_events (
  id               INTEGER PRIMARY KEY,
  account_id       TEXT NOT NULL,
  message_id       TEXT NOT NULL,
  sender           TEXT,
  recipient        TEXT,
  email_domain     TEXT,
  subject          TEXT,
  verification_url TEXT NOT NULL,
  token            TEXT,
  received_at      TEXT NOT NULL,
  UNIQUE(account_id, message_id)
);

CREATE TABLE IF NOT EXISTS imap_checkpoints (
  account_id  TEXT PRIMARY KEY,
  uidvalidity TEXT NOT NULL,
  last_uid    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_beta_dom   ON beta_contacts(email_domain);
CREATE INDEX IF NOT EXISTS ix_beta_stage ON beta_contacts(stage);
CREATE INDEX IF NOT EXISTS ix_alpha_dom  ON alpha_mirror(root_domain);
CREATE INDEX IF NOT EXISTS ix_ver_dom    ON verification_events(email_domain, received_at DESC);
"""


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def open_db(path: str | Path = "private/beta_contacts.sqlite") -> sqlite3.Connection:
    p = Path(path)
    if p.is_symlink():
        raise ValueError(f"Refusing symlink database path: {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(p))
    try:
        os.chmod(str(p), 0o600)
    except OSError:
        pass
    db.executescript(SCHEMA)
    db.row_factory = sqlite3.Row
    return db


def upsert_beta_contact(db: sqlite3.Connection, rec: dict[str, Any]) -> bool:
    now = utcnow()
    stage = rec.get("stage") or (
        "rdap_available" if "404" in str(rec.get("rdap_status", "")) else "extracted"
    )
    cur = db.execute(
        """SELECT id FROM beta_contacts
           WHERE registry_code=? AND registry_id=? AND email_domain=?""",
        (rec["registry_code"], rec["registry_id"], rec["email_domain"]),
    ).fetchone()
    if cur:
        db.execute(
            """UPDATE beta_contacts
               SET name=?, contact_email=?, gs_id=?, gs_website_null=?,
                   rdap_status=?, stage=?, updated_at=?
               WHERE id=?""",
            (
                rec["name"],
                rec["contact_email"],
                rec.get("gs_id"),
                1 if rec.get("gs_website_null", True) else 0,
                rec.get("rdap_status") or "UNCHECKED",
                stage,
                now,
                cur["id"],
            ),
        )
        db.commit()
        return False

    db.execute(
        """INSERT INTO beta_contacts(
             registry_code, registry_id, iso3, country, name, contact_email,
             email_domain, gs_id, gs_website_null, rdap_status, stage,
             created_at, updated_at
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            rec["registry_code"],
            rec["registry_id"],
            rec["iso3"],
            rec["country"],
            rec["name"],
            rec["contact_email"],
            rec["email_domain"],
            rec.get("gs_id"),
            1 if rec.get("gs_website_null", True) else 0,
            rec.get("rdap_status") or "UNCHECKED",
            stage,
            now,
            now,
        ),
    )
    db.commit()
    return True


def sync_alpha_bridge(db: sqlite3.Connection, payload: dict[str, Any]) -> int:
    if payload.get("schema") != "ngo-recon-bridge/v1":
        raise ValueError(f"Unsupported bridge schema: {payload.get('schema')}")
    now = utcnow()
    records = payload.get("records") or []
    for r in records:
        db.execute(
            """INSERT INTO alpha_mirror(
                 gs_id, iso3, country, name, registry_code, registry_id,
                 root_domain, confidence, stage, score, wayback_snapshots, synced_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(gs_id) DO UPDATE SET
                 stage=excluded.stage,
                 score=excluded.score,
                 wayback_snapshots=excluded.wayback_snapshots,
                 synced_at=excluded.synced_at""",
            (
                r["gs_id"],
                r["iso3"],
                r["country"],
                r["name"],
                r["registry_code"],
                r["registry_id"],
                r["root_domain"],
                r["confidence"],
                r["stage"],
                int(r["score"]),
                int(r.get("wayback_snapshots") or 0),
                now,
            ),
        )
    db.commit()
    return len(records)


def unified_dashboard(db: sqlite3.Connection, alpha_repo_path: str = "") -> dict[str, Any]:
    alpha_total = db.execute("SELECT COUNT(*) AS c FROM alpha_mirror").fetchone()["c"]
    alpha_stages = {
        r["stage"]: r["c"]
        for r in db.execute("SELECT stage, COUNT(*) AS c FROM alpha_mirror GROUP BY stage").fetchall()
    }
    alpha_top = [
        dict(r)
        for r in db.execute(
            """SELECT iso3, name, registry_code, registry_id, root_domain,
                      stage, score, wayback_snapshots
               FROM alpha_mirror
               ORDER BY score DESC, wayback_snapshots DESC
               LIMIT 8"""
        ).fetchall()
    ]

    beta_total = db.execute("SELECT COUNT(*) AS c FROM beta_contacts").fetchone()["c"]
    beta_stages = {
        r["stage"]: r["c"]
        for r in db.execute("SELECT stage, COUNT(*) AS c FROM beta_contacts GROUP BY stage").fetchall()
    }
    overlap = [
        dict(r)
        for r in db.execute(
            """SELECT b.iso3, b.name, b.contact_email, b.email_domain,
                      a.gs_id AS alpha_gs_id, a.stage AS alpha_stage
               FROM beta_contacts b
               INNER JOIN alpha_mirror a ON b.email_domain = a.root_domain"""
        ).fetchall()
    ]
    beta_exclusive = [
        dict(r)
        for r in db.execute(
            """SELECT b.iso3, b.country, b.name, b.registry_code, b.registry_id,
                      b.contact_email, b.email_domain, b.rdap_status, b.stage
               FROM beta_contacts b
               LEFT JOIN alpha_mirror a ON b.email_domain = a.root_domain
               WHERE a.gs_id IS NULL
               ORDER BY b.id ASC
               LIMIT 10"""
        ).fetchall()
    ]
    return {
        "schema": "ngo-recon-bridge/v1",
        "track_alpha_website_surface": {
            "repo": alpha_repo_path or "ngo-domain-claim",
            "total_synced": alpha_total,
            "by_stage": alpha_stages,
            "top_candidates": alpha_top,
        },
        "track_beta_contact_surface": {
            "repo": "ngo-mail-catch",
            "total_contacts": beta_total,
            "by_stage": beta_stages,
            "exclusive_candidates": beta_exclusive,
        },
        "cross_track_domain_overlap": overlap,
    }


def rank_beta_contacts(
    db: sqlite3.Connection,
    *,
    iso3: str | None = None,
    stage: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if iso3:
        clauses.append("iso3=?")
        params.append(iso3.upper())
    if stage:
        clauses.append("stage=?")
        params.append(stage)
    params.append(limit)
    sql = f"""
        SELECT id, iso3, country, registry_code, registry_id, name,
               contact_email, email_domain, gs_id, gs_website_null,
               rdap_status, stage, notes, updated_at
        FROM beta_contacts
        WHERE {' AND '.join(clauses)}
        ORDER BY gs_website_null DESC, (gs_id IS NOT NULL) DESC, id ASC
        LIMIT ?
    """
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def advance_beta_stage(
    db: sqlite3.Connection,
    identifier: str,
    new_stage: str,
    notes: str | None = None,
) -> dict[str, Any]:
    if new_stage not in BETA_STAGES:
        raise ValueError(f"Unsupported beta stage: {new_stage}. Valid: {BETA_STAGES}")
    row = db.execute(
        "SELECT * FROM beta_contacts WHERE email_domain=? OR contact_email=? OR registry_id=?",
        (identifier.lower(), identifier.lower(), identifier),
    ).fetchone()
    if not row:
        raise KeyError(f"Beta target not found: {identifier}")
    now = utcnow()
    if notes is not None:
        db.execute("UPDATE beta_contacts SET stage=?, notes=?, updated_at=? WHERE id=?", (new_stage, notes, now, row["id"]))
    else:
        db.execute("UPDATE beta_contacts SET stage=?, updated_at=? WHERE id=?", (new_stage, now, row["id"]))
    db.commit()
    return dict(db.execute("SELECT * FROM beta_contacts WHERE id=?", (row["id"],)).fetchone())


def save_verification_event(
    db: sqlite3.Connection,
    *,
    account_id: str,
    message_id: str,
    sender: str,
    recipient: str,
    subject: str,
    verification_url: str,
    token: str | None,
) -> bool:
    """Persist a captured verification link and auto-update matching beta_contacts stage."""
    now = utcnow()
    rec_clean = (recipient or "").strip().lower()
    dom = ""
    if "@" in rec_clean:
        dom = rec_clean.split("@", 1)[-1].strip(" >.")

    cur = db.execute(
        "SELECT id FROM verification_events WHERE account_id=? AND message_id=?",
        (account_id, message_id),
    ).fetchone()
    if cur:
        return False

    db.execute(
        """INSERT INTO verification_events(
             account_id, message_id, sender, recipient, email_domain,
             subject, verification_url, token, received_at
           ) VALUES (?,?,?,?,?,?,?,?,?)""",
        (account_id, message_id, sender, recipient, dom, subject, verification_url, token, now),
    )
    if dom:
        db.execute(
            """UPDATE beta_contacts
               SET stage='verified_candidate',
                   notes=?,
                   updated_at=?
               WHERE email_domain=? AND stage NOT IN ('claimed_goodstack', 'approved_claude')""",
            (f"verification_url:{verification_url[:180]}", now, dom),
        )
    db.commit()
    return True


def list_verification_events(
    db: sqlite3.Connection,
    *,
    domain: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if domain:
        rows = db.execute(
            "SELECT * FROM verification_events WHERE email_domain=? ORDER BY id DESC LIMIT ?",
            (domain.lower().strip("."), limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM verification_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
