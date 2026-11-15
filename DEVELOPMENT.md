# Development

Work from the repository root with Python 3.10+. Keep all registry parsers and correlators config-driven without hardcoded URLs or tokens.

## Local checks

```bash
export PYTHONPATH=packages
python3 -m pytest
python3 scripts/public-hygiene-gate.py --repo-root .
```

All automated tests must execute 100% offline using local HTTP mock servers and temporary SQLite databases. Use Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`).
