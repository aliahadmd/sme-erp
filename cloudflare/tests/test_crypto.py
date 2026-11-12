import time
import uuid
from types import SimpleNamespace

import jwt
import pytest

from erp_cf.crypto import DUMMY_HASH, create_access, decode_access, hash_password, verify_password
from erp_cf.errors import AuthenticationError

ENV = SimpleNamespace(JWT_SECRET="test-secret-for-tests-only-" * 3)


@pytest.mark.asyncio
async def test_password_hashes_use_independent_salts_and_verify_unicode_long_values():
    password = "Long password Δ" * 10
    first, second = await hash_password(password), await hash_password(password)
    assert first != second
    assert await verify_password(first, password)
    assert not await verify_password(first, "incorrect")
    assert not await verify_password(DUMMY_HASH, password)
    for invalid in ["bad", "$pbkdf2-sha256$1$bad$bad", "$argon2$bad", first + "!"]:
        assert not await verify_password(invalid, password)


def test_access_tokens_require_claims_type_audience_expiry_and_supported_algorithm():
    user, session = str(uuid.uuid7()), str(uuid.uuid7())
    payload = decode_access(ENV, create_access(ENV, user, session))
    assert payload["sub"] == user and payload["sid"] == session
    for changes in [
        {"exp": int(time.time()) - 1},
        {"type": "refresh"},
        {"sid": "bad"},
        {"aud": "other"},
    ]:
        invalid = jwt.encode({**payload, **changes}, ENV.JWT_SECRET, algorithm="HS256")
        with pytest.raises(AuthenticationError):
            decode_access(ENV, invalid)
    with pytest.raises(AuthenticationError):
        decode_access(ENV, jwt.encode(payload, ENV.JWT_SECRET, algorithm="HS512"))
    with pytest.raises(AuthenticationError):
        decode_access(ENV, "not-a-token")
