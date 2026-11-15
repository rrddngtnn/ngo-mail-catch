"""Layered configuration loader for Track Beta: CLI > Env > TOML > Defaults."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class BetaSourceConfig:
    kind: str = "json_file"
    url: str = ""
    file_path: str = ""
    iso3: str = "UNK"
    country: str = "Unknown"
    registry_code: str = "REG"
    odata_filter: str = ""
    odata_select: str = ""
    page_size: int = 200
    max_pages: int = 2
    field_map: dict[str, str] = field(default_factory=dict)


@dataclass
class CorrelatorConfig:
    enable_directory_check: bool = False
    directory_base_url: str = ""
    auth_header: str = "Authorization"
    auth_prefix: str = ""
    token_env: str = "DIRECTORY_API_TOKEN"
    proxy_env: str = "BETA_PROXY_URL"
    origin: str = ""
    referer: str = ""
    request_delay_s: float = 0.5
    doh_url: str = "https://dns.google/resolve"
    enable_doh: bool = True
    iana_bootstrap_url: str = "https://data.iana.org/rdap/dns.json"
    rdap_cache_path: str = "private/iana_rdap_dns.json"
    rdap_cache_ttl_s: int = 86400 * 7
    extra_rdap_servers: dict[str, str] = field(default_factory=dict)
    timeout_s: int = 10
    workers: int = 8


@dataclass
class BetaConfig:
    db_path: str = "private/beta_contacts.sqlite"
    bridge_file: str = "private/alpha_bridge.json"
    alpha_repo_path: str = ""
    user_agent: str = "ngo-mail-catch/0.2 (public-registry-research)"
    source: BetaSourceConfig = field(default_factory=BetaSourceConfig)
    correlator: CorrelatorConfig = field(default_factory=CorrelatorConfig)


def _load_toml(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")
    return tomllib.loads(p.read_text(encoding="utf-8"))


def load_config(
    config_path: str | Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> BetaConfig:
    cfg_file = config_path or os.environ.get("BETA_CONFIG")
    raw = _load_toml(cfg_file)
    cli = cli_overrides or {}

    core_sec = raw.get("core", {})
    src_sec = raw.get("source", {})
    cor_sec = raw.get("correlator", {})

    db_path = (
        cli.get("db")
        or os.environ.get("BETA_RECON_DB")
        or core_sec.get("db_path")
        or "private/beta_contacts.sqlite"
    )
    bridge_file = (
        cli.get("bridge_file")
        or os.environ.get("BETA_BRIDGE_FILE")
        or core_sec.get("bridge_file")
        or "private/alpha_bridge.json"
    )
    alpha_repo_path = (
        os.environ.get("ALPHA_REPO_PATH")
        or core_sec.get("alpha_repo_path")
        or ""
    )
    user_agent = (
        os.environ.get("BETA_USER_AGENT")
        or core_sec.get("user_agent")
        or "ngo-mail-catch/0.2 (public-registry-research)"
    )

    src = BetaSourceConfig(
        kind=cli.get("source_kind") or os.environ.get("BETA_SOURCE_KIND") or src_sec.get("kind", "json_file"),
        url=cli.get("url") or os.environ.get("BETA_SOURCE_URL") or src_sec.get("url", ""),
        file_path=cli.get("file") or src_sec.get("file_path", ""),
        iso3=str(cli.get("iso3") or src_sec.get("iso3", "UNK")).upper(),
        country=str(cli.get("country") or src_sec.get("country", "Unknown")),
        registry_code=str(cli.get("registry_code") or src_sec.get("registry_code", "REG")),
        odata_filter=src_sec.get("odata_filter", ""),
        odata_select=src_sec.get("odata_select", ""),
        page_size=int(cli.get("page_size") or src_sec.get("page_size", 200)),
        max_pages=int(cli.get("max_pages") or src_sec.get("max_pages", 2)),
        field_map=dict(src_sec.get("field_map", {})),
    )

    cor = CorrelatorConfig(
        enable_directory_check=bool(
            cli.get("enable_directory_check")
            if cli.get("enable_directory_check") is not None
            else cor_sec.get("enable_directory_check", False)
        ),
        directory_base_url=os.environ.get("DIRECTORY_BASE_URL") or cor_sec.get("directory_base_url", ""),
        auth_header=cor_sec.get("auth_header", "Authorization"),
        auth_prefix=cor_sec.get("auth_prefix", ""),
        token_env=cor_sec.get("token_env", "DIRECTORY_API_TOKEN"),
        proxy_env=cor_sec.get("proxy_env", "BETA_PROXY_URL"),
        origin=os.environ.get("DIRECTORY_ORIGIN") or cor_sec.get("origin", ""),
        referer=os.environ.get("DIRECTORY_REFERER") or cor_sec.get("referer", ""),
        request_delay_s=float(cor_sec.get("request_delay_s", 0.5)),
        doh_url=os.environ.get("BETA_DOH_URL") or cor_sec.get("doh_url", "https://dns.google/resolve"),
        enable_doh=bool(cli.get("enable_doh") if cli.get("enable_doh") is not None else cor_sec.get("enable_doh", True)),
        iana_bootstrap_url=os.environ.get("BETA_RDAP_BOOTSTRAP_URL") or cor_sec.get("iana_bootstrap_url", "https://data.iana.org/rdap/dns.json"),
        rdap_cache_path=cor_sec.get("rdap_cache_path", "private/iana_rdap_dns.json"),
        rdap_cache_ttl_s=int(cor_sec.get("rdap_cache_ttl_s", 86400 * 7)),
        extra_rdap_servers=dict(cor_sec.get("extra_rdap_servers", {})),
        timeout_s=int(cli.get("timeout") or cor_sec.get("timeout_s", 10)),
        workers=int(cli.get("workers") or cor_sec.get("workers", 8)),
    )

    return BetaConfig(
        db_path=db_path,
        bridge_file=bridge_file,
        alpha_repo_path=alpha_repo_path,
        user_agent=user_agent,
        source=src,
        correlator=cor,
    )
