export PYTHONPATH := packages

.PHONY: test hygiene doctor status scan-source correlate rank preflight sync-alpha unified

test:
	python3 -m pytest

hygiene:
	python3 scripts/public-hygiene-gate.py --repo-root .

doctor:
	python3 -m ngo_mail_catch.cli doctor

status:
	python3 -m ngo_mail_catch.cli status

scan-source:
	python3 -m ngo_mail_catch.cli scan-source

correlate:
	python3 -m ngo_mail_catch.cli correlate --limit $${N:-25}

rank:
	python3 -m ngo_mail_catch.cli rank --limit $${N:-20}

preflight:
	python3 -m ngo_mail_catch.cli preflight --domain $(DOMAIN)

sync-alpha:
	python3 -m ngo_mail_catch.cli sync-alpha

unified:
	python3 -m ngo_mail_catch.cli unified-dashboard

inbox-poll:
	python3 -m ngo_mail_catch.cli inbox-poll

inbox-links:
	python3 -m ngo_mail_catch.cli inbox-links
