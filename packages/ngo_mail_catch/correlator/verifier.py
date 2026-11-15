"""Verify dead custom contact-email domains via DNS/DoH, IANA RDAP, and optional directory cross-check."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import socket
import sqlite3
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from ngo_mail_catch.core.config import BetaConfig, CorrelatorConfig
from ngo_mail_catch.core.store import upsert_beta_contact
from ngo_mail_catch.parser.extract import extract_custom_email_domain
from ngo_mail_catch.sources.adapters import build_contact_source


def _doh_alive(domain: str, doh_url: str, timeout_s: int, user_agent: str) -> bool:
    for rtype in ("MX", "A", "NS"):
        q = urllib.parse.urlencode({"name": domain, "type": rtype})
        req = urllib.request.Request(
            f"{doh_url}?{q}",
            headers={"Accept": "application/dns-json", "User-Agent": user_agent},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            if data.get("Answer"):
                return True
        except Exception:
            continue
    return False


def probe_dns_alive(domain: str, cor: CorrelatorConfig, user_agent: str, *, use_system_dns: bool = True) -> bool:
    dom = domain.lower().strip(".")
    if use_system_dns:
        try:
            socket.getaddrinfo(dom, None)
            return True
        except socket.gaierror:
            pass
    if cor.enable_doh and cor.doh_url:
        return _doh_alive(dom, cor.doh_url, cor.timeout_s, user_agent)
    return False


class BetaRdapVerifier:
    def __init__(self, cor: CorrelatorConfig, user_agent: str) -> None:
        self.cor = cor
        self.user_agent = user_agent
        self._map: dict[str, str] | None = None

    @staticmethod
    def _parse_bootstrap(payload: dict[str, Any]) -> dict[str, str]:
        out: dict[str, str] = {}
        for entry in payload.get("services", []):
            if not isinstance(entry, list) or len(entry) < 2 or not entry[1]:
                continue
            base = str(entry[1][0]).rstrip("/") + "/"
            for tld in entry[0]:
                out[str(tld).lower().lstrip(".")] = base
        return out

    def _load_map(self) -> dict[str, str]:
        if self._map is not None:
            return self._map
        cp = Path(self.cor.rdap_cache_path)
        if cp.exists() and (time.time() - cp.stat().st_mtime) <= self.cor.rdap_cache_ttl_s:
            try:
                self._map = self._parse_bootstrap(json.loads(cp.read_text(encoding="utf-8")))
                return self._map
            except Exception:
                pass
        req = urllib.request.Request(self.cor.iana_bootstrap_url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(req, timeout=self.cor.timeout_s) as resp:
            raw = resp.read()
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_bytes(raw)
        self._map = self._parse_bootstrap(json.loads(raw.decode("utf-8", "ignore")))
        return self._map

    def check(self, domain: str) -> dict[str, Any]:
        dom = domain.lower().strip(".")
        tld = dom.rsplit(".", 1)[-1] if "." in dom else ""
        extra = {k.lower().lstrip("."): v.rstrip("/") + "/" for k, v in self.cor.extra_rdap_servers.items()}
        server = extra.get(tld) or self._load_map().get(tld)
        if not server:
            return {"status": "unsupported_tld", "available": False, "label": "UNSUPPORTED_TLD"}
        req = urllib.request.Request(f"{server}domain/{dom}", headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=self.cor.timeout_s) as resp:
                return {"status": "taken", "available": False, "label": f"TAKEN({resp.status})"}
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {"status": "available", "available": True, "label": "AVAILABLE(404)"}
            return {"status": "error", "available": False, "label": f"ERR({exc.code})"}
        except Exception:
            return {"status": "error", "available": False, "label": "ERR(NET)"}


def lookup_directory_record(
    iso3: str,
    registry_id: str,
    cor: CorrelatorConfig,
    user_agent: str,
) -> dict[str, Any] | None:
    if not cor.enable_directory_check or not cor.directory_base_url or not registry_id:
        return None
    hdrs = {"User-Agent": user_agent, "Accept": "application/json"}
    token = os.environ.get(cor.token_env, "").strip()
    if token:
        hdrs[cor.auth_header] = f"{cor.auth_prefix}{token}" if cor.auth_prefix else token
    if cor.origin:
        hdrs["Origin"] = cor.origin
    if cor.referer:
        hdrs["Referer"] = cor.referer

    sep = "&" if "?" in cor.directory_base_url else "?"
    url = f"{cor.directory_base_url}{sep}{urllib.parse.urlencode({'countryCode': iso3, 'registryId': registry_id})}"
    proxy_url = os.environ.get(cor.proxy_env, "").strip()
    for attempt in range(4):
        req = urllib.request.Request(url, headers=hdrs)
        try:
            if proxy_url:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
                ctx_mgr = opener.open(req, timeout=cor.timeout_s)
            else:
                ctx_mgr = urllib.request.urlopen(req, timeout=cor.timeout_s)
            with ctx_mgr as resp:
                rows = json.loads(resp.read().decode("utf-8", "ignore")).get("data") or []
            return rows[0] if rows else None
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 3:
                time.sleep(min(10.0, (2 ** attempt) * 1.0))
                continue
            return None
        except Exception:
            return None
    return None


def scan_contact_source(
    cfg: BetaConfig,
    db: sqlite3.Connection,
    *,
    dry_run: bool = False,
    use_system_dns: bool = True,
) -> dict[str, Any]:
    src = build_contact_source(cfg.source, user_agent=cfg.user_agent)
    rdap = BetaRdapVerifier(cfg.correlator, cfg.user_agent)

    raw_count = 0
    candidates: list[dict[str, Any]] = []
    for raw in src.iter_contacts():
        raw_count += 1
        dom = extract_custom_email_domain(raw.get("email"))
        if not dom:
            continue
        # Only track website-absent records for Track Beta unless explicitly empty/none
        if raw.get("website"):
            continue
        candidates.append({
            "iso3": raw["iso3"],
            "country": raw["country"],
            "registry_code": raw["registry_code"],
            "registry_id": raw["registry_id"],
            "name": raw["name"],
            "contact_email": raw["email"].strip().lower(),
            "email_domain": dom,
        })

    unique_doms = sorted({c["email_domain"] for c in candidates})
    dead_doms: set[str] = set()
    with ThreadPoolExecutor(max_workers=max(1, cfg.correlator.workers)) as pool:
        futs = {
            pool.submit(probe_dns_alive, dom, cfg.correlator, cfg.user_agent, use_system_dns=use_system_dns): dom
            for dom in unique_doms
        }
        for fut in as_completed(futs):
            dom = futs[fut]
            if not fut.result():
                dead_doms.add(dom)

    rdap_results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(4, cfg.correlator.workers))) as pool:
        futs = {pool.submit(rdap.check, dom): dom for dom in sorted(dead_doms)}
        for fut in as_completed(futs):
            dom = futs[fut]
            rdap_results[dom] = fut.result()

    hits: list[dict[str, Any]] = []
    unsupported_tld = 0
    for c in candidates:
        dom = c["email_domain"]
        if dom not in dead_doms:
            continue
        rd = rdap_results.get(dom, {})
        if rd.get("status") == "unsupported_tld":
            unsupported_tld += 1
            continue
        if not rd.get("available"):
            continue

        gs_id = None
        gs_web_null = True
        stage = "rdap_available"
        if cfg.correlator.enable_directory_check:
            match = lookup_directory_record(c["iso3"], c["registry_id"], cfg.correlator, cfg.user_agent)
            if match:
                if match.get("claimedAt"):
                    continue
                gs_id = match.get("id")
                gs_web_null = not bool(match.get("website"))
                if gs_web_null:
                    stage = "gs_matched_web_null"
            if cfg.correlator.request_delay_s > 0:
                time.sleep(cfg.correlator.request_delay_s)

        item = {
            **c,
            "gs_id": gs_id,
            "gs_website_null": gs_web_null,
            "rdap_status": rd["label"],
            "stage": stage,
        }
        hits.append(item)

    added = 0
    if not dry_run:
        for item in hits:
            if upsert_beta_contact(db, item):
                added += 1

    return {
        "source_kind": cfg.source.kind,
        "raw_records": raw_count,
        "custom_domain_website_null": len(candidates),
        "unique_custom_domains": len(unique_doms),
        "dns_dead_domains": len(dead_doms),
        "rdap_unsupported_tld": unsupported_tld,
        "verified_available_hits": len(hits),
        "added": added,
        "dry_run": dry_run,
        "hits": [
            {"iso3": h["iso3"], "registry_id": h["registry_id"], "email": h["contact_email"], "stage": h["stage"]}
            for h in hits
        ],
    }


def correlate_existing_contacts(
    cfg: BetaConfig,
    db: sqlite3.Connection,
    *,
    limit: int = 25,
) -> dict[str, Any]:
    """Backfill directory match (gs_id, gs_website_null) for unlinked beta_contacts."""
    if not cfg.correlator.directory_base_url:
        raise ValueError("correlator.directory_base_url is required for correlate")
    cor = CorrelatorConfig(
        enable_directory_check=True,
        directory_base_url=cfg.correlator.directory_base_url,
        auth_header=cfg.correlator.auth_header,
        auth_prefix=cfg.correlator.auth_prefix,
        token_env=cfg.correlator.token_env,
        proxy_env=cfg.correlator.proxy_env,
        origin=cfg.correlator.origin,
        referer=cfg.correlator.referer,
        request_delay_s=cfg.correlator.request_delay_s,
        timeout_s=cfg.correlator.timeout_s,
    )
    rows = db.execute(
        "SELECT id, iso3, registry_id, name, contact_email, stage FROM beta_contacts WHERE gs_id IS NULL ORDER BY id ASC LIMIT ?",
        (limit,),
    ).fetchall()
    matched = 0
    claimed_archived = 0
    missed = 0
    for r in rows:
        m = lookup_directory_record(r["iso3"], r["registry_id"], cor, cfg.user_agent)
        if not m:
            missed += 1
        elif m.get("claimedAt"):
            db.execute("UPDATE beta_contacts SET gs_id=?, stage='archived', notes='already_claimed' WHERE id=?", (m.get("id"), r["id"]))
            claimed_archived += 1
        else:
            web_null = 0 if m.get("website") else 1
            new_stage = "gs_matched_web_null" if web_null else r["stage"]
            db.execute(
                "UPDATE beta_contacts SET gs_id=?, gs_website_null=?, stage=? WHERE id=?",
                (m.get("id"), web_null, new_stage, r["id"]),
            )
            matched += 1
        db.commit()
        if cor.request_delay_s > 0:
            time.sleep(cor.request_delay_s)
    return {"checked": len(rows), "matched_unclaimed": matched, "archived_already_claimed": claimed_archived, "unmatched": missed}


def check_mailbox_dns(domain: str, doh_url: str = "https://dns.google/resolve", timeout_s: int = 8, user_agent: str = "ngo-mail-catch/0.2") -> dict[str, Any]:
    """Verify live MX, SPF, and DMARC records before capturing verification mail."""
    dom = domain.lower().strip(".")

    def q(name: str, rtype: str) -> list[str]:
        qs = urllib.parse.urlencode({"name": name, "type": rtype})
        req = urllib.request.Request(f"{doh_url}?{qs}", headers={"Accept": "application/dns-json", "User-Agent": user_agent})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                d = json.loads(resp.read().decode("utf-8", "ignore"))
            return [str(a.get("data", "")) for a in (d.get("Answer") or []) if a.get("data")]
        except Exception:
            return []

    mx = q(dom, "MX")
    txt = q(dom, "TXT")
    dmarc = q(f"_dmarc.{dom}", "TXT")
    spf = [t for t in txt if "v=spf1" in t.lower()]
    checks = {
        "mx_live": bool(mx),
        "spf_present": bool(spf),
        "dmarc_present": any("v=dmarc1" in t.lower() for t in dmarc),
    }
    return {"domain": dom, "ready": all(checks.values()), "checks": checks, "mx": mx, "spf": spf, "dmarc": dmarc}
