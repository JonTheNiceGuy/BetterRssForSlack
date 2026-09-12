from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


class ConfigError(Exception):
    pass


_REQUIRED = (
    "DATABASE_URL",
    "OIDC_ISSUER",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
    "SLACK_BOT_TOKEN",
)


@dataclass(frozen=True)
class Config:
    database_url: str
    oidc_issuer: str
    oidc_client_id: str
    oidc_client_secret: str
    slack_bot_token: str
    admin_oidc_groups: frozenset[str] = frozenset()
    token_max_ttl_seconds: int = 86400
    slack_post_min_interval_seconds: float = 1.0
    template_truncate_chars: int = 500
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        missing = [name for name in _REQUIRED if not env.get(name)]
        if missing:
            raise ConfigError(f"Missing required env vars: {', '.join(missing)}")
        groups_csv = env.get("ADMIN_OIDC_GROUPS", "")
        groups = frozenset(g.strip() for g in groups_csv.split(",") if g.strip())
        return cls(
            database_url=env["DATABASE_URL"],
            oidc_issuer=env["OIDC_ISSUER"],
            oidc_client_id=env["OIDC_CLIENT_ID"],
            oidc_client_secret=env["OIDC_CLIENT_SECRET"],
            slack_bot_token=env["SLACK_BOT_TOKEN"],
            admin_oidc_groups=groups,
            token_max_ttl_seconds=int(env.get("TOKEN_MAX_TTL_SECONDS", 86400)),
            slack_post_min_interval_seconds=float(
                env.get("SLACK_POST_MIN_INTERVAL_SECONDS", 1.0)
            ),
            template_truncate_chars=int(env.get("TEMPLATE_TRUNCATE_CHARS", 500)),
            log_level=env.get("LOG_LEVEL", "INFO"),
        )
