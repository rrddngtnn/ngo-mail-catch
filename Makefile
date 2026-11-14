export PYTHONPATH := packages

.PHONY: test hygiene doctor status unified

test:
	python3 -m pytest

hygiene:
	python3 scripts/public-hygiene-gate.py --repo-root .

doctor:
	python3 -m ngo_mail_catch.cli doctor

status:
	python3 -m ngo_mail_catch.cli status

unified:
	python3 -m ngo_mail_catch.cli unified-dashboard
