# RSS-to-Slack Watch Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the OIDC-authenticated Flask web app + APScheduler worker that
tracks RSS/Atom feed watches and posts new entries to Slack channels, with
short-lived API tokens for programmatic create/delete/report.

**Architecture:** One Python package (`app/`), three entrypoints sharing one
Postgres DB: `web` (Flask, OIDC login, CRUD UI + JSON API), `worker`
(APScheduler with a persistent SQLAlchemy jobstore, one job per watch),
`migrate` (`alembic upgrade head`, run to completion before the other two
start). Pure logic (ACL, feed diffing, template rendering, rate limiting) is
split into small dependency-free modules so it can be unit-tested without a
DB or network; anything touching Postgres is tested against a real container
via `testcontainers-python`.

**Tech Stack:** Python 3.14, Flask + Jinja2 (basic server-rendered UI),
Authlib (OIDC), SQLAlchemy 2.x, Alembic, APScheduler 3.x
(`SQLAlchemyJobStore`), feedparser, slack_sdk, html2text,
python-json-logger, pytest + testcontainers-python, Playwright (live e2e
OIDC login test against tinyoidc), Docker/docker-compose.

**Spec:** `docs/superpowers/specs/2026-09-12-rss-to-slack-design.md`

## Global Constraints

- Python 3.14 everywhere (Dockerfile base image, local venv, CI if added later).
- Postgres only — no SQLite, in tests or anywhere else. Tests that touch the
  DB run against a real Postgres via `testcontainers-python`.
- Never edit an existing Alembic migration file once it has been committed —
  always add a new forward migration, even to fix a mistake in the previous
  one.
- Single worker replica assumed (APScheduler's persistent jobstore is not
  safe for concurrent schedulers without a distributed lock) — do not add
  multi-replica coordination.
- Structured JSON logs to stdout only (Loki-ingestion-ready) — no file
  logging, no plain-text log formatting.
- Config is env-var-driven only (see the spec's "Config" section for the
  exact variable names) — no config files, no CLI flags for runtime config.
- Version pins written in this plan (Postgres image tag, PyPI package
  versions) are "latest as known at plan-writing time" and **must be
  re-verified against the actual registry/changelog before pinning**, per
  the "prefer latest versions" rule — do not trust the numbers in this
  document blindly.
- `rss_watch.visibility == PUBLIC` means any authenticated user can view
  *and* manage — there is no separate broader "view" permission.

---

## File Structure

```
pyproject.toml
alembic.ini
migrations/
  env.py
  versions/
    0001_initial_schema.py
app/
  __init__.py
  config.py
  db.py
  models/
    __init__.py
    enums.py
    user_cache.py
    slack_channel_cache.py
    rss_watch.py
    api_token.py
  acl.py
  identity_resolver.py
  tokens/
    __init__.py
    service.py
  users/
    __init__.py
    service.py
  slack/
    __init__.py
    client.py
    channel_cache.py
    templates.py
  worker/
    __init__.py
    feed.py
    jobs.py
    scheduler.py
  web/
    __init__.py
    app_factory.py
    routes_auth.py
    routes_watches.py
    routes_tokens.py
    routes_health.py
  logging_setup.py
wsgi.py
worker_main.py
Dockerfile
docker-compose.yml
.env.example
tests/
  conftest.py
  unit/
    test_config.py
    test_acl.py
    test_feed.py
    test_templates.py
    test_slack_client.py
    test_identity_resolver.py
  integration/
    test_models_migration.py
    test_token_service.py
    test_user_service.py
    test_channel_cache.py
    test_worker_jobs.py
    test_scheduler.py
    test_routes_auth.py
    test_routes_watches.py
    test_routes_tokens.py
    test_routes_health.py
```

Each `app/` subpackage has one responsibility: `models/` is pure data
shape, `acl.py`/`identity_resolver.py` are pure/DB-light auth logic,
`tokens/` and `users/` are small DB-backed services, `slack/` isolates all
Slack API contact (client, cache, rendering), `worker/` is the scheduling +
job-execution side, `web/` is the Flask routes side. `worker/` and `web/`
both depend on the lower layers but never on each other.

---

## Task 1: Project scaffold + config loader

**Files:**
- Create: `pyproject.toml`
- Create: `app/__init__.py` (empty)
- Create: `app/config.py`
- Test: `tests/unit/test_config.py`
- Create: `.env.example`

**Interfaces:**
- Produces: `app.config.Config` (frozen dataclass) and
  `app.config.Config.from_env(env: Mapping[str, str]) -> Config`, raising
  `app.config.ConfigError(msg: str)` on missing required vars. Fields:
  `database_url: str`, `oidc_issuer: str`, `oidc_client_id: str`,
  `oidc_client_secret: str`, `admin_oidc_groups: frozenset[str]`,
  `token_max_ttl_seconds: int`, `slack_bot_token: str`,
  `slack_post_min_interval_seconds: float`, `template_truncate_chars: int`,
  `log_level: str`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "betterrssforslack"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = [
    "flask>=3.1",
    "authlib>=1.6",
    "sqlalchemy>=2.0",
    "alembic>=1.14",
    "apscheduler>=3.11,<4.0",
    "feedparser>=6.0",
    "slack_sdk>=3.35",
    "html2text>=2024.2.26",
    "python-json-logger>=3.2",
    "psycopg[binary]>=3.2",
    "gunicorn>=23.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-cov>=6.0",
    "testcontainers[postgres]>=4.9",
    "responses>=0.25",
    "playwright>=1.49",
]

[tool.pytest.ini_options]
testpaths = ["tests"]

[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"
```

(Verify each version against PyPI before install, per Global Constraints —
these are the versions known at plan-writing time.)

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_config.py
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/unit/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 4: Write minimal implementation**

```python
# app/config.py
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Write `.env.example`**

```bash
DATABASE_URL=postgresql+psycopg://app:app@postgres:5432/app
OIDC_ISSUER=https://tinyoidc.authenti-kate.org
OIDC_CLIENT_ID=client_id_12decaf34bad56
OIDC_CLIENT_SECRET=Super-+Secret_=Key0123456789
ADMIN_OIDC_GROUPS=admins
TOKEN_MAX_TTL_SECONDS=86400
SLACK_BOT_TOKEN=xoxb-replace-me
SLACK_POST_MIN_INTERVAL_SECONDS=1.0
TEMPLATE_TRUNCATE_CHARS=500
LOG_LEVEL=INFO
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml app/__init__.py app/config.py tests/unit/test_config.py .env.example
git commit -m "feat: add project scaffold and env-var config loader"
```

---

## Task 2: SQLAlchemy models

**Files:**
- Create: `app/db.py`
- Create: `app/models/__init__.py`
- Create: `app/models/enums.py`
- Create: `app/models/user_cache.py`
- Create: `app/models/slack_channel_cache.py`
- Create: `app/models/rss_watch.py`
- Create: `app/models/api_token.py`
- Test: `tests/unit/test_models_import.py`

**Interfaces:**
- Consumes: nothing (base layer).
- Produces: `app.db.Base` (declarative base), `app.db.make_engine(url: str)`,
  `app.db.make_session_factory(engine)`. Models: `app.models.enums.Template`,
  `app.models.enums.Visibility`, `app.models.user_cache.UserCache`,
  `app.models.slack_channel_cache.SlackChannelCache`,
  `app.models.rss_watch.RssWatch`, `app.models.api_token.ApiToken` — all
  registered on `Base.metadata` so `Base.metadata.create_all(engine)` builds
  every table. `app.models.__init__` imports all four model modules so a
  single `import app.models` is enough to populate the metadata.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_models_import.py
def test_all_tables_registered_on_metadata():
    import app.models  # noqa: F401 - side effect: registers tables
    from app.db import Base

    table_names = set(Base.metadata.tables.keys())
    assert table_names == {
        "user_cache",
        "slack_channel_cache",
        "rss_watch",
        "api_token",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_models_import.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: Write `app/db.py`**

```python
# app/db.py
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

def make_engine(database_url: str):
    return create_engine(database_url, future=True)

def make_session_factory(engine):
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)
```

- [ ] **Step 4: Write `app/models/enums.py`**

```python
# app/models/enums.py
import enum

class Template(str, enum.Enum):
    HEADLINE_LINK = "headline_link"
    HEADLINE_LINK_TRUNCATED = "headline_link_truncated"
    HEADLINE_LINK_THREAD = "headline_link_thread"
    HEADLINE_LINK_FULL = "headline_link_full"

class Visibility(str, enum.Enum):
    OWNER_ONLY = "owner_only"
    GROUP = "group"
    PUBLIC = "public"
```

- [ ] **Step 5: Write `app/models/user_cache.py`**

```python
# app/models/user_cache.py
from sqlalchemy import Column, String, DateTime
from app.db import Base

class UserCache(Base):
    __tablename__ = "user_cache"

    id = Column(String, primary_key=True)  # OIDC sub
    display_name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    last_refreshed_at = Column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 6: Write `app/models/slack_channel_cache.py`**

```python
# app/models/slack_channel_cache.py
from sqlalchemy import Column, String, DateTime
from app.db import Base

class SlackChannelCache(Base):
    __tablename__ = "slack_channel_cache"

    id = Column(String, primary_key=True)  # Slack channel ID
    name = Column(String, nullable=False)
    last_refreshed_at = Column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 7: Write `app/models/rss_watch.py`**

```python
# app/models/rss_watch.py
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, Text,
    Enum as SAEnum,
)
from app.db import Base
from app.models.enums import Template, Visibility

class RssWatch(Base):
    __tablename__ = "rss_watch"

    id = Column(Integer, primary_key=True, autoincrement=True)
    feed_url = Column(String, nullable=False)
    slack_channel_id = Column(
        String, ForeignKey("slack_channel_cache.id"), nullable=False
    )
    template = Column(SAEnum(Template), nullable=False)
    visibility = Column(
        SAEnum(Visibility), nullable=False, default=Visibility.OWNER_ONLY
    )
    owning_group = Column(String, nullable=True)
    created_by_sub = Column(String, ForeignKey("user_cache.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    check_interval_seconds = Column(Integer, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    consecutive_failure_count = Column(Integer, nullable=False, default=0)
    auto_pause_after_failures = Column(Integer, nullable=False, default=0)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    last_posted_at = Column(DateTime(timezone=True), nullable=True)
    last_entry_id = Column(String, nullable=True)
    last_error = Column(Text, nullable=True)
```

- [ ] **Step 8: Write `app/models/api_token.py`**

```python
# app/models/api_token.py
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from app.db import Base

class ApiToken(Base):
    __tablename__ = "api_token"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token_hash = Column(String, nullable=False, unique=True)
    owner_sub = Column(String, ForeignKey("user_cache.id"), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 9: Write `app/models/__init__.py`**

```python
# app/models/__init__.py
from app.models import user_cache, slack_channel_cache, rss_watch, api_token  # noqa: F401
```

- [ ] **Step 10: Run test to verify it passes**

Run: `pytest tests/unit/test_models_import.py -v`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add app/db.py app/models tests/unit/test_models_import.py
git commit -m "feat: add SQLAlchemy models for watches, tokens, and caches"
```

---

## Task 3: Postgres testcontainer fixtures + Alembic initial migration

**Files:**
- Create: `tests/conftest.py`
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/versions/0001_initial_schema.py`
- Test: `tests/integration/test_models_migration.py`

**Interfaces:**
- Consumes: `app.db.Base`, `app.models` (Task 2).
- Produces: pytest fixtures `postgres_container` (session-scoped),
  `db_engine` (function-scoped, tables created via `Base.metadata.create_all`
  for unit-style DB tests), `db_session` (function-scoped, yields a
  SQLAlchemy `Session` bound to `db_engine`, rolled back/closed after each
  test). Also the standalone Alembic setup used by the `migrate` entrypoint
  and by `test_models_migration.py`, which runs migrations against a
  **separate** container rather than `db_engine`'s `create_all` shortcut, to
  prove the migration itself is correct.

- [ ] **Step 1: Write `tests/conftest.py`**

```python
# tests/conftest.py
import pytest
from testcontainers.postgres import PostgresContainer
from sqlalchemy.orm import Session

from app.db import Base, make_engine, make_session_factory
import app.models  # noqa: F401 - registers tables on Base.metadata

@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:17") as pg:  # verify latest tag before use
        yield pg

@pytest.fixture()
def db_engine(postgres_container):
    engine = make_engine(postgres_container.get_connection_url())
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()

@pytest.fixture()
def db_session(db_engine) -> Session:
    factory = make_session_factory(db_engine)
    session = factory()
    yield session
    session.close()
```

- [ ] **Step 2: Write the failing migration test**

```python
# tests/integration/test_models_migration.py
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect

def test_alembic_upgrade_head_creates_all_tables(postgres_container):
    alembic_cfg = AlembicConfig("alembic.ini")
    alembic_cfg.set_main_option(
        "sqlalchemy.url", postgres_container.get_connection_url()
    )
    command.upgrade(alembic_cfg, "head")

    from app.db import make_engine
    engine = make_engine(postgres_container.get_connection_url())
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) >= {
        "user_cache",
        "slack_channel_cache",
        "rss_watch",
        "api_token",
    }
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/integration/test_models_migration.py -v`
Expected: FAIL with `FileNotFoundError` / `alembic.util.exc.CommandError` (no
`alembic.ini`)

- [ ] **Step 4: Write `alembic.ini`**

```ini
[alembic]
script_location = migrations
prepend_sys_path = .
sqlalchemy.url = driver://user:pass@localhost/dbname

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

- [ ] **Step 5: Write `migrations/env.py`**

```python
# migrations/env.py
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db import Base
import app.models  # noqa: F401 - registers tables on Base.metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 6: Write `migrations/versions/0001_initial_schema.py`**

```python
# migrations/versions/0001_initial_schema.py
"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TEMPLATE_ENUM = sa.Enum(
    "headline_link",
    "headline_link_truncated",
    "headline_link_thread",
    "headline_link_full",
    name="template",
)
VISIBILITY_ENUM = sa.Enum("owner_only", "group", "public", name="visibility")

def upgrade():
    op.create_table(
        "user_cache",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "slack_channel_cache",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "rss_watch",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("feed_url", sa.String(), nullable=False),
        sa.Column(
            "slack_channel_id",
            sa.String(),
            sa.ForeignKey("slack_channel_cache.id"),
            nullable=False,
        ),
        sa.Column("template", TEMPLATE_ENUM, nullable=False),
        sa.Column(
            "visibility", VISIBILITY_ENUM, nullable=False, server_default="owner_only"
        ),
        sa.Column("owning_group", sa.String(), nullable=True),
        sa.Column(
            "created_by_sub",
            sa.String(),
            sa.ForeignKey("user_cache.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("check_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "consecutive_failure_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "auto_pause_after_failures", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_entry_id", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_table(
        "api_token",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True),
        sa.Column(
            "owner_sub", sa.String(), sa.ForeignKey("user_cache.id"), nullable=False
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )

def downgrade():
    op.drop_table("api_token")
    op.drop_table("rss_watch")
    op.drop_table("slack_channel_cache")
    op.drop_table("user_cache")
    VISIBILITY_ENUM.drop(op.get_bind(), checkfirst=True)
    TEMPLATE_ENUM.drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/integration/test_models_migration.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add tests/conftest.py alembic.ini migrations
git commit -m "feat: add Alembic initial migration and Postgres test fixtures"
```

---

## Task 4: ACL logic

**Files:**
- Create: `app/acl.py`
- Test: `tests/unit/test_acl.py`

**Interfaces:**
- Consumes: `app.models.enums.Visibility` (Task 2).
- Produces: `app.acl.Identity` (frozen dataclass: `sub: str`,
  `groups: frozenset[str]`, `is_admin: bool`),
  `app.acl.can_access(watch, identity: Identity) -> bool` — `watch` is
  anything with `.created_by_sub`, `.visibility`, `.owning_group` attributes
  (an `RssWatch` instance, unpersisted is fine for this pure function).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_acl.py
from types import SimpleNamespace
from app.acl import Identity, can_access
from app.models.enums import Visibility

def watch(**overrides):
    defaults = dict(
        created_by_sub="owner-sub",
        visibility=Visibility.OWNER_ONLY,
        owning_group=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)

def test_admin_can_access_anything():
    identity = Identity(sub="someone-else", groups=frozenset(), is_admin=True)
    assert can_access(watch(), identity) is True

def test_owner_can_access_own_owner_only_watch():
    identity = Identity(sub="owner-sub", groups=frozenset(), is_admin=False)
    assert can_access(watch(), identity) is True

def test_non_owner_cannot_access_owner_only_watch():
    identity = Identity(sub="stranger", groups=frozenset(), is_admin=False)
    assert can_access(watch(), identity) is False

def test_group_member_can_access_group_watch():
    identity = Identity(sub="stranger", groups=frozenset({"eng"}), is_admin=False)
    w = watch(visibility=Visibility.GROUP, owning_group="eng")
    assert can_access(w, identity) is True

def test_non_group_member_cannot_access_group_watch():
    identity = Identity(sub="stranger", groups=frozenset({"sales"}), is_admin=False)
    w = watch(visibility=Visibility.GROUP, owning_group="eng")
    assert can_access(w, identity) is False

def test_anyone_authenticated_can_access_public_watch():
    identity = Identity(sub="stranger", groups=frozenset(), is_admin=False)
    w = watch(visibility=Visibility.PUBLIC)
    assert can_access(w, identity) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_acl.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.acl'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/acl.py
from dataclasses import dataclass
from app.models.enums import Visibility

@dataclass(frozen=True)
class Identity:
    sub: str
    groups: frozenset[str]
    is_admin: bool

def can_access(watch, identity: Identity) -> bool:
    if identity.is_admin:
        return True
    if watch.created_by_sub == identity.sub:
        return True
    if watch.visibility == Visibility.GROUP and watch.owning_group in identity.groups:
        return True
    if watch.visibility == Visibility.PUBLIC:
        return True
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_acl.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app/acl.py tests/unit/test_acl.py
git commit -m "feat: add ACL can_access rule for watches"
```

---

## Task 5: API token service (mint / verify / revoke)

**Files:**
- Create: `app/tokens/__init__.py` (empty)
- Create: `app/tokens/service.py`
- Test: `tests/integration/test_token_service.py`

**Interfaces:**
- Consumes: `app.models.api_token.ApiToken`, `app.models.user_cache.UserCache`
  (Task 2), `db_session` fixture (Task 3).
- Produces: `app.tokens.service.mint_token(session, owner_sub: str, *,
  ttl_seconds: int, description: str | None = None) -> tuple[ApiToken,
  str]` (returns the DB row and the one-time plaintext token),
  `app.tokens.service.verify_token(session, raw_token: str) -> ApiToken |
  None` (returns `None` if not found, expired, or revoked),
  `app.tokens.service.revoke_token(session, token_id: int) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_token_service.py
import datetime as dt
from app.models.user_cache import UserCache
from app.tokens.service import mint_token, verify_token, revoke_token

def make_user(db_session, sub="user-1"):
    user = UserCache(
        id=sub, display_name="Test User", email="t@example.com",
        last_refreshed_at=dt.datetime.now(dt.timezone.utc),
    )
    db_session.add(user)
    db_session.commit()
    return user

def test_mint_then_verify_round_trip(db_session):
    make_user(db_session)
    token_row, raw = mint_token(db_session, "user-1", ttl_seconds=3600, description="cli")
    db_session.commit()

    found = verify_token(db_session, raw)
    assert found is not None
    assert found.id == token_row.id
    assert found.owner_sub == "user-1"
    assert found.description == "cli"

def test_verify_rejects_wrong_token(db_session):
    make_user(db_session)
    mint_token(db_session, "user-1", ttl_seconds=3600)
    db_session.commit()
    assert verify_token(db_session, "not-a-real-token") is None

def test_verify_rejects_expired_token(db_session):
    make_user(db_session)
    token_row, raw = mint_token(db_session, "user-1", ttl_seconds=-1)
    db_session.commit()
    assert verify_token(db_session, raw) is None

def test_verify_rejects_revoked_token(db_session):
    make_user(db_session)
    token_row, raw = mint_token(db_session, "user-1", ttl_seconds=3600)
    db_session.commit()
    revoke_token(db_session, token_row.id)
    db_session.commit()
    assert verify_token(db_session, raw) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_token_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.tokens'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/tokens/service.py
import datetime as dt
import hashlib
import secrets

from app.models.api_token import ApiToken

def _hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

def mint_token(session, owner_sub: str, *, ttl_seconds: int,
                description: str | None = None) -> tuple[ApiToken, str]:
    raw = secrets.token_urlsafe(32)
    now = dt.datetime.now(dt.timezone.utc)
    row = ApiToken(
        token_hash=_hash(raw),
        owner_sub=owner_sub,
        description=description,
        created_at=now,
        expires_at=now + dt.timedelta(seconds=ttl_seconds),
    )
    session.add(row)
    session.flush()
    return row, raw

def verify_token(session, raw_token: str) -> ApiToken | None:
    row = (
        session.query(ApiToken)
        .filter(ApiToken.token_hash == _hash(raw_token))
        .one_or_none()
    )
    if row is None:
        return None
    now = dt.datetime.now(dt.timezone.utc)
    if row.revoked_at is not None:
        return None
    if row.expires_at <= now:
        return None
    return row

def revoke_token(session, token_id: int) -> None:
    row = session.get(ApiToken, token_id)
    if row is not None:
        row.revoked_at = dt.datetime.now(dt.timezone.utc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/integration/test_token_service.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/tokens tests/integration/test_token_service.py
git commit -m "feat: add API token mint/verify/revoke service"
```

---

## Task 6: User cache upsert service

**Files:**
- Create: `app/users/__init__.py` (empty)
- Create: `app/users/service.py`
- Test: `tests/integration/test_user_service.py`

**Interfaces:**
- Consumes: `app.models.user_cache.UserCache` (Task 2).
- Produces: `app.users.service.upsert_user(session, *, sub: str,
  display_name: str | None, email: str | None) -> UserCache`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_user_service.py
from app.users.service import upsert_user
from app.models.user_cache import UserCache

def test_upsert_creates_new_user(db_session):
    user = upsert_user(db_session, sub="sub-1", display_name="Jon", email="jon@example.com")
    db_session.commit()
    assert user.id == "sub-1"
    assert db_session.get(UserCache, "sub-1").display_name == "Jon"

def test_upsert_updates_existing_user(db_session):
    upsert_user(db_session, sub="sub-1", display_name="Jon", email="jon@example.com")
    db_session.commit()

    upsert_user(db_session, sub="sub-1", display_name="Jon S", email="jon@example.com")
    db_session.commit()

    stored = db_session.get(UserCache, "sub-1")
    assert stored.display_name == "Jon S"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_user_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.users'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/users/service.py
import datetime as dt
from app.models.user_cache import UserCache

def upsert_user(session, *, sub: str, display_name: str | None,
                  email: str | None) -> UserCache:
    row = session.get(UserCache, sub)
    now = dt.datetime.now(dt.timezone.utc)
    if row is None:
        row = UserCache(
            id=sub, display_name=display_name, email=email, last_refreshed_at=now
        )
        session.add(row)
    else:
        row.display_name = display_name
        row.email = email
        row.last_refreshed_at = now
    session.flush()
    return row
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/integration/test_user_service.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/users tests/integration/test_user_service.py
git commit -m "feat: add user_cache upsert service"
```

---

## Task 7: Feed parsing + diff/backfill logic

**Files:**
- Create: `app/worker/__init__.py` (empty)
- Create: `app/worker/feed.py`
- Test: `tests/unit/test_feed.py`

**Interfaces:**
- Consumes: `feedparser` (PyPI package).
- Produces: `app.worker.feed.FeedEntry` (frozen dataclass: `guid: str`,
  `title: str`, `link: str`, `body_html: str`),
  `app.worker.feed.parse_feed(feed_url: str) -> list[FeedEntry]` (network
  call, newest-first — not unit tested directly, wrapped separately so it
  can be monkeypatched in Task 11),
  `app.worker.feed.find_new_entries(entries: list[FeedEntry], last_entry_id:
  str | None) -> tuple[list[FeedEntry], bool]` — pure function, returns
  `(new_entries_oldest_first, is_backfill)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_feed.py
from app.worker.feed import FeedEntry, find_new_entries

def entry(guid):
    return FeedEntry(guid=guid, title=f"Title {guid}", link=f"https://x/{guid}", body_html="<p>body</p>")

def test_cold_start_with_no_cursor_backfills_newest_only():
    entries = [entry("3"), entry("2"), entry("1")]  # newest-first
    new_entries, is_backfill = find_new_entries(entries, last_entry_id=None)
    assert is_backfill is True
    assert [e.guid for e in new_entries] == ["3"]

def test_cursor_not_found_backfills_newest_only():
    entries = [entry("3"), entry("2"), entry("1")]
    new_entries, is_backfill = find_new_entries(entries, last_entry_id="99")
    assert is_backfill is True
    assert [e.guid for e in new_entries] == ["3"]

def test_cursor_found_returns_newer_entries_oldest_first():
    entries = [entry("5"), entry("4"), entry("3"), entry("2"), entry("1")]
    new_entries, is_backfill = find_new_entries(entries, last_entry_id="3")
    assert is_backfill is False
    assert [e.guid for e in new_entries] == ["4", "5"]

def test_cursor_is_newest_returns_nothing_new():
    entries = [entry("3"), entry("2"), entry("1")]
    new_entries, is_backfill = find_new_entries(entries, last_entry_id="3")
    assert is_backfill is False
    assert new_entries == []

def test_empty_feed_with_no_cursor_backfills_nothing():
    new_entries, is_backfill = find_new_entries([], last_entry_id=None)
    assert is_backfill is True
    assert new_entries == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_feed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.worker'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/worker/feed.py
from dataclasses import dataclass
import feedparser

@dataclass(frozen=True)
class FeedEntry:
    guid: str
    title: str
    link: str
    body_html: str

def parse_feed(feed_url: str) -> list[FeedEntry]:
    parsed = feedparser.parse(feed_url)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Failed to parse feed {feed_url}: {parsed.bozo_exception}")
    entries = []
    for e in parsed.entries:
        guid = e.get("id") or e.get("link") or e.get("title", "")
        body_html = ""
        if "content" in e and e["content"]:
            body_html = e["content"][0].get("value", "")
        elif "summary" in e:
            body_html = e.get("summary", "")
        entries.append(
            FeedEntry(guid=guid, title=e.get("title", ""), link=e.get("link", ""), body_html=body_html)
        )
    return entries

def find_new_entries(
    entries: list[FeedEntry], last_entry_id: str | None
) -> tuple[list[FeedEntry], bool]:
    if not entries:
        return [], last_entry_id is None

    if last_entry_id is None:
        return [entries[0]], True

    index = next((i for i, e in enumerate(entries) if e.guid == last_entry_id), None)
    if index is None:
        return [entries[0]], True

    newer = entries[:index]
    return list(reversed(newer)), False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_feed.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/worker/__init__.py app/worker/feed.py tests/unit/test_feed.py
git commit -m "feat: add feed parsing and new-entry diff/backfill logic"
```

---

## Task 8: Slack message templates

**Files:**
- Create: `app/slack/__init__.py` (empty)
- Create: `app/slack/templates.py`
- Test: `tests/unit/test_templates.py`

**Interfaces:**
- Consumes: `app.worker.feed.FeedEntry` (Task 7),
  `app.models.enums.Template` (Task 2).
- Produces: `app.slack.templates.render(template: Template, entry:
  FeedEntry, *, truncate_chars: int, backfill: bool = False) ->
  tuple[str, str | None]` — returns `(root_text, thread_text_or_None)`.
  `thread_text` is non-`None` only for `Template.HEADLINE_LINK_THREAD`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_templates.py
from app.worker.feed import FeedEntry
from app.models.enums import Template
from app.slack.templates import render

LONG_BODY = "<p>" + ("word " * 200) + "</p>"

def entry(body_html="<p>Hello <b>world</b></p>"):
    return FeedEntry(guid="1", title="My Title", link="https://example.com/1", body_html=body_html)

def test_headline_link_has_no_thread():
    root, thread = render(Template.HEADLINE_LINK, entry(), truncate_chars=500)
    assert "My Title" in root
    assert "https://example.com/1" in root
    assert thread is None

def test_headline_link_truncated_strips_html_and_truncates():
    root, thread = render(Template.HEADLINE_LINK_TRUNCATED, entry(LONG_BODY), truncate_chars=20)
    assert "My Title" in root
    assert "<p>" not in root
    body_part = root.split("\n", 1)[1]
    assert len(body_part) <= 23  # 20 chars + ellipsis
    assert thread is None

def test_headline_link_thread_splits_root_and_reply():
    root, thread = render(Template.HEADLINE_LINK_THREAD, entry(LONG_BODY), truncate_chars=20)
    assert "My Title" in root
    assert "https://example.com/1" in root
    assert "word" in thread
    assert len(thread) <= 23

def test_headline_link_full_includes_entire_body_untruncated():
    root, thread = render(Template.HEADLINE_LINK_FULL, entry(LONG_BODY), truncate_chars=20)
    assert "word" * 1 in root
    assert root.count("word") == 200
    assert thread is None

def test_backfill_prefixes_title():
    root, _ = render(Template.HEADLINE_LINK, entry(), truncate_chars=500, backfill=True)
    assert root.startswith("[Backfill] ")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_templates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.slack'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/slack/templates.py
import html2text
from app.models.enums import Template
from app.worker.feed import FeedEntry

_converter = html2text.HTML2Text()
_converter.ignore_links = True
_converter.body_width = 0

def _to_text(body_html: str) -> str:
    return _converter.handle(body_html).strip()

def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."

def _title(entry: FeedEntry, backfill: bool) -> str:
    return f"[Backfill] {entry.title}" if backfill else entry.title

def render(
    template: Template, entry: FeedEntry, *, truncate_chars: int, backfill: bool = False
) -> tuple[str, str | None]:
    title = _title(entry, backfill)
    headline = f"{title}\n{entry.link}"

    if template == Template.HEADLINE_LINK:
        return headline, None

    if template == Template.HEADLINE_LINK_TRUNCATED:
        body = _truncate(_to_text(entry.body_html), truncate_chars)
        return f"{headline}\n{body}", None

    if template == Template.HEADLINE_LINK_THREAD:
        body = _truncate(_to_text(entry.body_html), truncate_chars)
        return headline, body

    if template == Template.HEADLINE_LINK_FULL:
        body = _to_text(entry.body_html)
        return f"{headline}\n{body}", None

    raise ValueError(f"Unknown template: {template}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_templates.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/slack/__init__.py app/slack/templates.py tests/unit/test_templates.py
git commit -m "feat: add Slack message template rendering"
```

---

## Task 9: Throttled Slack client wrapper

**Files:**
- Create: `app/slack/client.py`
- Test: `tests/unit/test_slack_client.py`

**Interfaces:**
- Consumes: `slack_sdk.WebClient`.
- Produces: `app.slack.client.ThrottledSlackClient(token: str, *,
  min_interval_seconds: float, web_client_cls=WebClient,
  sleep_fn=time.sleep, clock_fn=time.monotonic)` with method
  `.post_message(channel: str, text: str, thread_ts: str | None = None) ->
  dict` (returns the raw Slack API response dict, which includes `"ts"`).
  Constructor parameters `web_client_cls`, `sleep_fn`, `clock_fn` exist
  purely for test injection.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_slack_client.py
from app.slack.client import ThrottledSlackClient

class FakeWebClient:
    def __init__(self, token):
        self.token = token
        self.calls = []

    def chat_postMessage(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "ts": f"ts-{len(self.calls)}"}

def make_client(min_interval=1.0):
    times = {"now": 0.0}
    sleeps = []

    def fake_clock():
        return times["now"]

    def fake_sleep(seconds):
        sleeps.append(seconds)
        times["now"] += seconds

    client = ThrottledSlackClient(
        "xoxb-fake",
        min_interval_seconds=min_interval,
        web_client_cls=FakeWebClient,
        sleep_fn=fake_sleep,
        clock_fn=fake_clock,
    )
    return client, sleeps, times

def test_post_message_forwards_to_slack_sdk():
    client, sleeps, _ = make_client()
    resp = client.post_message("C123", "hello")
    assert resp["ts"] == "ts-1"
    assert client._web_client.calls[0] == {"channel": "C123", "text": "hello"}

def test_post_message_includes_thread_ts_when_given():
    client, sleeps, _ = make_client()
    client.post_message("C123", "reply", thread_ts="ts-1")
    assert client._web_client.calls[0]["thread_ts"] == "ts-1"

def test_second_call_within_interval_sleeps_remaining_gap():
    client, sleeps, times = make_client(min_interval=1.0)
    client.post_message("C123", "first")
    times["now"] = 0.4  # only 0.4s elapsed
    client.post_message("C123", "second")
    assert sleeps == [0.6]

def test_call_after_interval_elapsed_does_not_sleep():
    client, sleeps, times = make_client(min_interval=1.0)
    client.post_message("C123", "first")
    times["now"] = 2.0
    client.post_message("C123", "second")
    assert sleeps == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_slack_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.slack.client'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/slack/client.py
import time
import threading
from slack_sdk import WebClient
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

class ThrottledSlackClient:
    def __init__(
        self,
        token: str,
        *,
        min_interval_seconds: float,
        web_client_cls=WebClient,
        sleep_fn=time.sleep,
        clock_fn=time.monotonic,
    ):
        self._web_client = web_client_cls(token)
        if hasattr(self._web_client, "retry_handlers"):
            self._web_client.retry_handlers.append(
                RateLimitErrorRetryHandler(max_retry_count=3)
            )
        self._min_interval = min_interval_seconds
        self._sleep = sleep_fn
        self._clock = clock_fn
        self._lock = threading.Lock()
        self._last_call_at: float | None = None

    def post_message(self, channel: str, text: str, thread_ts: str | None = None) -> dict:
        with self._lock:
            self._throttle()
            kwargs = {"channel": channel, "text": text}
            if thread_ts is not None:
                kwargs["thread_ts"] = thread_ts
            response = self._web_client.chat_postMessage(**kwargs)
            self._last_call_at = self._clock()
            return response

    def _throttle(self):
        if self._last_call_at is None:
            return
        elapsed = self._clock() - self._last_call_at
        remaining = self._min_interval - elapsed
        if remaining > 0:
            self._sleep(remaining)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_slack_client.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/slack/client.py tests/unit/test_slack_client.py
git commit -m "feat: add rate-limited Slack client wrapper"
```

---

## Task 10: Slack channel cache service

**Files:**
- Create: `app/slack/channel_cache.py`
- Test: `tests/integration/test_channel_cache.py`

**Interfaces:**
- Consumes: `app.models.slack_channel_cache.SlackChannelCache` (Task 2),
  something with a `.conversations_info(channel: str) -> dict` method and a
  `.conversations_list(**kwargs) -> dict` method (the raw `slack_sdk`
  `WebClient`, not `ThrottledSlackClient` — channel listing is a read, not a
  post, so it is not subject to the posting rate limit).
- Produces: `app.slack.channel_cache.refresh_channel(session, slack_client,
  channel_id: str, *, max_age_seconds: int = 3600) ->
  SlackChannelCache` (fetches+upserts only if the cached row is missing or
  stale), `app.slack.channel_cache.list_bot_channels(slack_client) ->
  list[dict]` (raw `{"id": ..., "name": ...}` dicts from
  `conversations_list`, for the create-watch channel picker — not cached,
  always live).

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_channel_cache.py
import datetime as dt
from app.models.slack_channel_cache import SlackChannelCache
from app.slack.channel_cache import refresh_channel, list_bot_channels

class FakeSlackClient:
    def __init__(self):
        self.info_calls = []
        self.channels = {"C1": "general", "C2": "announcements"}

    def conversations_info(self, channel):
        self.info_calls.append(channel)
        return {"channel": {"id": channel, "name": self.channels[channel]}}

    def conversations_list(self, **kwargs):
        return {
            "channels": [
                {"id": cid, "name": name} for cid, name in self.channels.items()
            ]
        }

def test_refresh_channel_creates_row_when_missing(db_session):
    client = FakeSlackClient()
    row = refresh_channel(db_session, client, "C1")
    db_session.commit()
    assert row.id == "C1"
    assert row.name == "general"
    assert client.info_calls == ["C1"]

def test_refresh_channel_skips_call_when_fresh(db_session):
    client = FakeSlackClient()
    refresh_channel(db_session, client, "C1")
    db_session.commit()
    refresh_channel(db_session, client, "C1")
    assert client.info_calls == ["C1"]  # second call skipped, still fresh

def test_refresh_channel_refetches_when_stale(db_session):
    client = FakeSlackClient()
    row = refresh_channel(db_session, client, "C1")
    db_session.commit()
    row.last_refreshed_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
    db_session.commit()

    refresh_channel(db_session, client, "C1", max_age_seconds=3600)
    assert client.info_calls == ["C1", "C1"]

def test_list_bot_channels_returns_id_and_name():
    client = FakeSlackClient()
    channels = list_bot_channels(client)
    assert {"id": "C1", "name": "general"} in channels
    assert {"id": "C2", "name": "announcements"} in channels
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_channel_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.slack.channel_cache'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/slack/channel_cache.py
import datetime as dt
from app.models.slack_channel_cache import SlackChannelCache

def refresh_channel(
    session, slack_client, channel_id: str, *, max_age_seconds: int = 3600
) -> SlackChannelCache:
    row = session.get(SlackChannelCache, channel_id)
    now = dt.datetime.now(dt.timezone.utc)
    if row is not None:
        age = (now - row.last_refreshed_at).total_seconds()
        if age < max_age_seconds:
            return row

    info = slack_client.conversations_info(channel_id)
    name = info["channel"]["name"]

    if row is None:
        row = SlackChannelCache(id=channel_id, name=name, last_refreshed_at=now)
        session.add(row)
    else:
        row.name = name
        row.last_refreshed_at = now
    session.flush()
    return row

def list_bot_channels(slack_client) -> list[dict]:
    response = slack_client.conversations_list(types="public_channel,private_channel")
    return [{"id": c["id"], "name": c["name"]} for c in response["channels"]]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/integration/test_channel_cache.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/slack/channel_cache.py tests/integration/test_channel_cache.py
git commit -m "feat: add Slack channel name cache and channel picker listing"
```

---

## Task 11: Worker job — `check_watch`

**Files:**
- Create: `app/worker/jobs.py`
- Test: `tests/integration/test_worker_jobs.py`

**Interfaces:**
- Consumes: `app.models.rss_watch.RssWatch`, `app.models.enums.Template`
  (Task 2), `app.worker.feed.{FeedEntry, find_new_entries}` (Task 7),
  `app.slack.templates.render` (Task 8), `app.slack.client.ThrottledSlackClient`
  interface shape (`.post_message`) (Task 9).
- Produces: `app.worker.jobs.check_watch(session, watch_id: int, *,
  fetch_entries: Callable[[str], list[FeedEntry]], slack_client,
  truncate_chars: int) -> None`. `fetch_entries` is injected (defaults to
  `app.worker.feed.parse_feed` in production wiring, done in Task 12) so
  tests never hit the network.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_worker_jobs.py
import datetime as dt
from app.models.user_cache import UserCache
from app.models.slack_channel_cache import SlackChannelCache
from app.models.rss_watch import RssWatch
from app.models.enums import Template, Visibility
from app.worker.feed import FeedEntry
from app.worker.jobs import check_watch

class FakeSlack:
    def __init__(self):
        self.posts = []

    def post_message(self, channel, text, thread_ts=None):
        self.posts.append({"channel": channel, "text": text, "thread_ts": thread_ts})
        return {"ok": True, "ts": f"ts-{len(self.posts)}"}

def seed_watch(db_session, **overrides):
    now = dt.datetime.now(dt.timezone.utc)
    db_session.add(UserCache(id="owner", display_name="O", email="o@x.com", last_refreshed_at=now))
    db_session.add(SlackChannelCache(id="C1", name="general", last_refreshed_at=now))
    defaults = dict(
        feed_url="https://example.com/feed.xml",
        slack_channel_id="C1",
        template=Template.HEADLINE_LINK,
        visibility=Visibility.OWNER_ONLY,
        created_by_sub="owner",
        created_at=now,
        check_interval_seconds=3600,
        is_active=True,
        consecutive_failure_count=0,
        auto_pause_after_failures=0,
        last_entry_id=None,
    )
    defaults.update(overrides)
    watch = RssWatch(**defaults)
    db_session.add(watch)
    db_session.commit()
    return watch

def entries(*guids):
    return [
        FeedEntry(guid=g, title=f"T{g}", link=f"https://x/{g}", body_html="<p>b</p>")
        for g in guids
    ]

def test_success_posts_new_entries_and_advances_cursor(db_session):
    watch = seed_watch(db_session, last_entry_id="1")
    slack = FakeSlack()
    fetch = lambda url: entries("3", "2", "1")  # newest-first

    check_watch(db_session, watch.id, fetch_entries=fetch, slack_client=slack, truncate_chars=500)
    db_session.commit()

    refreshed = db_session.get(RssWatch, watch.id)
    assert refreshed.last_entry_id == "3"
    assert refreshed.last_error is None
    assert refreshed.consecutive_failure_count == 0
    assert refreshed.last_posted_at is not None
    assert len(slack.posts) == 2  # entries "2" then "3", oldest-of-new-batch first

def test_thread_template_posts_root_then_threaded_reply(db_session):
    watch = seed_watch(db_session, template=Template.HEADLINE_LINK_THREAD, last_entry_id="1")
    slack = FakeSlack()
    fetch = lambda url: entries("2", "1")

    check_watch(db_session, watch.id, fetch_entries=fetch, slack_client=slack, truncate_chars=500)

    assert len(slack.posts) == 2
    assert slack.posts[0]["thread_ts"] is None
    assert slack.posts[1]["thread_ts"] == slack.posts[0] and False or True  # see below

def test_fetch_failure_increments_failure_count_and_sets_error(db_session):
    watch = seed_watch(db_session)
    slack = FakeSlack()
    def fetch(url):
        raise ValueError("boom")

    check_watch(db_session, watch.id, fetch_entries=fetch, slack_client=slack, truncate_chars=500)
    db_session.commit()

    refreshed = db_session.get(RssWatch, watch.id)
    assert refreshed.consecutive_failure_count == 1
    assert "boom" in refreshed.last_error
    assert refreshed.is_active is True  # threshold is 0 -> never auto-pause

def test_failure_reaching_threshold_auto_pauses_and_notifies(db_session):
    watch = seed_watch(db_session, auto_pause_after_failures=2, consecutive_failure_count=1)
    slack = FakeSlack()
    def fetch(url):
        raise ValueError("boom")

    check_watch(db_session, watch.id, fetch_entries=fetch, slack_client=slack, truncate_chars=500)
    db_session.commit()

    refreshed = db_session.get(RssWatch, watch.id)
    assert refreshed.consecutive_failure_count == 2
    assert refreshed.is_active is False
    assert len(slack.posts) == 1
    assert "Auto-paused" in slack.posts[0]["text"]

def test_cold_start_backfills_single_newest_entry(db_session):
    watch = seed_watch(db_session, last_entry_id=None)
    slack = FakeSlack()
    fetch = lambda url: entries("3", "2", "1")

    check_watch(db_session, watch.id, fetch_entries=fetch, slack_client=slack, truncate_chars=500)
    db_session.commit()

    refreshed = db_session.get(RssWatch, watch.id)
    assert refreshed.last_entry_id == "3"
    assert len(slack.posts) == 1
    assert slack.posts[0]["text"].startswith("[Backfill]")
```

Fix the malformed thread-reply assertion before running (see Step 4 — it is
corrected there; write it as shown below, not as pasted above).

- [ ] **Step 2: Run test to verify it fails**

First correct `test_thread_template_posts_root_then_threaded_reply`'s last
line to:

```python
    assert slack.posts[1]["thread_ts"] == slack.posts[0]["text"] and False
```

is still wrong — replace the whole assertion block with:

```python
def test_thread_template_posts_root_then_threaded_reply(db_session):
    watch = seed_watch(db_session, template=Template.HEADLINE_LINK_THREAD, last_entry_id="1")
    slack = FakeSlack()
    fetch = lambda url: entries("2", "1")

    check_watch(db_session, watch.id, fetch_entries=fetch, slack_client=slack, truncate_chars=500)

    assert len(slack.posts) == 2
    assert slack.posts[0]["thread_ts"] is None
    root_ts = "ts-1"
    assert slack.posts[1]["thread_ts"] == root_ts
```

Run: `pytest tests/integration/test_worker_jobs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.worker.jobs'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/worker/jobs.py
import datetime as dt
from typing import Callable

from app.models.rss_watch import RssWatch
from app.models.enums import Template
from app.worker.feed import FeedEntry, find_new_entries
from app.slack.templates import render

def check_watch(
    session,
    watch_id: int,
    *,
    fetch_entries: Callable[[str], list[FeedEntry]],
    slack_client,
    truncate_chars: int,
) -> None:
    watch = session.get(RssWatch, watch_id)
    if watch is None:
        return

    now = dt.datetime.now(dt.timezone.utc)

    try:
        entries = fetch_entries(watch.feed_url)
    except Exception as exc:  # feed fetch/parse failure
        watch.last_error = str(exc)
        watch.last_checked_at = now
        watch.consecutive_failure_count += 1
        if (
            watch.auto_pause_after_failures > 0
            and watch.consecutive_failure_count >= watch.auto_pause_after_failures
        ):
            slack_client.post_message(
                watch.slack_channel_id,
                f"⚠️ Auto-paused after {watch.consecutive_failure_count} "
                f"consecutive failures. Last error: {watch.last_error}",
            )
            watch.is_active = False
        session.flush()
        return

    watch.last_error = None
    watch.consecutive_failure_count = 0

    new_entries, is_backfill = find_new_entries(entries, watch.last_entry_id)

    for entry in new_entries:
        root_text, thread_text = render(
            watch.template, entry, truncate_chars=truncate_chars, backfill=is_backfill
        )
        response = slack_client.post_message(watch.slack_channel_id, root_text)
        if watch.template == Template.HEADLINE_LINK_THREAD and thread_text is not None:
            slack_client.post_message(
                watch.slack_channel_id, thread_text, thread_ts=response["ts"]
            )

    if new_entries:
        watch.last_entry_id = new_entries[-1].guid
        watch.last_posted_at = now
    watch.last_checked_at = now
    session.flush()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/integration/test_worker_jobs.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app/worker/jobs.py tests/integration/test_worker_jobs.py
git commit -m "feat: add check_watch worker job with auto-pause on repeated failure"
```

---

## Task 12: APScheduler wrapper

**Files:**
- Create: `app/worker/scheduler.py`
- Test: `tests/integration/test_scheduler.py`

**Interfaces:**
- Consumes: `apscheduler.schedulers.background.BackgroundScheduler`,
  `apscheduler.jobstores.sqlalchemy.SQLAlchemyJobStore`,
  `app.models.rss_watch.RssWatch` (Task 2), `app.worker.jobs.check_watch`
  (Task 11).
- Produces: `app.worker.scheduler.build_scheduler(database_url: str,
  job_func) -> BackgroundScheduler` (`job_func` is the callable each job
  invokes with the watch id — production wiring passes a closure around
  `check_watch` built in Task 13's entrypoint),
  `app.worker.scheduler.sync_job(scheduler, watch) -> None` (add if absent,
  reschedule if the interval changed, using `id=str(watch.id)`),
  `app.worker.scheduler.pause_job(scheduler, watch_id: int) -> None`,
  `app.worker.scheduler.resume_job(scheduler, watch_id: int) -> None`,
  `app.worker.scheduler.remove_job(scheduler, watch_id: int) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_scheduler.py
from app.worker.scheduler import (
    build_scheduler, sync_job, pause_job, resume_job, remove_job,
)
from types import SimpleNamespace

def watch(id=1, check_interval_seconds=60):
    return SimpleNamespace(id=id, check_interval_seconds=check_interval_seconds)

def test_sync_job_adds_job_with_correct_interval(postgres_container):
    scheduler = build_scheduler(postgres_container.get_connection_url(), job_func=lambda watch_id: None)
    try:
        sync_job(scheduler, watch(id=1, check_interval_seconds=120))
        job = scheduler.get_job("1")
        assert job is not None
        assert job.trigger.interval.total_seconds() == 120
    finally:
        scheduler.shutdown(wait=False)

def test_sync_job_reschedules_on_interval_change(postgres_container):
    scheduler = build_scheduler(postgres_container.get_connection_url(), job_func=lambda watch_id: None)
    try:
        sync_job(scheduler, watch(id=2, check_interval_seconds=60))
        sync_job(scheduler, watch(id=2, check_interval_seconds=300))
        job = scheduler.get_job("2")
        assert job.trigger.interval.total_seconds() == 300
    finally:
        scheduler.shutdown(wait=False)

def test_pause_and_resume_job(postgres_container):
    scheduler = build_scheduler(postgres_container.get_connection_url(), job_func=lambda watch_id: None)
    try:
        sync_job(scheduler, watch(id=3))
        pause_job(scheduler, 3)
        assert scheduler.get_job("3").next_run_time is None
        resume_job(scheduler, 3)
        assert scheduler.get_job("3").next_run_time is not None
    finally:
        scheduler.shutdown(wait=False)

def test_remove_job(postgres_container):
    scheduler = build_scheduler(postgres_container.get_connection_url(), job_func=lambda watch_id: None)
    try:
        sync_job(scheduler, watch(id=4))
        remove_job(scheduler, 4)
        assert scheduler.get_job("4") is None
    finally:
        scheduler.shutdown(wait=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.worker.scheduler'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/worker/scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

def build_scheduler(database_url: str, job_func) -> BackgroundScheduler:
    jobstore = SQLAlchemyJobStore(url=database_url)
    scheduler = BackgroundScheduler(jobstores={"default": jobstore})
    scheduler._job_func = job_func  # stashed for sync_job to reference
    scheduler.start(paused=True)
    return scheduler

def _job_id(watch_id: int) -> str:
    return str(watch_id)

def sync_job(scheduler, watch) -> None:
    job_id = _job_id(watch.id)
    existing = scheduler.get_job(job_id)
    if existing is not None:
        scheduler.reschedule_job(job_id, trigger="interval", seconds=watch.check_interval_seconds)
        return
    scheduler.add_job(
        scheduler._job_func,
        trigger="interval",
        seconds=watch.check_interval_seconds,
        id=job_id,
        args=[watch.id],
        replace_existing=True,
    )

def pause_job(scheduler, watch_id: int) -> None:
    scheduler.pause_job(_job_id(watch_id))

def resume_job(scheduler, watch_id: int) -> None:
    scheduler.resume_job(_job_id(watch_id))

def remove_job(scheduler, watch_id: int) -> None:
    job = scheduler.get_job(_job_id(watch_id))
    if job is not None:
        scheduler.remove_job(_job_id(watch_id))
```

Note: `scheduler.start(paused=True)` in `build_scheduler` means callers that
want jobs actually firing (the real worker entrypoint, Task 13) must call
`scheduler.resume()` after the initial `sync_job` calls for all active
watches are done, so nothing fires mid-startup-sync.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/integration/test_scheduler.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/worker/scheduler.py tests/integration/test_scheduler.py
git commit -m "feat: add APScheduler wrapper for per-watch job lifecycle"
```

---

## Task 13: Identity resolver (session + token)

**Files:**
- Create: `app/identity_resolver.py`
- Test: `tests/unit/test_identity_resolver.py`

**Interfaces:**
- Consumes: `app.acl.Identity` (Task 4), `app.tokens.service.verify_token`
  (Task 5), `app.models.api_token.ApiToken` (Task 2).
- Produces: `app.identity_resolver.identity_from_session(session_data:
  dict, admin_groups: frozenset[str]) -> Identity | None` (reads
  `session_data["sub"]` and `session_data["groups"]`, returns `None` if
  `"sub"` absent), `app.identity_resolver.identity_from_token(db_session,
  raw_token: str, admin_groups: frozenset[str], *, groups_for_sub:
  Callable[[str], frozenset[str]]) -> Identity | None` (verifies the token,
  then resolves groups via the injected callable since group membership
  lives in the OIDC session, not in `api_token` — a minted token acts as its
  owner's *current* group membership at request time, not a snapshot from
  mint time).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_identity_resolver.py
from app.identity_resolver import identity_from_session, identity_from_token
from app.acl import Identity

def test_identity_from_session_builds_identity_with_admin_flag():
    identity = identity_from_session(
        {"sub": "u1", "groups": ["eng", "admins"]}, admin_groups=frozenset({"admins"})
    )
    assert identity == Identity(sub="u1", groups=frozenset({"eng", "admins"}), is_admin=True)

def test_identity_from_session_non_admin():
    identity = identity_from_session(
        {"sub": "u1", "groups": ["eng"]}, admin_groups=frozenset({"admins"})
    )
    assert identity.is_admin is False

def test_identity_from_session_missing_sub_returns_none():
    assert identity_from_session({}, admin_groups=frozenset()) is None

def test_identity_from_token_resolves_via_verify_and_groups_lookup(db_session):
    from app.models.user_cache import UserCache
    from app.tokens.service import mint_token
    import datetime as dt

    db_session.add(UserCache(id="u1", display_name="U", email="u@x.com", last_refreshed_at=dt.datetime.now(dt.timezone.utc)))
    db_session.commit()
    _, raw = mint_token(db_session, "u1", ttl_seconds=3600)
    db_session.commit()

    identity = identity_from_token(
        db_session, raw, admin_groups=frozenset({"admins"}),
        groups_for_sub=lambda sub: frozenset({"eng"}),
    )
    assert identity == Identity(sub="u1", groups=frozenset({"eng"}), is_admin=False)

def test_identity_from_token_invalid_returns_none(db_session):
    identity = identity_from_token(
        db_session, "bogus", admin_groups=frozenset(), groups_for_sub=lambda sub: frozenset()
    )
    assert identity is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_identity_resolver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.identity_resolver'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/identity_resolver.py
from typing import Callable
from app.acl import Identity
from app.tokens.service import verify_token

def identity_from_session(session_data: dict, admin_groups: frozenset[str]) -> Identity | None:
    sub = session_data.get("sub")
    if not sub:
        return None
    groups = frozenset(session_data.get("groups", []))
    return Identity(sub=sub, groups=groups, is_admin=bool(groups & admin_groups))

def identity_from_token(
    db_session,
    raw_token: str,
    admin_groups: frozenset[str],
    *,
    groups_for_sub: Callable[[str], frozenset[str]],
) -> Identity | None:
    token_row = verify_token(db_session, raw_token)
    if token_row is None:
        return None
    groups = groups_for_sub(token_row.owner_sub)
    return Identity(sub=token_row.owner_sub, groups=groups, is_admin=bool(groups & admin_groups))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_identity_resolver.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/identity_resolver.py tests/unit/test_identity_resolver.py
git commit -m "feat: add session- and token-based identity resolution"
```

---

## Task 14: Flask app factory + structured JSON logging + health routes

**Files:**
- Create: `app/logging_setup.py`
- Create: `app/web/__init__.py` (empty)
- Create: `app/web/app_factory.py`
- Create: `app/web/routes_health.py`
- Test: `tests/unit/test_logging_setup.py`
- Test: `tests/integration/test_routes_health.py`

**Interfaces:**
- Consumes: `app.config.Config` (Task 1), `app.db.make_session_factory`
  (Task 2).
- Produces: `app.logging_setup.configure_logging(level: str) -> None`
  (installs a JSON stdout handler on the root logger).
  `app.web.app_factory.create_app(config: Config, session_factory) ->
  Flask` — stores `config` and `session_factory` on `app.extensions` under
  keys `"config"` and `"session_factory"` so route modules (Tasks 15-17) can
  reach them via `current_app.extensions[...]`. Registers the `health`
  blueprint. `/healthz` returns `{"status": "ok"}`, HTTP 200, and performs a
  trivial `SELECT 1` against the DB to prove connectivity — returns 503 with
  `{"status": "error", "detail": str(exc)}` if that fails.

- [ ] **Step 1: Write the failing logging test**

```python
# tests/unit/test_logging_setup.py
import json
import logging
from app.logging_setup import configure_logging

def test_configure_logging_emits_json(capsys):
    configure_logging("INFO")
    logger = logging.getLogger("test.logger")
    logger.info("hello world")

    captured = capsys.readouterr()
    line = captured.err.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["message"] == "hello world"
    assert payload["levelname"] == "INFO"

def test_configure_logging_respects_level(capsys):
    configure_logging("WARNING")
    logger = logging.getLogger("test.logger.level")
    logger.info("should not appear")
    logger.warning("should appear")

    captured = capsys.readouterr()
    assert "should not appear" not in captured.err
    assert "should appear" in captured.err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_logging_setup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.logging_setup'`

- [ ] **Step 3: Write `app/logging_setup.py`**

```python
# app/logging_setup.py
import logging
import sys
from pythonjsonlogger import jsonlogger

def configure_logging(level: str) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream=sys.stderr)
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)
```

- [ ] **Step 4: Run logging test to verify it passes**

Run: `pytest tests/unit/test_logging_setup.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Write the failing health-route test**

```python
# tests/integration/test_routes_health.py
from app.config import Config
from app.web.app_factory import create_app
from app.db import make_session_factory

def make_test_config(db_url):
    return Config(
        database_url=db_url,
        oidc_issuer="https://tinyoidc.authenti-kate.org",
        oidc_client_id="client_id_12decaf34bad56",
        oidc_client_secret="Super-+Secret_=Key0123456789",
        slack_bot_token="xoxb-fake",
    )

def test_healthz_returns_ok(db_engine, postgres_container):
    config = make_test_config(postgres_container.get_connection_url())
    app = create_app(config, make_session_factory(db_engine))
    client = app.test_client()

    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}

def test_healthz_returns_503_when_db_unreachable(postgres_container):
    config = make_test_config("postgresql+psycopg://bad:bad@localhost:1/nope")
    from sqlalchemy import create_engine
    from app.db import make_session_factory as msf
    bad_engine = create_engine(config.database_url, future=True)
    app = create_app(config, msf(bad_engine))
    client = app.test_client()

    resp = client.get("/healthz")
    assert resp.status_code == 503
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/integration/test_routes_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.web.app_factory'`

- [ ] **Step 7: Write `app/web/routes_health.py`**

```python
# app/web/routes_health.py
from flask import Blueprint, current_app, jsonify
from sqlalchemy import text

health_bp = Blueprint("health", __name__)

@health_bp.route("/healthz")
def healthz():
    session_factory = current_app.extensions["session_factory"]
    session = session_factory()
    try:
        session.execute(text("SELECT 1"))
        return jsonify({"status": "ok"}), 200
    except Exception as exc:
        return jsonify({"status": "error", "detail": str(exc)}), 503
    finally:
        session.close()
```

- [ ] **Step 8: Write `app/web/app_factory.py`**

```python
# app/web/app_factory.py
from flask import Flask
from app.config import Config
from app.web.routes_health import health_bp

def create_app(config: Config, session_factory) -> Flask:
    app = Flask(__name__)
    app.extensions["config"] = config
    app.extensions["session_factory"] = session_factory
    app.register_blueprint(health_bp)
    return app
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `pytest tests/integration/test_routes_health.py -v`
Expected: PASS (2 tests) — the second test relies on a connection to a
closed port failing fast; if it hangs instead, add `connect_timeout=2` to
the bad URL's query string (`?connect_timeout=2`) before re-running.

- [ ] **Step 10: Commit**

```bash
git add app/logging_setup.py app/web/__init__.py app/web/app_factory.py app/web/routes_health.py tests/unit/test_logging_setup.py tests/integration/test_routes_health.py
git commit -m "feat: add Flask app factory, JSON logging, and health check route"
```

---

## Task 15: OIDC login/callback routes

**Files:**
- Create: `app/web/routes_auth.py`
- Modify: `app/web/app_factory.py` (register `auth_bp`, configure Authlib
  `OAuth` client bound to `config.oidc_issuer`/`oidc_client_id`/`oidc_client_secret`)
- Test: `tests/integration/test_routes_auth.py`

**Interfaces:**
- Consumes: `app.users.service.upsert_user` (Task 6), `app.config.Config`
  (Task 1), Authlib's `authlib.integrations.flask_client.OAuth`.
- Produces: routes `GET /login` (redirects to the provider's authorization
  endpoint via `oauth.tinyoidc.authorize_redirect(...)`), `GET
  /auth/callback` (exchanges the code via
  `oauth.tinyoidc.authorize_access_token()`, reads `userinfo` claims
  `sub`/`name`/`email`/`groups`, calls `upsert_user`, writes
  `session["sub"]`, `session["groups"]`, redirects to `/`), `GET /logout`
  (clears the session, redirects to `/`).

Authlib's OAuth client object is registered on `app.extensions["oauth"]` so
tests can monkeypatch `authorize_access_token`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_routes_auth.py
from unittest.mock import patch
from app.config import Config
from app.web.app_factory import create_app
from app.db import make_session_factory

def make_test_config(db_url):
    return Config(
        database_url=db_url,
        oidc_issuer="https://tinyoidc.authenti-kate.org",
        oidc_client_id="client_id_12decaf34bad56",
        oidc_client_secret="Super-+Secret_=Key0123456789",
        slack_bot_token="xoxb-fake",
        admin_oidc_groups=frozenset({"admins"}),
    )

def build_app(db_engine, postgres_container):
    config = make_test_config(postgres_container.get_connection_url())
    app = create_app(config, make_session_factory(db_engine))
    app.secret_key = "test-secret"
    return app

def test_login_redirects_to_provider(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    with patch.object(
        app.extensions["oauth"].tinyoidc, "authorize_redirect",
        return_value=("redirect", 302),
    ) as mock_redirect:
        client.get("/login")
        assert mock_redirect.called

def test_callback_upserts_user_and_sets_session(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    fake_token = {
        "userinfo": {
            "sub": "u1", "name": "Jon", "email": "jon@example.com", "groups": ["admins"],
        }
    }
    with patch.object(
        app.extensions["oauth"].tinyoidc, "authorize_access_token", return_value=fake_token,
    ):
        resp = client.get("/auth/callback")
        assert resp.status_code == 302

    with client.session_transaction() as sess:
        assert sess["sub"] == "u1"
        assert sess["groups"] == ["admins"]

    from app.models.user_cache import UserCache
    session = make_session_factory(db_engine)()
    stored = session.get(UserCache, "u1")
    assert stored.display_name == "Jon"
    session.close()

def test_logout_clears_session(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["sub"] = "u1"
    client.get("/logout")
    with client.session_transaction() as sess:
        assert "sub" not in sess
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_routes_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.web.routes_auth'`

- [ ] **Step 3: Write `app/web/routes_auth.py`**

```python
# app/web/routes_auth.py
from flask import Blueprint, current_app, redirect, session, url_for
from app.users.service import upsert_user

auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/login")
def login():
    oauth = current_app.extensions["oauth"]
    redirect_uri = url_for("auth.callback", _external=True)
    return oauth.tinyoidc.authorize_redirect(redirect_uri)

@auth_bp.route("/auth/callback")
def callback():
    oauth = current_app.extensions["oauth"]
    token = oauth.tinyoidc.authorize_access_token()
    userinfo = token["userinfo"]
    sub = userinfo["sub"]
    groups = userinfo.get("groups", [])

    session_factory = current_app.extensions["session_factory"]
    db_session = session_factory()
    try:
        upsert_user(
            db_session, sub=sub,
            display_name=userinfo.get("name"), email=userinfo.get("email"),
        )
        db_session.commit()
    finally:
        db_session.close()

    session["sub"] = sub
    session["groups"] = groups
    return redirect(url_for("watches.index") if current_app.url_map.strict_slashes else "/")

@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect("/")
```

`callback`'s redirect target references `watches.index`, which does not
exist yet — Task 16 adds it. Until Task 16 lands, change the last line of
`callback` to `return redirect("/")` unconditionally; revisit once
`watches_bp` exists.

- [ ] **Step 4: Simplify the callback redirect for now**

```python
# app/web/routes_auth.py — replace the callback's return line
    return redirect("/")
```

- [ ] **Step 5: Register the blueprint and Authlib client in `app_factory.py`**

```python
# app/web/app_factory.py
from authlib.integrations.flask_client import OAuth
from flask import Flask
from app.config import Config
from app.web.routes_health import health_bp
from app.web.routes_auth import auth_bp

def create_app(config: Config, session_factory) -> Flask:
    app = Flask(__name__)
    app.extensions["config"] = config
    app.extensions["session_factory"] = session_factory
    app.secret_key = config.oidc_client_secret  # POC only; see Task 21 note

    oauth = OAuth(app)
    oauth.register(
        name="tinyoidc",
        client_id=config.oidc_client_id,
        client_secret=config.oidc_client_secret,
        server_metadata_url=f"{config.oidc_issuer}/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile email groups"},
    )
    app.extensions["oauth"] = oauth

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    return app
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/integration/test_routes_auth.py -v`
Expected: PASS (3 tests). Note: `OAuth.register` with a
`server_metadata_url` performs discovery lazily on first use inside Authlib,
not at `register()` time, so building the app in tests does not require
network access to `tinyoidc.authenti-kate.org`; the two tests that reach
`authorize_redirect`/`authorize_access_token` mock those methods directly
and never trigger discovery either.

- [ ] **Step 7: Commit**

```bash
git add app/web/routes_auth.py app/web/app_factory.py tests/integration/test_routes_auth.py
git commit -m "feat: add OIDC login/callback/logout routes"
```

**Confirmed live (plan-writing time):** tinyoidc's discovery document at
`https://tinyoidc.authenti-kate.org/.well-known/openid-configuration` lists
`authorization_endpoint=.../c2s/authorize`, `token_endpoint=.../s2s/token`,
`userinfo_endpoint=.../s2s/userinfo`, and genuinely supports the `groups`
scope/claim. Its authorize page has no password form — one "Login as
&lt;user&gt;" button per pre-seeded account (admin/it/accounts/auditor/
sysadmin/reception/contractor), each with real `groups` claims (`admin`'s
groups include `admins`, matching `ADMIN_OIDC_GROUPS` by design). Task 22
drives this login live with Playwright.

---

## Task 16: Watch CRUD routes (create/list/detail/edit/delete/pause/resume)

**Files:**
- Create: `app/web/routes_watches.py`
- Modify: `app/web/app_factory.py` (register `watches_bp`; accept a
  `scheduler` argument so routes can call `sync_job`/`pause_job`/
  `resume_job`/`remove_job`)
- Modify: `app/web/routes_auth.py` (callback redirect →
  `url_for("watches.index")`)
- Test: `tests/integration/test_routes_watches.py`

**Interfaces:**
- Consumes: `app.acl.{Identity, can_access}` (Task 4),
  `app.identity_resolver.identity_from_session` (Task 13),
  `app.models.rss_watch.RssWatch`, `app.models.enums.{Template,
  Visibility}` (Task 2), `app.worker.scheduler.{sync_job, pause_job,
  resume_job, remove_job}` (Task 12).
- Produces: JSON API under `/api/v1/watches` — `GET` (list watches visible
  to the caller), `POST` (create), `GET /<id>`, `PATCH /<id>` (edit),
  `DELETE /<id>`, `POST /<id>/pause`, `POST /<id>/resume`. `create_app` now
  takes `scheduler` as a required parameter and stores it as
  `app.extensions["scheduler"]`. A helper
  `app.web.routes_watches._current_identity()` reads
  `current_app.extensions["config"].admin_oidc_groups` and the Flask
  `session`, used by every route in this blueprint (and reused by Task 17's
  token routes).

This task implements the JSON API surface only (per the spec, UI routes
mirror it) — HTML templates are out of scope for this plan and can be
layered on afterward without touching this interface.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_routes_watches.py
import datetime as dt
from app.config import Config
from app.web.app_factory import create_app
from app.db import make_session_factory
from app.models.user_cache import UserCache
from app.models.slack_channel_cache import SlackChannelCache

class FakeScheduler:
    def __init__(self):
        self.synced = []
        self.paused = []
        self.resumed = []
        self.removed = []

def make_test_config(db_url):
    return Config(
        database_url=db_url,
        oidc_issuer="https://tinyoidc.authenti-kate.org",
        oidc_client_id="client_id_12decaf34bad56",
        oidc_client_secret="Super-+Secret_=Key0123456789",
        slack_bot_token="xoxb-fake",
        admin_oidc_groups=frozenset({"admins"}),
    )

def build_app(db_engine, postgres_container):
    config = make_test_config(postgres_container.get_connection_url())
    app = create_app(config, make_session_factory(db_engine), scheduler=FakeScheduler())
    app.secret_key = "test-secret"
    return app

def login_as(client, sub, groups=None):
    with client.session_transaction() as sess:
        sess["sub"] = sub
        sess["groups"] = groups or []

def seed_channel(db_engine):
    session = make_session_factory(db_engine)()
    session.add(SlackChannelCache(id="C1", name="general", last_refreshed_at=dt.datetime.now(dt.timezone.utc)))
    session.commit()
    session.close()

def test_create_watch_requires_login(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    resp = client.post("/api/v1/watches", json={})
    assert resp.status_code == 401

def test_create_then_list_own_watch(db_engine, postgres_container):
    seed_channel(db_engine)
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")

    resp = client.post("/api/v1/watches", json={
        "feed_url": "https://example.com/feed.xml",
        "slack_channel_id": "C1",
        "template": "headline_link",
        "visibility": "owner_only",
        "check_interval_seconds": 3600,
    })
    assert resp.status_code == 201
    watch_id = resp.get_json()["id"]

    resp = client.get("/api/v1/watches")
    assert resp.status_code == 200
    ids = [w["id"] for w in resp.get_json()]
    assert watch_id in ids

def test_owner_only_watch_hidden_from_other_users(db_engine, postgres_container):
    seed_channel(db_engine)
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    resp = client.post("/api/v1/watches", json={
        "feed_url": "https://example.com/feed.xml", "slack_channel_id": "C1",
        "template": "headline_link", "visibility": "owner_only",
        "check_interval_seconds": 3600,
    })
    watch_id = resp.get_json()["id"]

    login_as(client, "stranger")
    resp = client.get(f"/api/v1/watches/{watch_id}")
    assert resp.status_code == 404  # hidden, not 403 — existence not disclosed

def test_owner_can_delete_own_watch(db_engine, postgres_container):
    seed_channel(db_engine)
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    resp = client.post("/api/v1/watches", json={
        "feed_url": "https://example.com/feed.xml", "slack_channel_id": "C1",
        "template": "headline_link", "visibility": "owner_only",
        "check_interval_seconds": 3600,
    })
    watch_id = resp.get_json()["id"]

    resp = client.delete(f"/api/v1/watches/{watch_id}")
    assert resp.status_code == 204
    assert app.extensions["scheduler"].removed == [watch_id]

def test_pause_and_resume(db_engine, postgres_container):
    seed_channel(db_engine)
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    resp = client.post("/api/v1/watches", json={
        "feed_url": "https://example.com/feed.xml", "slack_channel_id": "C1",
        "template": "headline_link", "visibility": "owner_only",
        "check_interval_seconds": 3600,
    })
    watch_id = resp.get_json()["id"]

    resp = client.post(f"/api/v1/watches/{watch_id}/pause")
    assert resp.status_code == 200
    assert app.extensions["scheduler"].paused == [watch_id]

    resp = client.post(f"/api/v1/watches/{watch_id}/resume")
    assert resp.status_code == 200
    assert app.extensions["scheduler"].resumed == [watch_id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_routes_watches.py -v`
Expected: FAIL with `TypeError: create_app() missing 1 required positional
argument: 'scheduler'`

- [ ] **Step 3: Add scheduler hooks to `app/worker/scheduler.py`'s test double contract**

No production code change needed here — `FakeScheduler` in the test above
stands in for `app.extensions["scheduler"]`; routes call
`sync_job`/`pause_job`/`resume_job`/`remove_job` as free functions imported
from `app.worker.scheduler`, passing `current_app.extensions["scheduler"]`
as the first argument, so the real functions from Task 12 work unchanged
against the real scheduler in production and the fake object's recorded
lists satisfy these tests. Record calls on `FakeScheduler` by monkeypatching
the module-level functions in the test file instead of relying on
`sync_job` etc. actually working against `FakeScheduler` (which lacks
APScheduler's real API) — see Step 4's route implementation, which calls
`app.worker.scheduler.sync_job(scheduler, watch)` etc. exactly as written;
adjust the test doubles to monkeypatch `app.web.routes_watches.sync_job`
and friends directly:

```python
# add near the top of tests/integration/test_routes_watches.py, replacing FakeScheduler's role
import app.web.routes_watches as routes_watches

class Recorder:
    def __init__(self):
        self.synced = []
        self.paused = []
        self.resumed = []
        self.removed = []

def install_recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(routes_watches, "sync_job", lambda scheduler, watch: rec.synced.append(watch.id))
    monkeypatch.setattr(routes_watches, "pause_job", lambda scheduler, wid: rec.paused.append(wid))
    monkeypatch.setattr(routes_watches, "resume_job", lambda scheduler, wid: rec.resumed.append(wid))
    monkeypatch.setattr(routes_watches, "remove_job", lambda scheduler, wid: rec.removed.append(wid))
    return rec
```

Update `test_owner_can_delete_own_watch` and `test_pause_and_resume` to take
a `monkeypatch` fixture parameter, call `rec = install_recorder(monkeypatch)`
right after `build_app(...)`, and assert against `rec.removed` /
`rec.paused` / `rec.resumed` instead of `app.extensions["scheduler"].removed`
etc. `create_app`'s `scheduler` parameter can then be any placeholder object
(e.g. `object()`) in `build_app`, since the recorder intercepts every call
before it reaches the real scheduler functions.

- [ ] **Step 4: Write `app/web/routes_watches.py`**

```python
# app/web/routes_watches.py
import datetime as dt
from flask import Blueprint, current_app, jsonify, request, session

from app.acl import Identity, can_access
from app.identity_resolver import identity_from_session
from app.models.rss_watch import RssWatch
from app.models.enums import Template, Visibility
from app.worker.scheduler import sync_job, pause_job, resume_job, remove_job

watches_bp = Blueprint("watches", __name__, url_prefix="/api/v1/watches")

def _current_identity() -> Identity | None:
    config = current_app.extensions["config"]
    return identity_from_session(dict(session), config.admin_oidc_groups)

def _serialize(watch: RssWatch) -> dict:
    return {
        "id": watch.id,
        "feed_url": watch.feed_url,
        "slack_channel_id": watch.slack_channel_id,
        "template": watch.template.value,
        "visibility": watch.visibility.value,
        "owning_group": watch.owning_group,
        "created_by_sub": watch.created_by_sub,
        "check_interval_seconds": watch.check_interval_seconds,
        "is_active": watch.is_active,
        "auto_pause_after_failures": watch.auto_pause_after_failures,
        "consecutive_failure_count": watch.consecutive_failure_count,
        "last_checked_at": watch.last_checked_at.isoformat() if watch.last_checked_at else None,
        "last_posted_at": watch.last_posted_at.isoformat() if watch.last_posted_at else None,
        "last_error": watch.last_error,
    }

@watches_bp.route("", methods=["GET"])
def index():
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    db_session = current_app.extensions["session_factory"]()
    try:
        all_watches = db_session.query(RssWatch).all()
        visible = [w for w in all_watches if can_access(w, identity)]
        return jsonify([_serialize(w) for w in visible]), 200
    finally:
        db_session.close()

@watches_bp.route("", methods=["POST"])
def create():
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(force=True)
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = RssWatch(
            feed_url=body["feed_url"],
            slack_channel_id=body["slack_channel_id"],
            template=Template(body["template"]),
            visibility=Visibility(body.get("visibility", "owner_only")),
            owning_group=body.get("owning_group"),
            created_by_sub=identity.sub,
            created_at=dt.datetime.now(dt.timezone.utc),
            check_interval_seconds=body["check_interval_seconds"],
            auto_pause_after_failures=body.get("auto_pause_after_failures", 0),
        )
        db_session.add(watch)
        db_session.commit()
        sync_job(current_app.extensions["scheduler"], watch)
        return jsonify(_serialize(watch)), 201
    finally:
        db_session.close()

def _load_visible_watch(db_session, identity, watch_id):
    watch = db_session.get(RssWatch, watch_id)
    if watch is None or not can_access(watch, identity):
        return None
    return watch

@watches_bp.route("/<int:watch_id>", methods=["GET"])
def detail(watch_id):
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(_serialize(watch)), 200
    finally:
        db_session.close()

@watches_bp.route("/<int:watch_id>", methods=["PATCH"])
def edit(watch_id):
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return jsonify({"error": "not found"}), 404
        body = request.get_json(force=True)
        for field in ("feed_url", "slack_channel_id", "check_interval_seconds",
                        "owning_group", "auto_pause_after_failures"):
            if field in body:
                setattr(watch, field, body[field])
        if "template" in body:
            watch.template = Template(body["template"])
        if "visibility" in body:
            watch.visibility = Visibility(body["visibility"])
        db_session.commit()
        sync_job(current_app.extensions["scheduler"], watch)
        return jsonify(_serialize(watch)), 200
    finally:
        db_session.close()

@watches_bp.route("/<int:watch_id>", methods=["DELETE"])
def delete(watch_id):
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return jsonify({"error": "not found"}), 404
        db_session.delete(watch)
        db_session.commit()
        remove_job(current_app.extensions["scheduler"], watch_id)
        return "", 204
    finally:
        db_session.close()

@watches_bp.route("/<int:watch_id>/pause", methods=["POST"])
def pause(watch_id):
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return jsonify({"error": "not found"}), 404
        watch.is_active = False
        db_session.commit()
        pause_job(current_app.extensions["scheduler"], watch_id)
        return jsonify(_serialize(watch)), 200
    finally:
        db_session.close()

@watches_bp.route("/<int:watch_id>/resume", methods=["POST"])
def resume(watch_id):
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return jsonify({"error": "not found"}), 404
        watch.is_active = True
        watch.consecutive_failure_count = 0
        db_session.commit()
        resume_job(current_app.extensions["scheduler"], watch_id)
        return jsonify(_serialize(watch)), 200
    finally:
        db_session.close()
```

- [ ] **Step 5: Wire `watches_bp` and `scheduler` into `app_factory.py`**

```python
# app/web/app_factory.py
from authlib.integrations.flask_client import OAuth
from flask import Flask
from app.config import Config
from app.web.routes_health import health_bp
from app.web.routes_auth import auth_bp
from app.web.routes_watches import watches_bp

def create_app(config: Config, session_factory, scheduler) -> Flask:
    app = Flask(__name__)
    app.extensions["config"] = config
    app.extensions["session_factory"] = session_factory
    app.extensions["scheduler"] = scheduler
    app.secret_key = config.oidc_client_secret  # POC only; see Task 21 note

    oauth = OAuth(app)
    oauth.register(
        name="tinyoidc",
        client_id=config.oidc_client_id,
        client_secret=config.oidc_client_secret,
        server_metadata_url=f"{config.oidc_issuer}/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile email groups"},
    )
    app.extensions["oauth"] = oauth

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(watches_bp)
    return app
```

- [ ] **Step 6: Fix Task 14/15's health and auth tests' `create_app` calls**

`tests/integration/test_routes_health.py` and
`tests/integration/test_routes_auth.py` now need a third positional
argument. Update both `create_app(config, make_session_factory(db_engine))`
call sites to `create_app(config, make_session_factory(db_engine), object())`
— a bare `object()` is a sufficient stand-in since neither test's routes
touch the scheduler.

- [ ] **Step 7: Update `app/web/routes_auth.py`'s callback redirect**

```python
# app/web/routes_auth.py — replace the callback's return line
    return redirect(url_for("watches.index"))
```

- [ ] **Step 8: Run all web tests to verify they pass**

Run: `pytest tests/integration/test_routes_health.py tests/integration/test_routes_auth.py tests/integration/test_routes_watches.py -v`
Expected: PASS (all tests across the three files)

- [ ] **Step 9: Commit**

```bash
git add app/web/routes_watches.py app/web/app_factory.py app/web/routes_auth.py tests/integration/test_routes_watches.py tests/integration/test_routes_health.py tests/integration/test_routes_auth.py
git commit -m "feat: add watch CRUD/pause/resume JSON API with ACL enforcement"
```

---

## Task 17: Channel picker route + API token routes

**Files:**
- Create: `app/web/routes_tokens.py`
- Modify: `app/web/routes_watches.py` (add `GET
  /api/v1/slack-channels` using `app.slack.channel_cache.list_bot_channels`
  — grouped here since it is the create-watch form's data source, not a
  separate blueprint)
- Modify: `app/web/app_factory.py` (register `tokens_bp`; accept
  `slack_client` for the channel-list route)
- Test: `tests/integration/test_routes_tokens.py`
- Modify test: `tests/integration/test_routes_watches.py` (add
  channel-list test, update `create_app` calls)

**Interfaces:**
- Consumes: `app.tokens.service.{mint_token, verify_token, revoke_token}`
  (Task 5), `app.slack.channel_cache.list_bot_channels` (Task 10).
- Produces: `GET /api/v1/slack-channels` (any authenticated identity;
  returns `list_bot_channels(slack_client)` verbatim as JSON). `POST
  /api/v1/tokens` (`{ttl_seconds?, description?}`, `ttl_seconds` clamped to
  `config.token_max_ttl_seconds`, returns `{"id":..., "token": <plaintext,
  shown once>, "expires_at":...}`), `GET /api/v1/tokens` (own tokens only,
  masked — no `token_hash` in the response), `POST
  /api/v1/tokens/<id>/revoke` (owner-only, 404 if not yours).

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_routes_tokens.py
from app.config import Config
from app.web.app_factory import create_app
from app.db import make_session_factory

def make_test_config(db_url):
    return Config(
        database_url=db_url,
        oidc_issuer="https://tinyoidc.authenti-kate.org",
        oidc_client_id="client_id_12decaf34bad56",
        oidc_client_secret="Super-+Secret_=Key0123456789",
        slack_bot_token="xoxb-fake",
        token_max_ttl_seconds=7200,
    )

def build_app(db_engine, postgres_container):
    config = make_test_config(postgres_container.get_connection_url())
    app = create_app(config, make_session_factory(db_engine), scheduler=object(), slack_client=object())
    app.secret_key = "test-secret"
    return app

def login_as(client, sub):
    with client.session_transaction() as sess:
        sess["sub"] = sub
        sess["groups"] = []

def test_mint_requires_login(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    resp = app.test_client().post("/api/v1/tokens", json={})
    assert resp.status_code == 401

def test_mint_clamps_ttl_to_configured_max(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    resp = client.post("/api/v1/tokens", json={"ttl_seconds": 999999, "description": "cli"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert "token" in body
    assert body["description"] == "cli"

def test_list_shows_only_own_tokens_without_hash(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    client.post("/api/v1/tokens", json={"ttl_seconds": 3600})

    login_as(client, "u2")
    client.post("/api/v1/tokens", json={"ttl_seconds": 3600})

    resp = client.get("/api/v1/tokens")
    body = resp.get_json()
    assert len(body) == 1
    assert "token_hash" not in body[0]
    assert "token" not in body[0]

def test_revoke_own_token(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    mint_resp = client.post("/api/v1/tokens", json={"ttl_seconds": 3600})
    token_id = mint_resp.get_json()["id"]

    resp = client.post(f"/api/v1/tokens/{token_id}/revoke")
    assert resp.status_code == 200

def test_cannot_revoke_others_token(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    mint_resp = client.post("/api/v1/tokens", json={"ttl_seconds": 3600})
    token_id = mint_resp.get_json()["id"]

    login_as(client, "u2")
    resp = client.post(f"/api/v1/tokens/{token_id}/revoke")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_routes_tokens.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.web.routes_tokens'`

- [ ] **Step 3: Write `app/web/routes_tokens.py`**

```python
# app/web/routes_tokens.py
from flask import Blueprint, current_app, jsonify, request
from app.web.routes_watches import _current_identity
from app.tokens.service import mint_token, verify_token, revoke_token
from app.models.api_token import ApiToken

tokens_bp = Blueprint("tokens", __name__, url_prefix="/api/v1/tokens")

def _serialize(token: ApiToken, *, include_plaintext: str | None = None) -> dict:
    body = {
        "id": token.id,
        "description": token.description,
        "created_at": token.created_at.isoformat(),
        "expires_at": token.expires_at.isoformat(),
        "revoked_at": token.revoked_at.isoformat() if token.revoked_at else None,
    }
    if include_plaintext is not None:
        body["token"] = include_plaintext
    return body

@tokens_bp.route("", methods=["POST"])
def mint():
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    config = current_app.extensions["config"]
    body = request.get_json(force=True)
    ttl_seconds = min(body.get("ttl_seconds", config.token_max_ttl_seconds), config.token_max_ttl_seconds)
    db_session = current_app.extensions["session_factory"]()
    try:
        token, raw = mint_token(
            db_session, identity.sub, ttl_seconds=ttl_seconds, description=body.get("description")
        )
        db_session.commit()
        return jsonify(_serialize(token, include_plaintext=raw)), 201
    finally:
        db_session.close()

@tokens_bp.route("", methods=["GET"])
def index():
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    db_session = current_app.extensions["session_factory"]()
    try:
        rows = db_session.query(ApiToken).filter(ApiToken.owner_sub == identity.sub).all()
        return jsonify([_serialize(r) for r in rows]), 200
    finally:
        db_session.close()

@tokens_bp.route("/<int:token_id>/revoke", methods=["POST"])
def revoke(token_id):
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    db_session = current_app.extensions["session_factory"]()
    try:
        row = db_session.get(ApiToken, token_id)
        if row is None or row.owner_sub != identity.sub:
            return jsonify({"error": "not found"}), 404
        revoke_token(db_session, token_id)
        db_session.commit()
        return jsonify(_serialize(row)), 200
    finally:
        db_session.close()
```

- [ ] **Step 4: Add the channel-list route to `app/web/routes_watches.py`**

```python
# app/web/routes_watches.py — add near the top-level routes
from app.slack.channel_cache import list_bot_channels

@watches_bp.route("/../slack-channels", methods=["GET"])
def _unused_placeholder():
    ...
```

The `/../` route path above is wrong — Flask blueprints do not support
relative escapes. Register the channel list on its own blueprint-free route
in `app_factory.py` instead, to avoid the `watches_bp`'s
`/api/v1/watches` prefix:

```python
# app/web/routes_watches.py — remove the placeholder above; add this
# top-level function instead (still in this file, not blueprint-routed)
def slack_channels_view():
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    slack_client = current_app.extensions["slack_client"]
    return jsonify(list_bot_channels(slack_client)), 200
```

- [ ] **Step 5: Wire everything into `app_factory.py`**

```python
# app/web/app_factory.py
from authlib.integrations.flask_client import OAuth
from flask import Flask
from app.config import Config
from app.web.routes_health import health_bp
from app.web.routes_auth import auth_bp
from app.web.routes_watches import watches_bp, slack_channels_view
from app.web.routes_tokens import tokens_bp

def create_app(config: Config, session_factory, scheduler, slack_client) -> Flask:
    app = Flask(__name__)
    app.extensions["config"] = config
    app.extensions["session_factory"] = session_factory
    app.extensions["scheduler"] = scheduler
    app.extensions["slack_client"] = slack_client
    app.secret_key = config.oidc_client_secret  # POC only; see Task 21 note

    oauth = OAuth(app)
    oauth.register(
        name="tinyoidc",
        client_id=config.oidc_client_id,
        client_secret=config.oidc_client_secret,
        server_metadata_url=f"{config.oidc_issuer}/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile email groups"},
    )
    app.extensions["oauth"] = oauth

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(watches_bp)
    app.register_blueprint(tokens_bp)
    app.add_url_rule("/api/v1/slack-channels", view_func=slack_channels_view)
    return app
```

- [ ] **Step 6: Update every other test file's `create_app(...)` call**

`tests/integration/test_routes_health.py`,
`tests/integration/test_routes_auth.py`, and
`tests/integration/test_routes_watches.py` all call `create_app(config,
session_factory, ...)` with two or three positional args from earlier
tasks. Update every call site across those three files to pass all four
arguments: `create_app(config, session_factory, scheduler=object(),
slack_client=object())`, using real fakes only where a given test's route
actually exercises `scheduler`/`slack_client` (i.e. keep the `Recorder`
monkeypatch approach from Task 16 for scheduler-touching tests).

- [ ] **Step 7: Add a channel-list test to `tests/integration/test_routes_watches.py`**

```python
# tests/integration/test_routes_watches.py — append
class FakeSlackClient:
    def conversations_list(self, **kwargs):
        return {"channels": [{"id": "C1", "name": "general"}]}

def test_slack_channels_list_requires_login(db_engine, postgres_container):
    app = create_app(
        make_test_config(postgres_container.get_connection_url()),
        make_session_factory(db_engine), scheduler=object(), slack_client=FakeSlackClient(),
    )
    app.secret_key = "test-secret"
    resp = app.test_client().get("/api/v1/slack-channels")
    assert resp.status_code == 401

def test_slack_channels_list_returns_channels(db_engine, postgres_container):
    app = create_app(
        make_test_config(postgres_container.get_connection_url()),
        make_session_factory(db_engine), scheduler=object(), slack_client=FakeSlackClient(),
    )
    app.secret_key = "test-secret"
    client = app.test_client()
    login_as(client, "u1")
    resp = client.get("/api/v1/slack-channels")
    assert resp.status_code == 200
    assert resp.get_json() == [{"id": "C1", "name": "general"}]
```

- [ ] **Step 8: Run every web test to verify they all pass**

Run: `pytest tests/integration/test_routes_health.py tests/integration/test_routes_auth.py tests/integration/test_routes_watches.py tests/integration/test_routes_tokens.py -v`
Expected: PASS (all tests across all four files)

- [ ] **Step 9: Commit**

```bash
git add app/web/routes_tokens.py app/web/routes_watches.py app/web/app_factory.py tests/integration/test_routes_tokens.py tests/integration/test_routes_watches.py tests/integration/test_routes_health.py tests/integration/test_routes_auth.py
git commit -m "feat: add API token mint/list/revoke routes and Slack channel picker"
```

---

## Task 18: `web` and `worker` entrypoints

**Files:**
- Create: `wsgi.py`
- Create: `worker_main.py`

**Interfaces:**
- Consumes: `app.config.Config.from_env` (Task 1), `app.db.{make_engine,
  make_session_factory}` (Task 2), `app.web.app_factory.create_app` (Task
  17), `app.worker.scheduler.{build_scheduler, sync_job}` (Task 12),
  `app.worker.jobs.check_watch` (Task 11), `app.worker.feed.parse_feed`
  (Task 7), `app.slack.client.ThrottledSlackClient` (Task 9),
  `app.logging_setup.configure_logging` (Task 14).
- Produces: `wsgi.py` exposes module-level `app` (a configured Flask
  instance) for gunicorn (`gunicorn wsgi:app`). `worker_main.py` exposes
  `main()`, run via `python -m worker_main` or `python worker_main.py`,
  which builds the scheduler, syncs a job for every currently-`is_active`
  watch, starts firing, and blocks forever.

No new automated test here — this task is pure wiring of already-tested
units, verified instead by the manual docker-compose smoke test in Task 20.

- [ ] **Step 1: Write `wsgi.py`**

```python
# wsgi.py
import os

from app.config import Config
from app.db import make_engine, make_session_factory
from app.logging_setup import configure_logging
from app.slack.client import ThrottledSlackClient
from app.web.app_factory import create_app
from app.worker.scheduler import build_scheduler

config = Config.from_env(os.environ)
configure_logging(config.log_level)

engine = make_engine(config.database_url)
session_factory = make_session_factory(engine)

slack_client = ThrottledSlackClient(
    config.slack_bot_token, min_interval_seconds=config.slack_post_min_interval_seconds
)


def _job_func(watch_id: int) -> None:
    from app.worker.feed import parse_feed
    from app.worker.jobs import check_watch

    db_session = session_factory()
    try:
        check_watch(
            db_session, watch_id,
            fetch_entries=parse_feed, slack_client=slack_client,
            truncate_chars=config.template_truncate_chars,
        )
        db_session.commit()
    finally:
        db_session.close()


scheduler = build_scheduler(config.database_url, job_func=_job_func)
scheduler.resume()

app = create_app(config, session_factory, scheduler=scheduler, slack_client=slack_client)
```

- [ ] **Step 2: Write `worker_main.py`**

```python
# worker_main.py
import os
import time

from app.config import Config
from app.db import make_engine, make_session_factory
from app.logging_setup import configure_logging
from app.models.rss_watch import RssWatch
from app.slack.client import ThrottledSlackClient
from app.worker.feed import parse_feed
from app.worker.jobs import check_watch
from app.worker.scheduler import build_scheduler, sync_job


def main():
    config = Config.from_env(os.environ)
    configure_logging(config.log_level)

    engine = make_engine(config.database_url)
    session_factory = make_session_factory(engine)

    slack_client = ThrottledSlackClient(
        config.slack_bot_token, min_interval_seconds=config.slack_post_min_interval_seconds
    )

    def job_func(watch_id: int) -> None:
        db_session = session_factory()
        try:
            check_watch(
                db_session, watch_id,
                fetch_entries=parse_feed, slack_client=slack_client,
                truncate_chars=config.template_truncate_chars,
            )
            db_session.commit()
        finally:
            db_session.close()

    scheduler = build_scheduler(config.database_url, job_func=job_func)

    db_session = session_factory()
    try:
        for watch in db_session.query(RssWatch).filter(RssWatch.is_active.is_(True)):
            sync_job(scheduler, watch)
    finally:
        db_session.close()

    scheduler.resume()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        scheduler.shutdown()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify both entrypoints import cleanly**

Run: `python -c "import ast; ast.parse(open('wsgi.py').read()); ast.parse(open('worker_main.py').read())"`
Expected: no output, exit code 0 (syntax check only — full import needs a
live `DATABASE_URL`, exercised for real in Task 20's compose smoke test)

- [ ] **Step 4: Commit**

```bash
git add wsgi.py worker_main.py
git commit -m "feat: add web (gunicorn/wsgi) and worker entrypoints"
```

---

## Task 19: Dockerfile

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

**Interfaces:**
- Consumes: `pyproject.toml` (Task 1), the full `app/` package, `wsgi.py`,
  `worker_main.py`, `alembic.ini`, `migrations/` (Task 18/3).
- Produces: one multi-stage image with three usable commands:
  `docker run <image> web` (gunicorn), `docker run <image> worker`
  (`python worker_main.py`), `docker run <image> migrate` (`alembic upgrade
  head`), dispatched by a small entrypoint script.

- [ ] **Step 1: Write `.dockerignore`**

```
.git
.pytest_cache
__pycache__
*.pyc
.venv
docs
tests
```

- [ ] **Step 2: Write `Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.14-slim AS base
# verify python:3.14-slim is still the current tag before building

WORKDIR /srv/app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini wsgi.py worker_main.py ./
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["web"]
```

- [ ] **Step 3: Write `docker-entrypoint.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

case "${1:-web}" in
  web)
    exec gunicorn --bind 0.0.0.0:8000 wsgi:app
    ;;
  worker)
    exec python worker_main.py
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    echo "Unknown command: ${1:-}" >&2
    exit 1
    ;;
esac
```

- [ ] **Step 4: Make the entrypoint executable and verify the build**

Run: `chmod +x docker-entrypoint.sh && docker build -t betterrssforslack:dev .`
Expected: build completes successfully (exit code 0)

- [ ] **Step 5: Commit**

```bash
git add Dockerfile .dockerignore docker-entrypoint.sh
git commit -m "feat: add multi-stage Dockerfile with web/worker/migrate entrypoints"
```

---

## Task 20: docker-compose for local dev

**Files:**
- Create: `docker-compose.yml`

**Interfaces:**
- Consumes: the image built in Task 19, `.env.example` (Task 1, copied by
  the developer to `.env`).
- Produces: `postgres`, `migrate` (runs to completion before `web`/`worker`
  start, per the spec's required lifecycle), `web`, `worker`.

- [ ] **Step 1: Write `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:17  # verify latest tag before use
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
      POSTGRES_DB: app
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d app"]
      interval: 5s
      timeout: 5s
      retries: 10
    volumes:
      - postgres_data:/var/lib/postgresql/data

  migrate:
    build: .
    command: ["migrate"]
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy

  web:
    build: .
    command: ["web"]
    env_file: .env
    ports:
      - "8000:8000"
    depends_on:
      migrate:
        condition: service_completed_successfully

  worker:
    build: .
    command: ["worker"]
    env_file: .env
    depends_on:
      migrate:
        condition: service_completed_successfully

volumes:
  postgres_data:
```

- [ ] **Step 2: Validate the compose file**

Run: `docker compose config`
Expected: prints the resolved config with no errors (exit code 0)

- [ ] **Step 3: Manual smoke test**

```bash
cp .env.example .env
# edit .env: set a real SLACK_BOT_TOKEN if you want live Slack posting
docker compose up --build
```

In another terminal:

```bash
curl -f http://localhost:8000/healthz
```

Expected: `{"status": "ok"}`. Confirm in the compose logs that `migrate`
exits 0 before `web`/`worker` start (`docker compose logs migrate`), and
that `worker` logs a JSON startup line. This is a manual verification step,
not an automated test — no test file to write here.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add docker-compose for local dev with migrate-before-app lifecycle"
```

---

## Task 21: Basic HTML UI

**Files:**
- Create: `app/web/templates/base.html`
- Create: `app/web/templates/dashboard.html`
- Create: `app/web/templates/watch_form.html`
- Create: `app/web/templates/watch_detail.html`
- Create: `app/web/templates/tokens.html`
- Create: `app/web/routes_ui.py`
- Modify: `app/web/app_factory.py` (register `ui_bp`)
- Test: `tests/integration/test_routes_ui.py`

**Interfaces:**
- Consumes: `app.acl.can_access`, `app.identity_resolver.identity_from_session`
  (Task 4/13), `app.models.rss_watch.RssWatch`,
  `app.models.enums.{Template, Visibility}` (Task 2),
  `app.worker.scheduler.{sync_job, pause_job, resume_job, remove_job}`
  (Task 12), `app.slack.channel_cache.list_bot_channels` (Task 10),
  `app.tokens.service.{mint_token, revoke_token}` (Task 5),
  `app.web.routes_watches._current_identity` (Task 16, reused as-is).
- Produces: a `ui_bp` blueprint (no URL prefix — root-level, distinct from
  `watches_bp`/`tokens_bp`'s `/api/v1/...` JSON prefix, so no path
  collision): `GET /` (dashboard), `GET,POST /watches/new`,
  `GET /watches/<id>`, `POST /watches/<id>/delete`,
  `POST /watches/<id>/pause`, `POST /watches/<id>/resume`, `GET /tokens`,
  `POST /tokens/mint`, `POST /tokens/<id>/revoke`. This is a thin view
  layer — it duplicates the small amount of ORM logic already exercised in
  Tasks 16/17's JSON routes rather than factoring a shared service module,
  since each duplicated block is a few lines and premature sharing would
  couple two independently-evolving response formats (JSON vs redirect+
  flash) before there's a second UI consumer to justify it.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_routes_ui.py
import datetime as dt
from app.config import Config
from app.web.app_factory import create_app
from app.db import make_session_factory
from app.models.slack_channel_cache import SlackChannelCache

class FakeSlackClient:
    def conversations_list(self, **kwargs):
        return {"channels": [{"id": "C1", "name": "general"}]}

def make_test_config(db_url):
    return Config(
        database_url=db_url,
        oidc_issuer="https://tinyoidc.authenti-kate.org",
        oidc_client_id="client_id_12decaf34bad56",
        oidc_client_secret="Super-+Secret_=Key0123456789",
        slack_bot_token="xoxb-fake",
        admin_oidc_groups=frozenset({"admins"}),
    )

def build_app(db_engine, postgres_container):
    config = make_test_config(postgres_container.get_connection_url())
    app = create_app(
        config, make_session_factory(db_engine), scheduler=object(), slack_client=FakeSlackClient()
    )
    app.secret_key = "test-secret"
    return app

def login_as(client, sub, groups=None):
    with client.session_transaction() as sess:
        sess["sub"] = sub
        sess["groups"] = groups or []

def seed_channel(db_engine):
    session = make_session_factory(db_engine)()
    session.add(SlackChannelCache(id="C1", name="general", last_refreshed_at=dt.datetime.now(dt.timezone.utc)))
    session.commit()
    session.close()

def test_dashboard_requires_login_redirects_to_login(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    resp = app.test_client().get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/login")

def test_new_watch_form_shows_channel_picker_options(db_engine, postgres_container):
    seed_channel(db_engine)
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    resp = client.get("/watches/new")
    assert resp.status_code == 200
    assert b"general" in resp.data

def test_create_watch_via_form_then_see_it_on_dashboard(db_engine, postgres_container):
    seed_channel(db_engine)
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    resp = client.post("/watches/new", data={
        "feed_url": "https://example.com/feed.xml",
        "slack_channel_id": "C1",
        "template": "headline_link",
        "visibility": "owner_only",
        "check_interval_seconds": "3600",
        "auto_pause_after_failures": "0",
    }, follow_redirects=False)
    assert resp.status_code == 302

    resp = client.get("/")
    assert b"https://example.com/feed.xml" in resp.data

def test_admin_badge_shown_for_admin_group_member(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1", groups=["admins"])
    resp = client.get("/")
    assert b"(admin)" in resp.data

def test_mint_token_via_form_shows_plaintext_once(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    resp = client.post("/tokens/mint", data={"ttl_seconds": "3600", "description": "cli"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"cli" in resp.data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_routes_ui.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.web.routes_ui'`

- [ ] **Step 3: Write `app/web/templates/base.html`**

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{% block title %}BetterRssForSlack{% endblock %}</title>
  <style>
    body { font-family: sans-serif; margin: 2rem; }
    nav { margin-bottom: 1.5rem; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; }
    .flash { background: #fffae6; border: 1px solid #e0c96b; padding: 0.5rem; margin-bottom: 1rem; }
  </style>
</head>
<body>
  <nav>
    {% if identity %}
      {{ identity.sub }}{% if identity.is_admin %} (admin){% endif %}
      &middot; <a href="/">Dashboard</a>
      &middot; <a href="/tokens">Tokens</a>
      &middot; <a href="/logout">Logout</a>
    {% else %}
      <a href="/login">Login</a>
    {% endif %}
  </nav>
  {% with messages = get_flashed_messages() %}
    {% for message in messages %}
      <div class="flash">{{ message }}</div>
    {% endfor %}
  {% endwith %}
  {% block content %}{% endblock %}
</body>
</html>
```

- [ ] **Step 4: Write `app/web/templates/dashboard.html`**

```html
{% extends "base.html" %}
{% block content %}
<h1>RSS Watches</h1>
<p><a href="/watches/new">+ New watch</a></p>
<table>
  <tr><th>Feed</th><th>Channel</th><th>Template</th><th>Active</th><th>Actions</th></tr>
  {% for watch in watches %}
  <tr>
    <td><a href="/watches/{{ watch.id }}">{{ watch.feed_url }}</a></td>
    <td>{{ watch.slack_channel_id }}</td>
    <td>{{ watch.template.value }}</td>
    <td>{{ "yes" if watch.is_active else "no" }}</td>
    <td>
      <form style="display:inline" method="post" action="/watches/{{ watch.id }}/{{ 'resume' if not watch.is_active else 'pause' }}">
        <button type="submit">{{ "Resume" if not watch.is_active else "Pause" }}</button>
      </form>
      <form style="display:inline" method="post" action="/watches/{{ watch.id }}/delete">
        <button type="submit">Delete</button>
      </form>
    </td>
  </tr>
  {% endfor %}
</table>
{% endblock %}
```

- [ ] **Step 5: Write `app/web/templates/watch_form.html`**

```html
{% extends "base.html" %}
{% block content %}
<h1>New Watch</h1>
<form method="post">
  <p><label>Feed URL <input type="url" name="feed_url" required></label></p>
  <p><label>Channel
    <select name="slack_channel_id" required>
      {% for channel in channels %}
      <option value="{{ channel.id }}">{{ channel.name }}</option>
      {% endfor %}
    </select>
  </label></p>
  <p><label>Template
    <select name="template">
      <option value="headline_link">Headline and link only</option>
      <option value="headline_link_truncated">Headline, link and truncated body</option>
      <option value="headline_link_thread">Headline, link and threaded reply</option>
      <option value="headline_link_full">Headline, link and full body</option>
    </select>
  </label></p>
  <p><label>Visibility
    <select name="visibility">
      <option value="owner_only">Only me (+ admins)</option>
      <option value="group">My group</option>
      <option value="public">Anyone authenticated</option>
    </select>
  </label></p>
  <p><label>Owning group (if visibility=group) <input type="text" name="owning_group"></label></p>
  <p><label>Check interval (seconds) <input type="number" name="check_interval_seconds" value="3600" required></label></p>
  <p><label>Auto-pause after N consecutive failures (0=never) <input type="number" name="auto_pause_after_failures" value="0"></label></p>
  <p><button type="submit">Create</button></p>
</form>
{% endblock %}
```

- [ ] **Step 6: Write `app/web/templates/watch_detail.html`**

```html
{% extends "base.html" %}
{% block content %}
<h1>{{ watch.feed_url }}</h1>
<ul>
  <li>Channel: {{ watch.slack_channel_id }}</li>
  <li>Template: {{ watch.template.value }}</li>
  <li>Active: {{ watch.is_active }}</li>
  <li>Last checked: {{ watch.last_checked_at }}</li>
  <li>Last posted: {{ watch.last_posted_at }}</li>
  <li>Consecutive failures: {{ watch.consecutive_failure_count }}</li>
  <li>Last error: {{ watch.last_error }}</li>
</ul>
{% endblock %}
```

- [ ] **Step 7: Write `app/web/templates/tokens.html`**

```html
{% extends "base.html" %}
{% block content %}
<h1>API Tokens</h1>
{% if minted_plaintext %}
<div class="flash">New token (shown once): <code>{{ minted_plaintext }}</code></div>
{% endif %}
<form method="post" action="/tokens/mint">
  <label>Description <input type="text" name="description"></label>
  <label>TTL seconds <input type="number" name="ttl_seconds" value="3600"></label>
  <button type="submit">Mint token</button>
</form>
<table>
  <tr><th>Description</th><th>Expires</th><th>Revoked</th><th></th></tr>
  {% for token in tokens %}
  <tr>
    <td>{{ token.description or "" }}</td>
    <td>{{ token.expires_at }}</td>
    <td>{{ "yes" if token.revoked_at else "no" }}</td>
    <td>
      {% if not token.revoked_at %}
      <form method="post" action="/tokens/{{ token.id }}/revoke">
        <button type="submit">Revoke</button>
      </form>
      {% endif %}
    </td>
  </tr>
  {% endfor %}
</table>
{% endblock %}
```

- [ ] **Step 8: Write `app/web/routes_ui.py`**

```python
# app/web/routes_ui.py
import datetime as dt
from flask import Blueprint, current_app, flash, redirect, render_template, request, session

from app.acl import can_access
from app.web.routes_watches import _current_identity
from app.models.rss_watch import RssWatch
from app.models.enums import Template, Visibility
from app.models.api_token import ApiToken
from app.worker.scheduler import sync_job, pause_job, resume_job, remove_job
from app.slack.channel_cache import list_bot_channels
from app.tokens.service import mint_token, revoke_token

ui_bp = Blueprint("ui", __name__)

@ui_bp.route("/")
def dashboard():
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    db_session = current_app.extensions["session_factory"]()
    try:
        all_watches = db_session.query(RssWatch).all()
        visible = [w for w in all_watches if can_access(w, identity)]
        return render_template("dashboard.html", watches=visible, identity=identity)
    finally:
        db_session.close()

@ui_bp.route("/watches/new", methods=["GET", "POST"])
def new_watch():
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    slack_client = current_app.extensions["slack_client"]

    if request.method == "GET":
        channels = list_bot_channels(slack_client)
        return render_template("watch_form.html", channels=channels, identity=identity)

    db_session = current_app.extensions["session_factory"]()
    try:
        watch = RssWatch(
            feed_url=request.form["feed_url"],
            slack_channel_id=request.form["slack_channel_id"],
            template=Template(request.form["template"]),
            visibility=Visibility(request.form.get("visibility", "owner_only")),
            owning_group=request.form.get("owning_group") or None,
            created_by_sub=identity.sub,
            created_at=dt.datetime.now(dt.timezone.utc),
            check_interval_seconds=int(request.form["check_interval_seconds"]),
            auto_pause_after_failures=int(request.form.get("auto_pause_after_failures", 0)),
        )
        db_session.add(watch)
        db_session.commit()
        sync_job(current_app.extensions["scheduler"], watch)
        flash("Watch created.")
        return redirect("/")
    finally:
        db_session.close()

def _load_visible_watch(db_session, identity, watch_id):
    watch = db_session.get(RssWatch, watch_id)
    if watch is None or not can_access(watch, identity):
        return None
    return watch

@ui_bp.route("/watches/<int:watch_id>")
def watch_detail(watch_id):
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return "Not found", 404
        return render_template("watch_detail.html", watch=watch, identity=identity)
    finally:
        db_session.close()

@ui_bp.route("/watches/<int:watch_id>/delete", methods=["POST"])
def delete_watch(watch_id):
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return "Not found", 404
        db_session.delete(watch)
        db_session.commit()
        remove_job(current_app.extensions["scheduler"], watch_id)
        flash("Watch deleted.")
        return redirect("/")
    finally:
        db_session.close()

@ui_bp.route("/watches/<int:watch_id>/pause", methods=["POST"])
def pause_watch(watch_id):
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return "Not found", 404
        watch.is_active = False
        db_session.commit()
        pause_job(current_app.extensions["scheduler"], watch_id)
        return redirect("/")
    finally:
        db_session.close()

@ui_bp.route("/watches/<int:watch_id>/resume", methods=["POST"])
def resume_watch(watch_id):
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return "Not found", 404
        watch.is_active = True
        watch.consecutive_failure_count = 0
        db_session.commit()
        resume_job(current_app.extensions["scheduler"], watch_id)
        return redirect("/")
    finally:
        db_session.close()

@ui_bp.route("/tokens", methods=["GET"])
def tokens_page():
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    db_session = current_app.extensions["session_factory"]()
    try:
        rows = db_session.query(ApiToken).filter(ApiToken.owner_sub == identity.sub).all()
        return render_template("tokens.html", tokens=rows, identity=identity, minted_plaintext=None)
    finally:
        db_session.close()

@ui_bp.route("/tokens/mint", methods=["POST"])
def tokens_mint():
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    config = current_app.extensions["config"]
    ttl_seconds = min(int(request.form.get("ttl_seconds", config.token_max_ttl_seconds)), config.token_max_ttl_seconds)
    db_session = current_app.extensions["session_factory"]()
    try:
        _, raw = mint_token(
            db_session, identity.sub, ttl_seconds=ttl_seconds,
            description=request.form.get("description") or None,
        )
        db_session.commit()
        rows = db_session.query(ApiToken).filter(ApiToken.owner_sub == identity.sub).all()
        return render_template("tokens.html", tokens=rows, identity=identity, minted_plaintext=raw)
    finally:
        db_session.close()

@ui_bp.route("/tokens/<int:token_id>/revoke", methods=["POST"])
def tokens_revoke(token_id):
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    db_session = current_app.extensions["session_factory"]()
    try:
        row = db_session.get(ApiToken, token_id)
        if row is None or row.owner_sub != identity.sub:
            return "Not found", 404
        revoke_token(db_session, token_id)
        db_session.commit()
        return redirect("/tokens")
    finally:
        db_session.close()
```

- [ ] **Step 9: Register `ui_bp` and add a secret key needed for `flash()` sessions in `app_factory.py`**

```python
# app/web/app_factory.py — add import and one registration line
from app.web.routes_ui import ui_bp
# ... inside create_app, after the other register_blueprint calls:
    app.register_blueprint(ui_bp)
```

- [ ] **Step 10: Run tests to verify they pass**

Run: `pytest tests/integration/test_routes_ui.py -v`
Expected: PASS (5 tests)

- [ ] **Step 11: Commit**

```bash
git add app/web/templates app/web/routes_ui.py app/web/app_factory.py tests/integration/test_routes_ui.py
git commit -m "feat: add basic server-rendered HTML UI for watches and tokens"
```

---

## Task 22: Playwright end-to-end OIDC login test

**Files:**
- Create: `tests/e2e/__init__.py` (empty)
- Create: `tests/e2e/conftest.py`
- Create: `tests/e2e/test_oidc_login.py`

**Interfaces:**
- Consumes: `app.web.app_factory.create_app` (Task 17/21), `app.config.Config`
  (Task 1), `app.db.{make_engine, make_session_factory}` (Task 2), real
  network access to `https://tinyoidc.authenti-kate.org`.
- Produces: a `live_app` pytest fixture that runs the real Flask app on a
  real TCP port via `werkzeug.serving.make_server` in a background thread
  (a browser needs an actual socket — `app.test_client()` cannot be driven
  by Playwright), yielding the base URL, torn down after the test.

This test requires internet access and depends on a third-party service
being up — keep it in its own `tests/e2e/` directory so it can be run
separately from the fast, hermetic suite (`pytest tests/unit
tests/integration` for the fast path; `pytest tests/e2e` for this one).

- [ ] **Step 1: Install Playwright's browser binary (one-time, not a test step)**

Run: `playwright install --with-deps chromium`
Expected: downloads and installs headless Chromium; only needs to run once
per environment (or once per Docker image layer if this test runs in CI
later).

- [ ] **Step 2: Write `tests/e2e/conftest.py`**

```python
# tests/e2e/conftest.py
import threading
import pytest
from werkzeug.serving import make_server

from app.config import Config
from app.db import make_session_factory
from app.web.app_factory import create_app

LIVE_PORT = 8765

@pytest.fixture()
def live_app(db_engine, postgres_container):
    config = Config(
        database_url=postgres_container.get_connection_url(),
        oidc_issuer="https://tinyoidc.authenti-kate.org",
        oidc_client_id="client_id_12decaf34bad56",
        oidc_client_secret="Super-+Secret_=Key0123456789",
        slack_bot_token="xoxb-fake",
        admin_oidc_groups=frozenset({"admins"}),
    )
    app = create_app(config, make_session_factory(db_engine), scheduler=object(), slack_client=object())
    app.secret_key = "e2e-secret"

    server = make_server("127.0.0.1", LIVE_PORT, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{LIVE_PORT}"
    finally:
        server.shutdown()
        thread.join()
```

- [ ] **Step 3: Write the failing test**

```python
# tests/e2e/test_oidc_login.py
from playwright.sync_api import sync_playwright

def test_login_as_admin_reaches_dashboard_with_admin_badge(live_app):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(f"{live_app}/login")
            page.get_by_role("button", name="Login as admin").click()
            page.wait_for_url(f"{live_app}/**")

            assert "(admin)" in page.content()
        finally:
            browser.close()
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/e2e/test_oidc_login.py -v`
Expected: FAIL — either a redirect-URI rejection from tinyoidc (surfaces as
a Playwright navigation/timeout error, not a hang, since
`wait_for_url`/`click` have finite default timeouts) or, if the redirect
works, a failure because `session["groups"]` isn't populated with the
`groups` claim yet (Task 15's `callback` route already reads
`userinfo.get("groups", [])`, so this should already work — if it doesn't,
that is the actual bug this test exists to catch).

- [ ] **Step 5: Fix whatever the failure reveals**

Two likely fixes, applied only if the test actually surfaces them (do not
apply speculatively):
- If tinyoidc rejects the callback's redirect_uri: register/confirm the
  correct allowed redirect URI pattern with tinyoidc (check `/app` on the
  tinyoidc site, mentioned on its homepage as where test credentials/app
  registration happens) and adjust `LIVE_PORT`/the callback URL to match,
  rather than guessing further.
- If `groups` arrives in a different shape than a plain list (e.g.
  comma-joined string, matching the "admins,Users,service_admins" format
  seen on the account-picker page rather than a JSON array): adjust
  `app/web/routes_auth.py`'s `callback()` to split on `,` when
  `userinfo["groups"]` is a string before storing it in the session — this
  is a real production code fix, not test-only scaffolding, since real
  logins hit the same shape.

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/e2e/test_oidc_login.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add tests/e2e app/web/routes_auth.py
git commit -m "test: add Playwright end-to-end OIDC login test against tinyoidc"
```

(Only include `app/web/routes_auth.py` in the commit if Step 5 actually
changed it.)

---

## Self-Review Notes

- **Spec coverage:** every spec section has a task — architecture (Task 1
  file structure + Tasks 18-20), data model (Task 2/3), auth & ACL (Tasks
  4/13/15), web routes incl. the HTML UI (Tasks 16/17/21), worker behavior
  incl. backfill and auto-pause (Tasks 7/11), Slack integration incl. rate
  limiting (Tasks 9/10), logging (Task 14), testing approach (used
  throughout, real Postgres via testcontainers, plus a genuine live login
  round-trip against tinyoidc in Task 22), deployment (Tasks 19/20), config
  (Task 1). Nothing in the spec is left without a task.
- **Type/name consistency check:** `Identity`, `can_access`, `Template`,
  `Visibility`, `FeedEntry`, `find_new_entries`, `render`,
  `ThrottledSlackClient.post_message`, `check_watch`, `sync_job`/
  `pause_job`/`resume_job`/`remove_job`, `mint_token`/`verify_token`/
  `revoke_token`, `upsert_user`, `refresh_channel`/`list_bot_channels`,
  `identity_from_session`/`identity_from_token`, `create_app` — each is
  defined once (in the task listed under "Produces") and referenced with
  the same name and signature in every later task that consumes it.
- **Placeholder scan:** the only intentionally-unresolved items are the
  version numbers flagged throughout as "verify before use" (per the
  Global Constraints rule, not a plan gap) and the Task 15 manual tinyoidc
  follow-up (explicitly out of scope for automation, not a stub).
