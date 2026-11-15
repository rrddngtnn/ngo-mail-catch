from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import threading

from ngo_mail_catch.core.config import load_config
from ngo_mail_catch.core.store import open_db
from ngo_mail_catch.correlator.verifier import scan_contact_source


class _BetaMockHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        return

    def do_GET(self):
        if self.path.startswith("/odata/Organisations"):
            body = json.dumps({
                "d": [
                    {
                        "Name": "Orphan Contact Trust",
                        "CharityRegistrationNumber": "REG-101",
                        "EMailAddress1": "info@orphancontact.org",
                        "WebSiteURL": None,
                    },
                    {
                        "Name": "Gmail Only Org",
                        "CharityRegistrationNumber": "REG-102",
                        "EMailAddress1": "org@gmail.com",
                        "WebSiteURL": None,
                    },
                    {
                        "Name": "Has Website Already Org",
                        "CharityRegistrationNumber": "REG-103",
                        "EMailAddress1": "info@hasweb.org",
                        "WebSiteURL": "https://hasweb.org",
                    },
                    {
                        "Name": "Unsupported ccTLD Contact",
                        "CharityRegistrationNumber": "REG-104",
                        "EMailAddress1": "admin@charity.co.nz",
                        "WebSiteURL": None,
                    },
                ]
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith("/v1/organisations"):
            body = json.dumps({
                "data": [
                    {
                        "id": "gs_org_101",
                        "name": "Orphan Contact Trust",
                        "website": None,
                        "claimedAt": None,
                    }
                ]
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/iana/dns.json":
            port = self.server.server_port
            body = json.dumps({
                "services": [[["org", "com"], [f"http://127.0.0.1:{port}/rdap/"]]]
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/rdap/domain/orphancontact.org":
            self.send_response(404)
            self.end_headers()
            return

        if self.path.startswith("/resolve"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"Status": 3}')
            return

        self.send_response(404)
        self.end_headers()


def test_beta_config_precedence(tmp_path: Path, monkeypatch):
    toml_file = tmp_path / "beta.toml"
    toml_file.write_text(
        """[core]
db_path = "from_toml.sqlite"
[source]
iso3 = "BGD"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("BETA_RECON_DB", "from_env.sqlite")
    cfg = load_config(toml_file, cli_overrides={"db": "from_cli.sqlite", "iso3": "nzl"})
    assert cfg.db_path == "from_cli.sqlite"
    assert cfg.source.iso3 == "NZL"


def test_beta_odata_and_text_regex_offline(tmp_path: Path):
    srv = HTTPServer(("127.0.0.1", 0), _BetaMockHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_port}"
        cfg_file = tmp_path / "beta.toml"
        cfg_file.write_text(
            f"""[core]
db_path = "{tmp_path / 'beta.sqlite'}"

[source]
kind = "odata_json"
url = "{base}/odata/Organisations"
iso3 = "BGD"
country = "Bangladesh"
registry_code = "NAB"
max_pages = 1

[correlator]
enable_directory_check = true
directory_base_url = "{base}/v1/organisations"
request_delay_s = 0.0
doh_url = "{base}/resolve"
enable_doh = true
iana_bootstrap_url = "{base}/iana/dns.json"
rdap_cache_path = "{tmp_path / 'iana.json'}"
""",
            encoding="utf-8",
        )
        cfg = load_config(cfg_file)
        db = open_db(cfg.db_path)
        rep = scan_contact_source(cfg, db, use_system_dns=False)
        assert rep["raw_records"] == 4
        assert rep["custom_domain_website_null"] == 2
        assert rep["rdap_unsupported_tld"] == 1
        assert rep["verified_available_hits"] == 1
        assert rep["hits"][0]["email"] == "info@orphancontact.org"
        assert rep["hits"][0]["stage"] == "gs_matched_web_null"

        # Also test text_regex source
        txt_file = tmp_path / "dump.txt"
        txt_file.write_text(
            "1|Test Society|Road 1|3548|28-Dec-25|email: info@orphancontact.org\n",
            encoding="utf-8",
        )
        cfg_txt = load_config(
            cfg_file,
            cli_overrides={"source_kind": "text_regex", "file": str(txt_file)},
        )
        rep2 = scan_contact_source(cfg_txt, db, dry_run=True, use_system_dns=False)
        assert rep2["verified_available_hits"] == 1
        assert rep2["hits"][0]["registry_id"] == "3548"
    finally:
        srv.shutdown()


def test_beta_correlate_backfill_stage_and_preflight(tmp_path: Path):
    from ngo_mail_catch.core.store import advance_beta_stage, rank_beta_contacts, upsert_beta_contact
    from ngo_mail_catch.correlator.verifier import check_mailbox_dns, correlate_existing_contacts

    srv = HTTPServer(("127.0.0.1", 0), _BetaMockHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_port}"
        cfg_file = tmp_path / "beta.toml"
        cfg_file.write_text(
            f"""[core]
db_path = "{tmp_path / 'beta.sqlite'}"

[correlator]
directory_base_url = "{base}/v1/organisations"
request_delay_s = 0.0
doh_url = "{base}/resolve"
""",
            encoding="utf-8",
        )
        cfg = load_config(cfg_file)
        db = open_db(cfg.db_path)
        upsert_beta_contact(db, {
            "iso3": "BGD",
            "country": "Bangladesh",
            "registry_code": "NAB",
            "registry_id": "3548",
            "name": "Orphan Contact Trust",
            "contact_email": "info@orphancontact.org",
            "email_domain": "orphancontact.org",
            "gs_id": None,
            "gs_website_null": True,
            "rdap_status": "AVAILABLE(404)",
        })
        res = correlate_existing_contacts(cfg, db, limit=10)
        assert res["matched_unclaimed"] == 1
        ranked = rank_beta_contacts(db, iso3="BGD")
        assert ranked[0]["gs_id"] == "gs_org_101"
        assert ranked[0]["stage"] == "gs_matched_web_null"

        updated = advance_beta_stage(db, "orphancontact.org", "mailbox_ready", notes="MX/SPF live")
        assert updated["stage"] == "mailbox_ready"

        dns_rep = check_mailbox_dns("orphancontact.org", doh_url=f"{base}/resolve")
        assert dns_rep["ready"] is False
    finally:
        srv.shutdown()


def test_mime_ingest_and_readonly_imap_poller_offline(tmp_path: Path):
    from ngo_mail_catch.core.store import list_verification_events, upsert_beta_contact
    from ngo_mail_catch.inbox.poller import ingest_mime_bytes, poll_imap_readonly

    db = open_db(tmp_path / "imap.sqlite")
    upsert_beta_contact(db, {
        "iso3": "BGD",
        "country": "Bangladesh",
        "registry_code": "NAB",
        "registry_id": "3548",
        "name": "ALO Society",
        "contact_email": "chairman@aloseocietybd.com",
        "email_domain": "aloseocietybd.com",
        "gs_id": "gs_3548",
        "gs_website_null": True,
        "rdap_status": "AVAILABLE(404)",
    })

    raw_eml = (chr(13) + chr(10)).join([
        "From: Goodstack Verification <no-reply@goodstack.io>",
        "To: chairman@aloseocietybd.com",
        "Subject: Confirm your association with ALO Society",
        "Message-ID: <msg-001@goodstack.io>",
        "Content-Type: text/html; charset=utf-8",
        "",
        '<html><body>Please confirm here: <a href="https://causes.goodstack.io/v1/agent-verifications/organisation-association?agentVerificationToken=tok_abc999">Confirm</a></body></html>',
    ]).encode("utf-8")

    class _FakeIMAP:
        def __init__(self, host, port):
            self.host = host
            self.port = port

        def login(self, user, pw):
            return ("OK", [b"logged in"])

        def select(self, mailbox, readonly=False):
            assert readonly is True
            return ("OK", [b"1"])

        def status(self, mailbox, q):
            return ("OK", [b"INBOX (UIDVALIDITY 778899)"])

        def uid(self, cmd, *args):
            if cmd == "search":
                return ("OK", [b"10 11"])
            if cmd == "fetch":
                uid_val = args[0]
                msg = raw_eml.replace(b"<msg-001@", f"<msg-{uid_val}@".encode())
                return ("OK", [(b"1 (BODY[] {200}", msg), b")"])
            return ("BAD", [])

        def logout(self):
            return ("BYE", [b"bye"])

    rep1 = poll_imap_readonly(
        db,
        host="127.0.0.1",
        port=1993,
        username="chairman@aloseocietybd.com",
        password="secret",
        imap_cls=_FakeIMAP,
    )
    assert rep1["fetched"] == 2
    assert rep1["matched_links"] == 2
    assert rep1["events"][0]["token"] == "tok_abc999"

    # Second poll with same UIDs should fetch 0 new messages thanks to checkpoint
    rep2 = poll_imap_readonly(
        db,
        host="127.0.0.1",
        port=1993,
        username="chairman@aloseocietybd.com",
        password="secret",
        imap_cls=_FakeIMAP,
    )
    assert rep2["fetched"] == 0

    events = list_verification_events(db, domain="aloseocietybd.com")
    assert len(events) == 2

    row = db.execute("SELECT stage FROM beta_contacts WHERE email_domain='aloseocietybd.com'").fetchone()
    assert row["stage"] == "verified_candidate"
