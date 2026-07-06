"""Unit tests for the SMTP notification module (header shaping, no real SMTP)."""

from unittest.mock import patch

import pytest

from api.services.notifications import email as email_mod

_BASE_ENV = {
    "SMTP_HOST": "smtp.test",
    "SMTP_USER": "apikey",
    "SMTP_PASSWORD": "secret",
    "SMTP_FROM": "noreply@auto4you.in",
}


def test_smtp_config_plain_from_when_no_name(monkeypatch):
    for k, v in _BASE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SMTP_FROM_NAME", raising=False)
    monkeypatch.delenv("SMTP_REPLY_TO", raising=False)

    cfg = email_mod._smtp_config()
    assert cfg["from_header"] == "noreply@auto4you.in"
    assert cfg["reply_to"] is None


def test_smtp_config_formats_display_name_and_reply_to(monkeypatch):
    for k, v in _BASE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SMTP_FROM_NAME", "Hardik from Auto4You")
    monkeypatch.setenv("SMTP_REPLY_TO", "owner@auto4you.in")

    cfg = email_mod._smtp_config()
    assert cfg["from_header"] == "Hardik from Auto4You <noreply@auto4you.in>"
    assert cfg["reply_to"] == "owner@auto4you.in"
    # Envelope/from address itself stays bare.
    assert cfg["from_addr"] == "noreply@auto4you.in"


@pytest.mark.asyncio
async def test_send_email_sets_from_name_and_default_reply_to(monkeypatch):
    for k, v in _BASE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SMTP_FROM_NAME", "Hardik from Auto4You")
    monkeypatch.setenv("SMTP_REPLY_TO", "owner@auto4you.in")

    sent = {}

    def fake_deliver(cfg, msg):
        sent["msg"] = msg

    with patch.object(email_mod, "_deliver", new=fake_deliver):
        ok = await email_mod.send_email("client@example.com", "Subj", "Body")

    assert ok is True
    msg = sent["msg"]
    assert msg["From"] == "Hardik from Auto4You <noreply@auto4you.in>"
    assert msg["Reply-To"] == "owner@auto4you.in"


@pytest.mark.asyncio
async def test_send_email_explicit_reply_to_wins(monkeypatch):
    for k, v in _BASE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SMTP_REPLY_TO", "owner@auto4you.in")

    sent = {}

    def fake_deliver(cfg, msg):
        sent["msg"] = msg

    with patch.object(email_mod, "_deliver", new=fake_deliver):
        ok = await email_mod.send_email(
            "client@example.com", "Subj", "Body", reply_to="lead@example.com"
        )

    assert ok is True
    assert sent["msg"]["Reply-To"] == "lead@example.com"


def test_lead_message_uses_from_header(monkeypatch):
    for k, v in _BASE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SMTP_FROM_NAME", "Auto4You")

    cfg = email_mod._smtp_config()
    msg = email_mod._build_message(
        "hire_expert", {"email": "lead@example.com"}, cfg, "owner@auto4you.in"
    )
    assert msg["From"] == "Auto4You <noreply@auto4you.in>"
    # Lead emails keep the lead's address as Reply-To.
    assert msg["Reply-To"] == "lead@example.com"
