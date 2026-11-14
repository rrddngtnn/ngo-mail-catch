from pathlib import Path
from ngo_mail_catch.core.store import (
    open_db,
    sync_alpha_bridge,
    unified_dashboard,
    upsert_beta_contact,
)
from ngo_mail_catch.parser.extract import extract_custom_email_domain, filter_registry_contacts


def test_freemail_filtering():
    assert extract_custom_email_domain("info@gmail.com") is None
    assert extract_custom_email_domain("contact@yahoo.co.uk") is None
    assert extract_custom_email_domain("admin@ www.nadobd.org ") is None
    assert extract_custom_email_domain("director@ruralhealth-bd.org") == "ruralhealth-bd.org"


def test_cross_track_correlation(tmp_path: Path):
    db = open_db(tmp_path / "beta.sqlite")
    sync_alpha_bridge(
        db,
        {
            "schema": "ngo-recon-bridge/v1",
            "records": [
                {
                    "gs_id": "organisation_01",
                    "iso3": "MWI",
                    "country": "Malawi",
                    "name": "Jallie Care Foundation",
                    "registry_code": "NGORA",
                    "registry_id": "NGO/L/23/020",
                    "root_domain": "jalliecare.org",
                    "confidence": "strong",
                    "stage": "wayback_checked",
                    "score": 95,
                    "wayback_snapshots": 3,
                }
            ],
        },
    )
    filtered = filter_registry_contacts([
        {
            "iso3": "BGD",
            "country": "Bangladesh",
            "registry_code": "NAB",
            "registry_id": "FD-9912",
            "name": "Delta Rural Welfare Sangstha",
            "email": "office@deltaruralbd.org",
            "rdap_status": "AVAILABLE(404)",
        },
        {
            "iso3": "BGD",
            "country": "Bangladesh",
            "registry_code": "NAB",
            "registry_id": "FD-1001",
            "name": "Generic Gmail Org",
            "email": "someorg@gmail.com",
        },
    ])
    assert len(filtered) == 1
    upsert_beta_contact(db, filtered[0])

    dash = unified_dashboard(db)
    assert dash["track_alpha_website_surface"]["total_synced"] == 1
    assert dash["track_beta_contact_surface"]["total_contacts"] == 1
    assert len(dash["track_beta_contact_surface"]["exclusive_candidates"]) == 1
