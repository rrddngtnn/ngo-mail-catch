"""Pluggable registry contact-domain adapters: OData, CSV, JSON, extracted text."""
from __future__ import annotations

import csv
from importlib import metadata
import json
from pathlib import Path
import re
from typing import Any, Iterator, Protocol
import urllib.parse
import urllib.request

from ngo_mail_catch.core.config import BetaSourceConfig

EMAIL_FINDER_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")


class ContactSource(Protocol):
    def iter_contacts(self) -> Iterator[dict[str, Any]]: ...


def _map_fields(raw: dict[str, Any], cfg: BetaSourceConfig) -> dict[str, Any]:
    fmap = cfg.field_map

    def pick(canonical: str, *fallbacks: str) -> Any:
        if canonical in fmap and fmap[canonical] in raw:
            return raw.get(fmap[canonical])
        for k in (canonical, *fallbacks):
            if k in raw:
                return raw.get(k)
        return None

    return {
        "iso3": str(pick("iso3", "countryCode") or cfg.iso3).upper(),
        "country": str(pick("country") or cfg.country),
        "registry_code": str(pick("registry_code", "regcode") or cfg.registry_code),
        "registry_id": str(pick("registry_id", "registryId", "CharityRegistrationNumber") or ""),
        "name": str(pick("name", "Name") or "").strip(),
        "email": str(pick("email", "contact_email", "EMailAddress1", "CharityEmailAddress") or "").strip(),
        "website": str(pick("website", "WebSiteURL") or "").strip(),
    }


class ODataJsonSource:
    def __init__(self, cfg: BetaSourceConfig, user_agent: str = "ngo-mail-catch/0.2") -> None:
        self.cfg = cfg
        self.user_agent = user_agent

    def iter_contacts(self) -> Iterator[dict[str, Any]]:
        if not self.cfg.url:
            raise ValueError("source.url is required for odata_json source")
        for page in range(self.cfg.max_pages):
            params: dict[str, Any] = {
                "$top": self.cfg.page_size,
                "$skip": page * self.cfg.page_size,
                "$format": "json",
            }
            if self.cfg.odata_filter:
                params["$filter"] = self.cfg.odata_filter
            if self.cfg.odata_select:
                params["$select"] = self.cfg.odata_select
            sep = "&" if "?" in self.cfg.url else "?"
            url = f"{self.cfg.url}{sep}{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8", "ignore"))
            rows = payload.get("d") or payload.get("value") or []
            if not rows:
                break
            for row in rows:
                yield _map_fields(row, self.cfg)
            if len(rows) < self.cfg.page_size:
                break


class CsvContactSource:
    def __init__(self, cfg: BetaSourceConfig) -> None:
        self.cfg = cfg

    def iter_contacts(self) -> Iterator[dict[str, Any]]:
        if not self.cfg.file_path:
            raise ValueError("source.file_path is required for csv_file source")
        with Path(self.cfg.file_path).open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                yield _map_fields(dict(row), self.cfg)


class JsonContactSource:
    def __init__(self, cfg: BetaSourceConfig) -> None:
        self.cfg = cfg

    def iter_contacts(self) -> Iterator[dict[str, Any]]:
        if not self.cfg.file_path:
            raise ValueError("source.file_path is required for json_file source")
        items = json.loads(Path(self.cfg.file_path).read_text(encoding="utf-8"))
        for row in items:
            yield _map_fields(row, self.cfg)


class TextRegexSource:
    """Scan plain-text registry extracts for custom emails and nearby registration context."""

    def __init__(self, cfg: BetaSourceConfig) -> None:
        self.cfg = cfg

    def iter_contacts(self) -> Iterator[dict[str, Any]]:
        if not self.cfg.file_path:
            raise ValueError("source.file_path is required for text_regex source")
        lines = Path(self.cfg.file_path).read_text(encoding="utf-8", errors="ignore").splitlines()
        seen: set[str] = set()
        for idx, raw_line in enumerate(lines):
            cleaned = re.sub(r"^\d+\|", "", raw_line)
            for m in EMAIL_FINDER_RE.finditer(cleaned):
                em = m.group(0).lower().strip(".")
                if em in seen:
                    continue
                seen.add(em)
                win = " ".join(re.sub(r"^\d+\|", "", l) for l in lines[max(0, idx - 2):min(len(lines), idx + 3)])
                reg_m = re.findall(r"(?:\||\s)(\d{4,7})(?:\||\s)+\d{2}-[A-Za-z]{3}-\d{2}", win)
                yield {
                    "iso3": self.cfg.iso3,
                    "country": self.cfg.country,
                    "registry_code": self.cfg.registry_code,
                    "registry_id": reg_m[0] if reg_m else f"LINE-{idx + 1}",
                    "name": re.sub(r"\s+", " ", win)[:80],
                    "email": em,
                    "website": "",
                }


def build_contact_source(cfg: BetaSourceConfig, user_agent: str = "ngo-mail-catch/0.2") -> ContactSource:
    eps = {ep.name: ep for ep in metadata.entry_points(group="ngo_mail_catch.sources")}
    if cfg.kind in eps:
        return eps[cfg.kind].load()(cfg, user_agent=user_agent)
    if cfg.kind == "odata_json":
        return ODataJsonSource(cfg, user_agent=user_agent)
    if cfg.kind == "csv_file":
        return CsvContactSource(cfg)
    if cfg.kind == "json_file":
        return JsonContactSource(cfg)
    if cfg.kind == "text_regex":
        return TextRegexSource(cfg)
    raise ValueError(f"Unsupported contact source kind: {cfg.kind}")
