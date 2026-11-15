# Architecture and operational boundaries

## Configuration precedence

Configuration resolves as: `CLI flags > environment variables > TOML file > defaults`. Bridge paths, OData filters, CSV column mappings, and directory URLs live strictly in config or environment variables.

## Ingestion and verification (`sources` + `correlator`)

1. **Source adapters (`sources/adapters.py`)**: `odata_json`, `csv_file`, `json_file`, `text_regex`, plus third-party `ngo_mail_catch.sources` entry points.
2. **Freemail filter (`parser/extract.py`)**: Excludes consumer/ISP domains and isolates custom organizational email domains on `website: null` records.
3. **DNS & Authoritative RDAP (`correlator/verifier.py`)**: Verifies MX/A/NS inactivity via system DNS + optional DoH, then queries authoritative TLD RDAP servers via IANA bootstrap (`unsupported_tld` is never treated as available).
4. **Inbox verification (`inbox/poller.py`)**: Parses raw `.eml` files or polls read-only IMAP SSL mailboxes (`imaplib`) with `PEEK` flags to extract verification URLs and tokens into SQLite without modifying remote messages.
5. **Cross-track dashboard (`core/store.py`)**: Syncs `ngo-recon-bridge/v1` snapshots from `ngo-domain-claim` and detects cross-track domain overlap.
