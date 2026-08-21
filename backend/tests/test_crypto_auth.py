"""Cookie encryption and session tokens — the two places a mistake leaks credentials."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from espn_dashboard import auth, crypto

KEY = crypto.generate_key()


def test_round_trip_preserves_the_cookie():
    token = crypto.encrypt("espn_s2_value", KEY, aad="{SWID}")
    assert crypto.decrypt(token, KEY, aad="{SWID}") == "espn_s2_value"


def test_ciphertext_never_contains_the_plaintext():
    token = crypto.encrypt("AEBsecretcookie", KEY, aad="{SWID}")
    assert "AEBsecretcookie" not in token
    assert "AEBsecretcookie" not in base64.b64decode(token).decode("latin-1")


def test_encrypting_twice_gives_different_ciphertext():
    """A fresh nonce each time; identical cookies must not be linkable."""
    assert crypto.encrypt("same", KEY, aad="x") != crypto.encrypt("same", KEY, aad="x")


def test_blob_cannot_be_replayed_under_another_swid():
    token = crypto.encrypt("cookie", KEY, aad="{OWNER}")
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(token, KEY, aad="{ATTACKER}")


def test_wrong_key_is_rejected():
    token = crypto.encrypt("cookie", KEY, aad="a")
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(token, crypto.generate_key(), aad="a")


def test_tampered_ciphertext_is_rejected():
    raw = bytearray(base64.b64decode(crypto.encrypt("cookie", KEY, aad="a")))
    raw[-1] ^= 0x01
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(base64.b64encode(bytes(raw)).decode(), KEY, aad="a")


@pytest.mark.parametrize("bad_key", ["", "not-base64!!", base64.b64encode(b"short").decode()])
def test_unusable_keys_are_config_errors(bad_key):
    with pytest.raises(crypto.CryptoError):
        crypto.encrypt("cookie", bad_key)


# --- session tokens ----------------------------------------------------------


def test_token_round_trip():
    token, expires_at = auth.issue_token("{SWID}", "secret", ttl_hours=1)
    assert auth.read_token(token, "secret") == "{SWID}"
    assert expires_at > datetime.now(UTC)


def test_token_carries_no_cookie_material():
    token, _ = auth.issue_token("{SWID}", "secret", ttl_hours=1)
    claims = jwt.decode(token, "secret", algorithms=["HS256"], issuer=auth.ISSUER)
    assert set(claims) == {"sub", "iss", "iat", "exp"}


def test_token_signed_with_another_secret_is_rejected():
    token, _ = auth.issue_token("{SWID}", "secret", ttl_hours=1)
    with pytest.raises(auth.AuthError):
        auth.read_token(token, "different-secret")


def test_expired_token_is_rejected():
    expired = jwt.encode(
        {
            "sub": "{SWID}",
            "iss": auth.ISSUER,
            "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
        },
        "secret",
        algorithm="HS256",
    )
    with pytest.raises(auth.AuthError, match="expired"):
        auth.read_token(expired, "secret")


def test_unsigned_token_is_rejected():
    """`alg: none` must not be accepted."""
    forged = jwt.encode({"sub": "{SWID}", "iss": auth.ISSUER}, key="", algorithm="none")
    with pytest.raises(auth.AuthError):
        auth.read_token(forged, "secret")


@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer", "Bearer   "])
def test_malformed_authorization_headers_are_rejected(header):
    with pytest.raises(auth.AuthError):
        auth.bearer_from_header(header)


def test_bearer_header_is_case_insensitive():
    assert auth.bearer_from_header("bearer abc.def.ghi") == "abc.def.ghi"
