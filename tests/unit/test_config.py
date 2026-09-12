import pytest
from app.config import Config, ConfigError

REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+psycopg://user:pass@localhost/db",
    "OIDC_ISSUER": "https://tinyoidc.authenti-kate.org",
    "OIDC_CLIENT_ID": "client_id_12decaf34bad56",
    "OIDC_CLIENT_SECRET": "Super-+Secret_=Key0123456789",
    "SLACK_BOT_TOKEN": "xoxb-fake",
}


def test_from_env_with_all_required_vars_applies_defaults():
    cfg = Config.from_env(REQUIRED_ENV)
    assert cfg.database_url == REQUIRED_ENV["DATABASE_URL"]
    assert cfg.admin_oidc_groups == frozenset()
    assert cfg.token_max_ttl_seconds == 86400
    assert cfg.slack_post_min_interval_seconds == 1.0
    assert cfg.template_truncate_chars == 500
    assert cfg.log_level == "INFO"


def test_from_env_parses_admin_groups_csv():
    env = dict(REQUIRED_ENV, ADMIN_OIDC_GROUPS="admins,platform-team")
    cfg = Config.from_env(env)
    assert cfg.admin_oidc_groups == frozenset({"admins", "platform-team"})


def test_from_env_missing_required_var_raises():
    env = dict(REQUIRED_ENV)
    del env["SLACK_BOT_TOKEN"]
    with pytest.raises(ConfigError, match="SLACK_BOT_TOKEN"):
        Config.from_env(env)
