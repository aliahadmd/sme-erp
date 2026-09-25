import pytest

from app.core.errors import AuthenticationError
from app.core.security import (
    REFRESH_TOKEN,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip():
    hashed = hash_password("s3cret!")
    assert hashed != "s3cret!"
    assert verify_password(hashed, "s3cret!")
    assert not verify_password(hashed, "wrong")


def test_access_token_roundtrip():
    token, jti = create_access_token("user-123", {"is_superuser": True})
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == "user-123"
    assert payload["jti"] == jti
    assert payload["is_superuser"] is True


def test_refresh_token_type_enforced():
    token, _ = create_refresh_token("user-123")
    assert decode_token(token, expected_type=REFRESH_TOKEN)["sub"] == "user-123"
    with pytest.raises(AuthenticationError):
        decode_token(token, expected_type="access")


def test_garbage_token_rejected():
    with pytest.raises(AuthenticationError):
        decode_token("not-a-jwt")
