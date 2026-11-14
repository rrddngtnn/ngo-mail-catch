"""Extract custom contact domains from public charity registry rows where website is absent."""
from __future__ import annotations

import re
from typing import Any

FREEMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.uk",
    "yahoo.fr",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "msn.com",
    "icloud.com",
    "aol.com",
    "protonmail.com",
    "proton.me",
    "ymail.com",
    "mail.com",
    "zoho.com",
    "gmx.com",
    "rediffmail.com",
}

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,24})$")


def extract_custom_email_domain(email: str | None) -> str | None:
    if not email:
        return None
    cleaned = email.strip().lower()
    m = EMAIL_RE.match(cleaned)
    if not m:
        return None
    dom = m.group(1).strip(".")
    if dom.startswith("www."):
        dom = dom[4:]
    if dom in FREEMAIL_DOMAINS or any(dom.endswith("." + f) for f in FREEMAIL_DOMAINS):
        return None
    return dom


def filter_registry_contacts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep records that have a custom non-freemail contact domain."""
    out: list[dict[str, Any]] = []
    for rec in records:
        email = (rec.get("email") or rec.get("contact_email") or "").strip().lower()
        dom = extract_custom_email_domain(email)
        if not dom:
            continue
        out.append({
            "iso3": (rec.get("iso3") or rec.get("countryCode") or "UNK").upper(),
            "country": rec.get("country") or "Unknown",
            "registry_code": rec.get("registry_code") or rec.get("regcode") or "REG",
            "registry_id": str(rec.get("registry_id") or rec.get("registryId") or ""),
            "name": (rec.get("name") or "").strip(),
            "contact_email": email,
            "email_domain": dom,
            "gs_id": rec.get("gs_id") or rec.get("id"),
            "gs_website_null": bool(rec.get("gs_website_null", True)),
            "rdap_status": rec.get("rdap_status") or "UNCHECKED",
        })
    return out
