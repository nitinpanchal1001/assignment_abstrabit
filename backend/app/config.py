"""Application configuration.

Validated once at import. A missing or malformed value fails the process at
startup with a precise message, rather than surfacing as a confusing runtime
error on the first request that happens to need it.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # One env file for the whole repo: the Next.js frontend reads
        # .env.local too, so there is a single place to put credentials during
        # local development. In production every value comes from the host's
        # environment and no file is present.
        env_file=("../.env.local", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- MongoDB -----------------------------------------------------------
    mongodb_uri: str = Field(..., description="MongoDB Atlas connection string")
    mongodb_db: str = Field(default="groundwork")

    # --- Qdrant ------------------------------------------------------------
    qdrant_url: str = Field(..., description="Qdrant Cloud cluster URL")
    qdrant_api_key: str | None = Field(default=None)
    #: ONE collection for every workspace. Tenancy is a payload field plus a
    #: mandatory filter, never a collection per tenant.
    qdrant_collection: str = Field(default="workspace_chunks")

    # --- Auth --------------------------------------------------------------
    auth_secret: str = Field(..., min_length=32)

    # --- Gemini ------------------------------------------------------------
    gemini_api_key: str = Field(...)

    #: A flash-LITE model, chosen for quota rather than capability. Measured
    #: against the live free tier: every full flash model (gemini-3.8-flash,
    #: gemini-3.6-flash, and the gemini-flash-latest alias) allows only 20
    #: requests per day, and pro previews allow 0. One chat turn costs several
    #: requests, so a full flash model runs dry after a few questions.
    gemini_chat_model: str = Field(default="gemini-3.5-flash-lite")
    gemini_embedding_model: str = Field(default="gemini-embedding-2")

    #: Must match the vector size the Qdrant collection was created with;
    #: `migrate` refuses to continue on a mismatch.
    embedding_dimensions: int = Field(default=1536, gt=0)

    # --- Notifications -----------------------------------------------------
    notification_webhook_url: str | None = Field(default=None)

    # --- Rate limiting -----------------------------------------------------
    #: Chat turns allowed per workspace per minute. Every tenant shares one
    #: free-tier Gemini quota, so this stops a single busy workspace from
    #: spending everyone else's. Sized above what a person types by hand and
    #: below what a loop can burn: one turn costs several upstream requests.
    chat_rate_limit_per_minute: int = Field(default=12, gt=0)

    # --- App ---------------------------------------------------------------
    #: Origins allowed to call the API directly. Normally empty: the Next.js
    #: frontend proxies /api/* to this service, so requests are same-origin
    #: and no CORS entry is needed. Populate it only for a split deployment.
    cors_origins: list[str] = Field(default_factory=list)

    environment: str = Field(default="development")

    @field_validator("notification_webhook_url", "qdrant_api_key", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        """Treat an empty env var as unset.

        `FOO=` in a .env file yields "", which would otherwise be accepted as a
        configured-but-broken webhook URL.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor. Import-time validation with a readable failure."""
    try:
        return Settings()  # type: ignore[call-arg]
    except Exception as exc:  # pragma: no cover - startup path
        raise RuntimeError(
            f"Invalid configuration.\n{exc}\n\n"
            "Copy .env.example to .env.local at the repository root and fill in the values."
        ) from exc


settings = get_settings()
