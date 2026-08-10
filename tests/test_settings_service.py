"""
Unit tests for the settings_service persistence layer — the code that backs
the admin "הגדרות" panel. These pin the save/load contract so future changes
(masking scheme, clear-protocol, coercion rules, .env writer) can't silently
break the outer boundary:

  * save()        — persists to .env, mirrors into the live Settings object,
                    honors the __clear protocol, ignores unknown keys
  * get_current() — returns every editable key, masking secrets only
  * _mask()       — never leaks a secret's body
  * run_test()    — missing-key fast paths fail cleanly WITHOUT network

The real .env is NEVER touched: every test redirects ENV_FILE to a tmp file.
"""
import pytest

from app.core.config import settings
from app.services import settings_service


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    """Point the service at a throwaway .env file for the duration."""
    path = tmp_path / ".env"
    monkeypatch.setattr(settings_service, "ENV_FILE", path)
    return path


# ---------------------------------------------------------------------------
# _mask
# ---------------------------------------------------------------------------

def test_mask_empty_is_empty():
    assert settings_service._mask("") == ""
    assert settings_service._mask(None) == ""


def test_mask_short_value_fully_hidden():
    assert settings_service._mask("abcd") == "••••"


def test_mask_long_value_hides_body():
    masked = settings_service._mask("super-secret-key-123")
    # Only the first 4 and last 3 chars survive; the middle is replaced.
    assert masked == "supe" + "••••••" + "123"
    assert "secret" not in masked


# ---------------------------------------------------------------------------
# get_current
# ---------------------------------------------------------------------------

def test_get_current_covers_every_editable_key():
    """The form must always expose the full whitelist — no key can silently
    disappear from the settings page."""
    out = settings_service.get_current()
    assert set(out) == set(settings_service.EDITABLE.values())


def test_get_current_masks_secrets(monkeypatch):
    monkeypatch.setattr(settings, "google_api_key", "sk-real-secret-xyz-123")
    out = settings_service.get_current()
    masked = out["GOOGLE_API_KEY"]
    assert masked != "sk-real-secret-xyz-123"
    assert "sk-real-secret" not in masked
    assert "xyz" not in masked


def test_get_current_plain_for_non_secret(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com")
    assert settings_service.get_current()["SMTP_HOST"] == "smtp.gmail.com"


# ---------------------------------------------------------------------------
# save — persistence
# ---------------------------------------------------------------------------

def test_save_writes_new_key(env_file):
    changed = settings_service.save({"GOOGLE_API_KEY": "new-key"})
    assert changed == ["GOOGLE_API_KEY"]
    assert "GOOGLE_API_KEY=new-key" in env_file.read_text()


def test_save_updates_existing_and_preserves_comments(env_file):
    env_file.write_text("# keep me\nSITE_URL=http://old.example\n\nSMTP_PORT=25\n")
    settings_service.save({"SITE_URL": "http://new.example", "SMTP_PORT": "587"})
    text = env_file.read_text()
    assert "# keep me" in text
    assert "SITE_URL=http://new.example" in text
    assert "SMTP_PORT=587" in text
    assert "http://old.example" not in text


def test_save_empty_without_clear_keeps_existing(env_file):
    env_file.write_text("GOOGLE_API_KEY=old-key\n")
    changed = settings_service.save({"GOOGLE_API_KEY": "   "})
    assert changed == []
    assert "GOOGLE_API_KEY=old-key" in env_file.read_text()


def test_save_empty_with_clear_flag_wipes(env_file):
    env_file.write_text("GOOGLE_API_KEY=old-key\n")
    changed = settings_service.save({"GOOGLE_API_KEY": "", "GOOGLE_API_KEY__clear": "1"})
    assert changed == ["GOOGLE_API_KEY"]
    assert "GOOGLE_API_KEY=\n" in env_file.read_text()


def test_save_ignores_unknown_keys(env_file):
    changed = settings_service.save({"NOT_A_REAL_KEY": "x", "HACKED": "y"})
    assert changed == []
    assert not env_file.exists()  # nothing worth writing -> file untouched


def test_save_strips_whitespace(env_file):
    settings_service.save({"SMTP_HOST": "  smtp.gmail.com  "})
    assert "SMTP_HOST=smtp.gmail.com" in env_file.read_text()


# ---------------------------------------------------------------------------
# save — live Settings mirror + coercion
# ---------------------------------------------------------------------------

def test_save_mirrors_into_live_settings(env_file, monkeypatch):
    monkeypatch.setattr(settings, "site_url", "http://old.example")
    settings_service.save({"SITE_URL": "http://new.example"})
    assert settings.site_url == "http://new.example"


def test_save_coerces_smtp_port_to_int(env_file, monkeypatch):
    monkeypatch.setattr(settings, "smtp_port", 587)
    settings_service.save({"SMTP_PORT": "465"})
    assert settings.smtp_port == 465


def test_save_invalid_smtp_port_falls_back(env_file, monkeypatch):
    monkeypatch.setattr(settings, "smtp_port", 587)
    settings_service.save({"SMTP_PORT": "abc"})
    assert settings.smtp_port == 587


# ---------------------------------------------------------------------------
# run_test — offline fast paths (no network, no valid key)
# ---------------------------------------------------------------------------

def test_run_test_missing_key_fails_fast_offline(monkeypatch):
    """Every service test with empty creds must return (False, msg) without
    touching the network — the real .env may hold a key in CI, so force-empty
    the settings attrs these branches read."""
    for attr in [
        "google_api_key", "smtp_host", "smtp_user", "instagram_access_token",
        "ebay_app_id", "aliexpress_app_key", "amazon_partner_tag",
        "cj_api_token", "rakuten_client_id", "awin_api_token",
        "telegram_bot_token",
    ]:
        monkeypatch.setattr(settings, attr, "", raising=False)
    for service in settings_service.TEST_FIELDS:
        ok, msg = settings_service.run_test(service, {})
        assert ok is False
        assert msg and len(msg) > 5


def test_run_test_unknown_service_rejected():
    ok, msg = settings_service.run_test("not-a-service", {})
    assert ok is False
    assert "לא ידוע" in msg


def test_test_fields_map_to_editable_keys():
    """Every field a service test reads must be a real editable env var —
    otherwise the test button could read config the admin can't set."""
    editable = set(settings_service.EDITABLE.values())
    for service, fields in settings_service.TEST_FIELDS.items():
        for field in fields:
            assert field in editable, f"{service}: {field} not editable"
