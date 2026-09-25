"""Test configuration.

``app.config`` validates settings at import and every module in the package
reaches it transitively, so the values below must be in the environment before
the first ``from app...`` in any test module. A ``conftest.py`` is imported by
pytest ahead of collection, which is the only hook early enough.

They are set UNCONDITIONALLY, overriding ``../.env.local`` when a developer has
one. That is the point: a test whose behaviour depends on whether the machine
running it happens to hold real credentials is not a test. Nothing in this suite
opens a socket — no Mongo, no Qdrant, no Gemini — so the values need to be
well-formed, not real, and a run on a laptop and a run in CI see the same
configuration.
"""

from __future__ import annotations

import os

_HERMETIC_ENV = {
    "MONGODB_URI": "mongodb://127.0.0.1:27017/?directConnection=true",
    "MONGODB_DB": "groundwork_test",
    "QDRANT_URL": "http://127.0.0.1:6333",
    "QDRANT_API_KEY": "",
    "QDRANT_COLLECTION": "workspace_chunks_test",
    # Long enough to satisfy the min_length=32 guard on the real setting.
    "AUTH_SECRET": "test-only-auth-secret-thirty-two-plus-characters",
    "GEMINI_API_KEY": "test-only-key-never-sent-anywhere",
    "GEMINI_CHAT_MODEL": "gemini-3.5-flash-lite",
    "GEMINI_EMBEDDING_MODEL": "gemini-embedding-2",
    "EMBEDDING_DIMENSIONS": "1536",
    "NOTIFICATION_WEBHOOK_URL": "",
    "CHAT_RATE_LIMIT_PER_MINUTE": "12",
    "ENVIRONMENT": "test",
}

for _key, _value in _HERMETIC_ENV.items():
    os.environ[_key] = _value
