# ngo-mail-catch (Track Beta & Unified Dashboard)

Public charity registry contact-domain parser (`website: null` surface) and unified cross-track correlation dashboard paired with [`ngo-domain-claim`](../ngo-domain-claim) (Track Alpha).

## Architecture

1. **Track Alpha (`../ngo-domain-claim`)**: Indexes directory records that publish an explicit `website` URL, scores humanitarian LMIC candidates, checks Wayback CDX history, and exports `private/alpha_bridge.json`.
2. **Track Beta (`ngo-mail-catch`)**: Parses public regulator contact extracts (filtering out freemail providers like Gmail/Yahoo/Outlook), isolates custom contact domains on records where `website` is null, and syncs `alpha_bridge.json` to prevent cross-track collision and monitor both pipelines from one command.

## Quickstart

```bash
export PYTHONPATH=packages

# Sync Track Alpha bridge snapshot
python3 -m ngo_mail_catch.cli sync-alpha --bridge-file ../ngo-domain-claim/private/alpha_bridge.json

# Render combined Alpha + Beta progress dashboard
python3 -m ngo_mail_catch.cli unified-dashboard
```
