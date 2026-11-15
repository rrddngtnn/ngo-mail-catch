# ngo-mail-catch (Track Beta & Unified Dashboard)

Configurable public charity registry contact-domain OSINT auditor (for `website`-absent records) and unified cross-track dashboard paired with [`ngo-domain-claim`](https://github.com/rrddngtnn/ngo-domain-claim) via `ngo-recon-bridge/v1`.

## Install

```bash
python3 -m pip install -e ".[dev]"
cp beta.example.toml private/beta.toml
```

## Quickstart

```bash
export PYTHONPATH=packages

# Inspect resolved config and SQLite readiness
python3 -m ngo_mail_catch.cli --config private/beta.toml doctor

# Run automated contact-domain scan (OData/CSV/Text -> DNS -> IANA RDAP -> SQLite)
python3 -m ngo_mail_catch.cli --config private/beta.toml scan-source

# Correlate contacts with configured directory & verify live MX/SPF/DMARC readiness
python3 -m ngo_mail_catch.cli --config private/beta.toml correlate --limit 25
python3 -m ngo_mail_catch.cli --config private/beta.toml rank --limit 20
python3 -m ngo_mail_catch.cli --config private/beta.toml preflight --domain example.org

# Read-only inbox verification parsing (raw .eml or IMAP SSL polling)
python3 -m ngo_mail_catch.cli --config private/beta.toml inbox-ingest --file message.eml
python3 -m ngo_mail_catch.cli --config private/beta.toml inbox-poll --limit 25
python3 -m ngo_mail_catch.cli --config private/beta.toml inbox-links

# Sync Track Alpha bridge snapshot and render combined dashboard
python3 -m ngo_mail_catch.cli --config private/beta.toml sync-alpha
python3 -m ngo_mail_catch.cli --config private/beta.toml unified-dashboard
```

## Tests & hygiene gate

```bash
make test
make hygiene
```
