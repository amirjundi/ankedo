"""Pairing-code enrolment.

Exists because of how the credential actually reaches the machine: an admin tells an
operator over WhatsApp or a phone call. Sending the long-lived key that way leaves a
never-expiring credential in a chat log on two phones and a backup. A pairing code is
single-use and short-lived, so the log holds nothing of value afterwards.
"""
from __future__ import annotations

import httpx
import pytest

from src.ettok.enrollment import EnrollmentError, normalise_code, redeem

BASE = "https://ettok.test/api/hermes/"


# ------------------------------------------------------------ code handling


@pytest.mark.parametrize(
    "typed,expected",
    [
        ("ANK4-7K2M-9XQP", "ANK4-7K2M-9XQP"),
        ("ank4-7k2m-9xqp", "ANK4-7K2M-9XQP"),      # lowercase from a phone keyboard
        ("ANK4 7K2M 9XQP", "ANK4-7K2M-9XQP"),      # spaces instead of hyphens
        ("ank47k2m9xqp", "ANK4-7K2M-9XQP"),        # hyphens omitted entirely
    ],
)
def test_accepts_what_a_human_actually_types(typed, expected):
    """A code read aloud gets typed inconsistently. Rejecting on formatting is hostile."""
    assert normalise_code(typed) == expected


def test_an_empty_code_is_rejected():
    with pytest.raises(EnrollmentError, match="no code"):
        normalise_code("   ")


# ----------------------------------------------------------------- redeeming


def _patch(monkeypatch, response):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: response)


def test_a_valid_code_returns_the_key(monkeypatch):
    _patch(monkeypatch, httpx.Response(
        200,
        json={"agent_key": "long-lived-key-xyz", "agent_id": "ankedo-desk-01"},
        request=httpx.Request("POST", BASE),
    ))
    result = redeem(BASE, "ANK4-7K2M", "ankedo-desk-01")

    assert result["agent_key"] == "long-lived-key-xyz"


@pytest.mark.parametrize("status", [400, 401, 403, 410])
def test_expired_or_used_codes_are_rejected_clearly(monkeypatch, status):
    """A typo is worth retrying; an expired code needs a new one. Different actions."""
    _patch(monkeypatch, httpx.Response(
        status, json={"detail": "code expired"}, request=httpx.Request("POST", BASE)
    ))
    with pytest.raises(EnrollmentError, match="expired"):
        redeem(BASE, "ANK4-7K2M", "agent")


def test_a_platform_without_pairing_says_so(monkeypatch):
    """Points the operator at the fallback rather than leaving them stuck."""
    _patch(monkeypatch, httpx.Response(404, request=httpx.Request("POST", BASE)))

    with pytest.raises(EnrollmentError, match="does not support pairing"):
        redeem(BASE, "ANK4-7K2M", "agent")


def test_an_unreachable_platform_is_distinguished(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(EnrollmentError, match="could not reach"):
        redeem(BASE, "ANK4-7K2M", "agent")


def test_a_response_without_a_key_is_an_error(monkeypatch):
    _patch(monkeypatch, httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", BASE)))

    with pytest.raises(EnrollmentError, match="no agent key"):
        redeem(BASE, "ANK4-7K2M", "agent")


def test_a_pairing_code_is_never_sent_over_plaintext(monkeypatch):
    """The code IS the credential during its window — it cannot cross the wire in clear."""
    with pytest.raises(EnrollmentError, match="plaintext"):
        redeem("http://ettok.test/api/hermes/", "ANK4-7K2M", "agent")


def test_localhost_plaintext_is_allowed_for_development(monkeypatch):
    _patch(monkeypatch, httpx.Response(
        200, json={"agent_key": "k"}, request=httpx.Request("POST", "http://127.0.0.1/")
    ))
    assert redeem("http://127.0.0.1:8000/api/hermes/", "ANK4-7K2M", "agent")["agent_key"] == "k"
