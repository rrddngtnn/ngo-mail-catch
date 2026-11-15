# Security policy

`ngo-mail-catch` parses public charity registry extracts, checks DNS/RDAP status, and correlates cross-track research state in local SQLite.

## Reporting a vulnerability

Report security issues privately via GitHub Security Advisories or the maintainer profile contact rather than public issues.

## Handling of secrets and private state

- `private/`, `*.sqlite`, `*.db`, `*.csv`, and `.env` are gitignored by default.
- Never commit live API tokens, raw registry dumps, or operational notes.
- Run `python3 scripts/public-hygiene-gate.py --repo-root .` before pushing to any public remote.
