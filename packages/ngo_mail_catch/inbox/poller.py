"""MIME parser and read-only IMAP SSL poller for verification links."""
from __future__ import annotations

from email import message_from_bytes
from email.header import decode_header
from email.message import Message
import html as html_lib
import imaplib
import os
import re
import sqlite3
from typing import Any
import urllib.parse

from ngo_mail_catch.core.store import save_verification_event

URL_RE = re.compile(r"""https?://[^\s"'<>)\]]+""", re.I)

VERIFICATION_HOST_KEYWORDS = (
    "goodstack",
    "poweredbypercent",
    "anthropic",
    "claude.com",
)

VERIFICATION_PATH_KEYWORDS = (
    "agent-verification",
    "verification",
    "organisation-association",
    "validate",
    "confirm",
    "claim",
    "token=",
)


def _decode_mime_header(val: str | None) -> str:
    if not val:
        return ""
    parts: list[str] = []
    for chunk, enc in decode_header(val):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(enc or "utf-8", errors="ignore"))
        else:
            parts.append(str(chunk))
    return "".join(parts).strip()


def _extract_body_text(msg: Message) -> str:
    chunks: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            if ctype in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    charset = part.get_content_charset() or "utf-8"
                    chunks.append(payload.decode(charset, errors="ignore"))
    else:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            charset = msg.get_content_charset() or "utf-8"
            chunks.append(payload.decode(charset, errors="ignore"))
        elif isinstance(payload, str):
            chunks.append(payload)
    return "\n".join(chunks)


def extract_verification_links(text: str) -> list[dict[str, Any]]:
    decoded = html_lib.unescape(text or "")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for m in URL_RE.finditer(decoded):
        url = m.group(0).rstrip(".,;'\">")
        if url in seen:
            continue
        seen.add(url)
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        path_q = f"{parsed.path}?{parsed.query}".lower()
        if not any(hk in host for hk in VERIFICATION_HOST_KEYWORDS):
            continue
        score = 1
        if any(pk in path_q for pk in VERIFICATION_PATH_KEYWORDS):
            score += 2
        qs = urllib.parse.parse_qs(parsed.query)
        token = (
            (qs.get("agentVerificationToken") or qs.get("verificationToken") or qs.get("token") or [None])[0]
        )
        if token:
            score += 2
        out.append({
            "url": url,
            "host": host,
            "token": token,
            "priority": score,
        })
    return sorted(out, key=lambda x: x["priority"], reverse=True)


def parse_raw_mime(raw_bytes: bytes) -> dict[str, Any]:
    msg = message_from_bytes(raw_bytes)
    msg_id = _decode_mime_header(msg.get("Message-ID")) or f"anon-{hash(raw_bytes)}"
    sender = _decode_mime_header(msg.get("From"))
    recipient = _decode_mime_header(msg.get("To"))
    subject = _decode_mime_header(msg.get("Subject"))
    body = _extract_body_text(msg)
    links = extract_verification_links(body)
    return {
        "message_id": msg_id,
        "sender": sender,
        "recipient": recipient,
        "subject": subject,
        "links": links,
        "primary_url": links[0]["url"] if links else None,
        "primary_token": links[0]["token"] if links else None,
    }


def ingest_mime_bytes(
    db: sqlite3.Connection,
    raw_bytes: bytes,
    *,
    account_id: str = "local_eml",
) -> dict[str, Any]:
    parsed = parse_raw_mime(raw_bytes)
    saved = False
    if parsed["primary_url"]:
        saved = save_verification_event(
            db,
            account_id=account_id,
            message_id=parsed["message_id"],
            sender=parsed["sender"],
            recipient=parsed["recipient"],
            subject=parsed["subject"],
            verification_url=parsed["primary_url"],
            token=parsed["primary_token"],
        )
    return {**parsed, "saved_to_db": saved}


def poll_imap_readonly(
    db: sqlite3.Connection,
    *,
    host: str,
    port: int = 993,
    mailbox: str = "INBOX",
    username: str = "",
    password: str = "",
    limit: int = 25,
    ssl_enabled: bool = True,
    imap_cls: Any = None,
) -> dict[str, Any]:
    """Read-only IMAP poller using UID + BODY.PEEK[] and atomic UIDVALIDITY checkpoints."""
    if not host or not username:
        raise ValueError("IMAP host and username are required")
    account_id = f"imap://{username}@{host}:{port}/{mailbox}"

    client_factory = imap_cls or (imaplib.IMAP4_SSL if ssl_enabled else imaplib.IMAP4)
    client = client_factory(host, port)
    try:
        client.login(username, password)
        typ, data = client.select(mailbox, readonly=True)
        if typ != "OK":
            raise RuntimeError(f"IMAP select failed for {mailbox}")

        status_typ, status_data = client.status(mailbox, "(UIDVALIDITY)")
        uidvalidity = "0"
        if status_typ == "OK" and status_data and status_data[0]:
            raw_st = status_data[0].decode("utf-8", errors="ignore") if isinstance(status_data[0], bytes) else str(status_data[0])
            m = re.search(r"UIDVALIDITY\s+(\d+)", raw_st, re.I)
            if m:
                uidvalidity = m.group(1)

        cp_row = db.execute(
            "SELECT uidvalidity, last_uid FROM imap_checkpoints WHERE account_id=?",
            (account_id,),
        ).fetchone()
        last_uid = int(cp_row["last_uid"]) if (cp_row and cp_row["uidvalidity"] == uidvalidity) else 0

        utyp, udata = client.uid("search", "ALL")
        if utyp != "OK" or not udata or not udata[0]:
            return {"account_id": account_id, "fetched": 0, "matched_links": 0, "events": []}

        all_uids = [int(x) for x in udata[0].split() if x.isdigit()]
        new_uids = [u for u in all_uids if u > last_uid][-max(1, limit):]

        matched_events: list[dict[str, Any]] = []
        max_seen_uid = last_uid
        for uid in new_uids:
            ftyp, fdata = client.uid("fetch", str(uid), "(BODY.PEEK[])")
            if ftyp != "OK" or not fdata:
                continue
            raw_msg = b""
            for part in fdata:
                if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes):
                    raw_msg = part[1]
                    break
            if not raw_msg:
                continue
            info = ingest_mime_bytes(db, raw_msg, account_id=account_id)
            if info["primary_url"]:
                matched_events.append({
                    "uid": uid,
                    "recipient": info["recipient"],
                    "subject": info["subject"],
                    "verification_url": info["primary_url"],
                    "token": info["primary_token"],
                })
            if uid > max_seen_uid:
                max_seen_uid = uid

        if new_uids:
            db.execute(
                """INSERT INTO imap_checkpoints(account_id, uidvalidity, last_uid)
                   VALUES (?,?,?)
                   ON CONFLICT(account_id) DO UPDATE SET
                     uidvalidity=excluded.uidvalidity,
                     last_uid=excluded.last_uid""",
                (account_id, uidvalidity, max_seen_uid),
            )
            db.commit()

        return {
            "account_id": account_id,
            "fetched": len(new_uids),
            "matched_links": len(matched_events),
            "events": matched_events,
        }
    finally:
        try:
            client.logout()
        except Exception:
            pass
