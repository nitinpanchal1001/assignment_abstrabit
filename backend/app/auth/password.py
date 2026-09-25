"""Password hashing.

bcrypt with cost 12: comfortably above the commonly cited 10, and still fast
enough (~250ms) on a small container. Higher would push sign-in toward the
request timeout under concurrency.
"""

from __future__ import annotations

import asyncio

import bcrypt

COST = 12

#: A real hash of a throwaway value, used to burn comparable time when no user
#: exists. Precomputed so the timing is stable.
_DUMMY_HASH = bcrypt.hashpw(b"placeholder-password", bcrypt.gensalt(rounds=COST))


def _hash_sync(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=COST)).decode("utf-8")


def _verify_sync(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


async def hash_password(plain: str) -> str:
    """Hash off the event loop.

    bcrypt at cost 12 blocks for roughly a quarter of a second. Running it
    inline would stall every other request on the worker for that entire time,
    so it goes to the thread pool.
    """
    return await asyncio.to_thread(_hash_sync, plain)


async def verify_password(plain: str, hashed: str) -> bool:
    return await asyncio.to_thread(_verify_sync, plain, hashed)


async def fake_verify() -> None:
    """Burn roughly the same time as a real comparison when no user exists.

    Without this, "unknown email" returns in ~1ms while "wrong password" takes
    ~250ms, which is a usable account-enumeration oracle.
    """
    await asyncio.to_thread(bcrypt.checkpw, b"placeholder-password", _DUMMY_HASH)
