from datetime import timedelta
from uuid import uuid4

import jwt
import pytest
from jwt.exceptions import InvalidSubjectError
from pwdlib import PasswordHash
from pydantic import ValidationError

from application.exceptions import InvalidToken
from infrastructure.config import SecuritySettings
from infrastructure.security import Argon2PasswordHasher, JwtTokenService


def test_argon2_salted_hashes_verify_exact_password_and_reject_corrupt_hash():
    passwords = Argon2PasswordHasher()
    password = " synthetic-password-with-spaces "
    first = passwords.hash(password)
    second = passwords.hash(password)
    assert first != second
    assert first.startswith("$argon2id$")
    assert password not in first
    # Independent verifier also accepts the stored representation.
    assert PasswordHash.recommended().verify(password, first)
    assert passwords.verify(password, first)
    assert not passwords.verify(password.strip(), first)
    assert not passwords.verify("incorrect-password", first)
    assert not passwords.verify(password, None)
    assert not passwords.verify(password, "not-a-hash")
    assert not passwords.verify(password, "$argon2id$v=19$m=65536,t=3,p=4$bad$bad")
    assert not passwords.verify("synthetic-absent-user-password", "")


def test_issued_token_contains_only_identity_and_times_and_expires_at_boundary(
    security_settings, auth_clock
):
    tokens = JwtTokenService(security_settings)
    user_id = uuid4()
    token = tokens.issue(user_id)
    payload = jwt.decode(
        token, security_settings.JWT_SECRET_KEY.get_secret_value(), algorithms=["HS256"]
    )
    assert payload == {
        "sub": str(user_id),
        "iat": 1893456000,
        "exp": 1893457800,  # 2030-01-01 00:30 UTC, independent expectation.
    }
    assert tokens.verify(token) == user_id
    auth_clock[0] += timedelta(seconds=1799)
    assert tokens.verify(token) == user_id
    auth_clock[0] += timedelta(seconds=1)
    with pytest.raises(InvalidToken):
        tokens.verify(token)


@pytest.mark.parametrize(
    "changes, removed, expected_error",
    [
        ({}, "sub", jwt.MissingRequiredClaimError),
        ({}, "exp", jwt.MissingRequiredClaimError),
        ({"exp": 1893455999}, None, jwt.ExpiredSignatureError),
        ({"sub": None}, None, jwt.MissingRequiredClaimError),
        ({"sub": 123}, None, InvalidSubjectError),
        ({"sub": "not-a-uuid"}, None, None),
        ({"sub": ""}, None, None),
        ({"exp": "not-a-time"}, None, jwt.DecodeError),
        ({"exp": "1893457800"}, None, None),
        ({"exp": 1893457800.5}, None, None),
        ({"exp": float("inf")}, None, None),
        ({"iat": 1893456060}, None, jwt.ImmatureSignatureError),
    ],
)
def test_signed_invalid_claims_reach_the_intended_rule(
    security_settings, auth_clock, changes, removed, expected_error
):
    key = security_settings.JWT_SECRET_KEY.get_secret_value()
    payload = {"sub": str(uuid4()), "exp": 1893457800} | changes
    if removed:
        del payload[removed]
    token = jwt.encode(payload, key, algorithm="HS256")
    if expected_error:
        # Proves that signature/configuration do not mask the intended claim defect.
        with pytest.raises(expected_error):
            jwt.decode(
                token, key, algorithms=["HS256"], options={"require": ["sub", "exp"]}
            )
    with pytest.raises(InvalidToken):
        JwtTokenService(security_settings).verify(token)


def test_invalid_signature_algorithm_and_malformed_token_are_rejected(
    security_settings, auth_clock
):
    key = security_settings.JWT_SECRET_KEY.get_secret_value()
    payload = {"sub": str(uuid4()), "exp": 1893457800}
    tokens = JwtTokenService(security_settings)
    invalid_tokens = [
        "not-a-jwt",
        jwt.encode(
            payload, "different-synthetic-signing-key-32-characters", algorithm="HS256"
        ),
        jwt.encode(payload, key, algorithm="HS384"),
        jwt.encode(payload, key="", algorithm="none"),
    ]
    for token in invalid_tokens:
        with pytest.raises(InvalidToken):
            tokens.verify(token)


def test_security_configuration_requires_key_and_hides_invalid_input(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError) as caught:
        SecuritySettings(_env_file=None)
    assert caught.value.errors(include_input=False)[0]["loc"] == ("JWT_SECRET_KEY",)
    secret = "too-short-synthetic"
    with pytest.raises(ValidationError) as caught:
        SecuritySettings(_env_file=None, JWT_SECRET_KEY=secret)
    assert caught.value.errors(include_input=False)[0]["type"] == "too_short"
    assert secret not in str(caught.value)


def test_security_key_is_masked_and_lifetime_is_bounded(security_settings):
    key = security_settings.JWT_SECRET_KEY.get_secret_value()
    assert key not in repr(security_settings)
    assert key not in security_settings.model_dump_json()
    for minutes in (0, 1441):
        with pytest.raises(ValidationError) as caught:
            SecuritySettings(
                _env_file=None, JWT_SECRET_KEY=key, JWT_ACCESS_TOKEN_MINUTES=minutes
            )
        assert caught.value.errors(include_input=False)[0]["loc"] == (
            "JWT_ACCESS_TOKEN_MINUTES",
        )
