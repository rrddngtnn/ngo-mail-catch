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
