"""Password KDF and signed access tokens for the fresh Workers deployment.

Argon2id uses vetted argon2-cffi with a Pyodide-compatible native wheel,
19 MiB memory, two passes, one lane, 128-bit salts and 256-bit output.
"""

import asyncio
import base64
import hashlib
import sys
import time
import uuid

import jwt
from argon2 import PasswordHasher, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type, hash_secret_raw

from erp_cf.errors import AuthenticationError, ValidationError

TIME_COST = 2
MEMORY_KIB = 19 * 1024
PARALLELISM = 1
ACCESS_SECONDS = 15 * 60
SESSION_SECONDS = 7 * 24 * 3600
DUMMY_HASH = "$argon2id$v=19$m=19456,t=2,p=1$AAAAAAAAAAAAAAAAAAAAAA$" + "A" * 43
_hasher = PasswordHasher(
    time_cost=TIME_COST,
    memory_cost=MEMORY_KIB,
    parallelism=PARALLELISM,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def check_password_size(password: str):
    if not isinstance(password, str) or len(password) > 1024:
        raise ValidationError("Password must contain at most 1024 characters")


async def cpu_call(function, *args):
    if sys.platform == "emscripten":
        return function(*args)
    return await asyncio.to_thread(function, *args)


async def derive(password: str, salt: bytes) -> bytes:
    check_password_size(password)
    return await cpu_call(
        hash_secret_raw, password.encode(), salt, TIME_COST, MEMORY_KIB, PARALLELISM, 32, Type.ID
    )


async def hash_password(password: str) -> str:
    if not 8 <= len(password) <= 1024:
        raise ValidationError("Password must contain 8 to 1024 characters")
    return await cpu_call(_hasher.hash, password)


async def verify_password(encoded: str, password: str) -> bool:
    try:
        parameters = extract_parameters(encoded)
        if (
            parameters.type != Type.ID
            or parameters.version != 19
            or parameters.memory_cost != MEMORY_KIB
            or parameters.time_cost != TIME_COST
            or parameters.parallelism != PARALLELISM
            or parameters.hash_len != 32
            or parameters.salt_len != 16
        ):
            return False
        check_password_size(password)
        return await cpu_call(_hasher.verify, encoded, password)
    except InvalidHashError, VerificationError, ValueError, TypeError:
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def jwt_secret(env) -> str:
    secret = str(getattr(env, "JWT_SECRET", ""))
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET must contain at least 32 characters")
    return secret


def create_access(env, user_id: str, session_id: str) -> str:
    timestamp = int(time.time())
    return jwt.encode(
        {
            "sub": user_id,
            "sid": session_id,
            "jti": uuid.uuid4().hex,
            "type": "access",
            "iat": timestamp,
            "exp": timestamp + ACCESS_SECONDS,
            "iss": "erp",
            "aud": "erp-api",
        },
        jwt_secret(env),
        algorithm="HS256",
    )


def decode_access(env, token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            jwt_secret(env),
            algorithms=["HS256"],
            issuer="erp",
            audience="erp-api",
            options={"require": ["sub", "sid", "jti", "type", "iat", "exp", "iss", "aud"]},
        )
        if payload["type"] != "access":
            raise ValueError("Wrong token type")
        payload["sub"] = str(uuid.UUID(payload["sub"]))
        payload["sid"] = str(uuid.UUID(payload["sid"]))
        return payload
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError) as exc:
        raise AuthenticationError("Invalid or expired access token") from exc
