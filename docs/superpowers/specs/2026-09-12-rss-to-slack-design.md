# BetterRssForSlack — Design

Date: 2026-09-12

## Purpose

OIDC-authenticated Flask web app to manage RSS/Atom feed watches that post
updates to Slack channels, plus a worker that checks feeds on a schedule and
posts new entries. Tracks who created each watch, which channel it posts to,
and when the last message was posted. Short-lived API tokens (minted from the
web app, tied to the OIDC account) allow create/delete/report on watches via
API.

## Architecture

Three containers, one shared Postgres database:

- **`web`** — Flask app. OIDC login (Authlib) against `tinyoidc.authenti-kate.org`.
  CRUD UI for RSS watches, token minting, JSON API mirroring the UI actions.
- **`worker`** — long-running process. APScheduler with a persistent
  `SQLAlchemyJobStore`, one job per watch at its own interval (feeds range
  from hourly to monthly). Each job fetch-diffs-posts-updates.
- **`migrate`** — one-shot `alembic upgrade head`. Runs to completion before
  `web`/`worker` start (docker-compose `service_completed_successfully`; same
  shape becomes a Helm pre-install/pre-upgrade hook later).

`web` and `worker` never call each other directly — Postgres is the only
shared state (including the APScheduler jobstore's own `apscheduler_jobs`
table), so either can restart independently. Single worker replica assumed:
APScheduler's persistent jobstore is not safe for concurrent schedulers
without a distributed lock, so scale-out is out of scope for this design.

## Data model

### `user_cache`
- `id` (OIDC `sub`, PK)
- `display_name`, `email`
- `last_refreshed_at` — refreshed from ID token claims at each login

### `slack_channel_cache`
- `id` (Slack channel ID, PK)
- `name`
- `last_refreshed_at` — refreshed opportunistically (channel picker load, or
  lazily when stale) via `conversations.info`

### `rss_watch`
- `id`
- `feed_url`
- `slack_channel_id` (FK → `slack_channel_cache.id`)
- `template` (enum: `headline_link`, `headline_link_truncated`,
  `headline_link_thread`, `headline_link_full`)
- `visibility` (enum: `owner_only`, `group`, `public`)
- `owning_group` (nullable, used when `visibility=group`)
- `created_by_sub` (FK → `user_cache.id`)
- `created_at`
- `check_interval_seconds`
- `is_active` (pause flag)
- `consecutive_failure_count` (int, resets to 0 on a successful check)
- `auto_pause_after_failures` (int, per-watch, default 0 = never auto-pause)
- `last_checked_at` (nullable)
- `last_posted_at` (nullable — "last message received" for reporting)
- `last_entry_id` (nullable — dedup cursor: newest processed entry's guid/link)
- `last_error` (nullable)

### `api_token`
- `id`
- `token_hash` (sha256; plaintext shown once at mint, never stored)
- `owner_sub` (FK → `user_cache.id`)
- `description` (nullable, user-supplied label)
- `created_at`
- `expires_at`
- `revoked_at` (nullable — manual early revoke)

Migrations via Alembic. **Never edit an existing migration once merged —
always a new forward migration**, even to fix a mistake in the previous one.

## Auth & ACL

**Login:** Authlib OIDC client against `tinyoidc.authenti-kate.org`
(`client_id_12decaf34bad56` / the documented non-expiring POC secret).
`/login` → provider → `/auth/callback` exchanges the code, upserts
`user_cache` from ID token claims (sub/name/email/groups), sets a signed
session cookie holding the sub.

`ADMIN_OIDC_GROUPS` env var (comma-separated) — membership in any listed
group grants admin.

**Identity resolution:** every request resolves to `(sub, groups, is_admin)`
— from the session cookie (browser) or from `api_token` lookup + hash match
(API, `Authorization: Bearer <token>`). Both paths feed the same resolver, so
ACL logic lives in exactly one place.

**`can_access(watch, identity)`** — governs view, edit, delete, pause/resume,
and report uniformly (no separate broader "view" rule):
- `identity.is_admin`, or
- `identity.sub == watch.created_by_sub`, or
- `watch.visibility == 'group' and watch.owning_group in identity.groups`, or
- `watch.visibility == 'public'`

## Web app routes

Two parallel surfaces, both passing through the same `can_access` check and
identity resolver, both backed by the same models/services — a basic
server-rendered Jinja2 HTML UI for humans, and a JSON API for scripts/tokens:

- `/login`, `/auth/callback`, `/logout`
- `/` — dashboard, watches visible to current identity
- `/watches/new` — feed URL, channel picker (via bot `conversations.list`),
  template choice, visibility + group, check interval,
  `auto_pause_after_failures`, backfill toggle (see below)
- `/watches/<id>` — detail/report (last posted, last checked, last error,
  failure count)
- `/watches/<id>/edit`, `/watches/<id>/delete` — ACL-gated
- `/watches/<id>/pause`, `/watches/<id>/resume` — ACL-gated; resume resets
  `consecutive_failure_count` to 0
- `/tokens` — list own tokens (masked, shows description/expiry)
- `/tokens/mint` — `POST {ttl_seconds?, description?}`, `ttl_seconds` capped
  by `TOKEN_MAX_TTL_SECONDS`; returns plaintext token once
- `/tokens/<id>/revoke`

**HTML UI:** basic, unstyled-beyond-minimal Jinja2 templates — a base layout
with a nav bar (current user's email + an "(admin)" badge when
`is_admin`, Logout link), a dashboard listing visible watches with
pause/resume/delete actions, a create/edit form (channel picker populated
from the bot's live channel list, template/visibility/interval/
auto-pause/backfill fields), a watch detail page showing the report fields,
and a tokens page (mint form + list + revoke, plaintext token shown once
directly in the rendered page after minting). This is a thin view layer over
the same watch/token operations the JSON API uses — no client-side
framework, plain HTML forms posting back to the app.

## Worker behavior

**Scheduling:** on boot, worker loads all `is_active` watches and adds one
interval job per watch (`id=watch.id`, `seconds=check_interval_seconds`) to
the persistent jobstore. `web` adds/reschedules/pauses/removes the matching
job directly (shared scheduler-client helper) whenever a watch is
created/edited/paused/resumed/deleted — the persistent jobstore is the
hand-off, not a separate queue.

**Job body** (`check_watch(watch_id)`):
1. Load the watch row fresh from the DB.
2. `feedparser.parse(feed_url)`.
3. On fetch/parse failure: set `last_error`, `last_checked_at=now`, increment
   `consecutive_failure_count`, log a warning. If
   `auto_pause_after_failures > 0` and the count reaches it: post one
   `chat.postMessage` to the watch's channel — "⚠️ Auto-paused after N
   consecutive failures. Last error: …" — then set `is_active=False` and
   `scheduler.pause_job(id)`. Return.
4. On success: clear `last_error`, reset `consecutive_failure_count=0`, find
   entries newer than `last_entry_id` (feed entries assumed newest-first;
   locate the cursor in the list, everything above it is new).
   - If `last_entry_id` is unset or not found in the current feed (cold
     start, or feed history rewritten): apply the **backfill rule** — post
     only the current newest entry, with title prefixed `[Backfill] `.
   - Otherwise post every new entry, oldest-of-the-new-batch first, in order.
5. For each posted entry, render per `template` and call `chat.postMessage`;
   for `headline_link_thread`, a second `chat.postMessage` with `thread_ts`
   from the first response.
6. After posting: advance `last_entry_id` to the newest entry's id;
   `last_posted_at=now` if ≥1 entry was posted; `last_checked_at=now`.

**Backfill toggle:** creation form defaults to "on" (post the current newest
entry, `[Backfill] `-prefixed) but the user can turn it off (baseline
silently, post nothing until the next genuinely new entry).

**Templates** (pure render functions — unit-testable with no DB/network):
- `headline_link` — title + link only
- `headline_link_truncated` — title + link, then body HTML→text (`html2text`)
  truncated to `TEMPLATE_TRUNCATE_CHARS` (default 500)
- `headline_link_thread` — root message = title + link; threaded reply =
  truncated body (same truncation as above). No per-entry edit-tracking — if
  a feed entry is edited later, no state means it will not be detected as
  changed; this can grow into feed-provided rewrite rules later if needed.
- `headline_link_full` — title + link, then full body HTML→text, no
  truncation

## Slack integration

Official `slack_sdk` `WebClient`, bot token (`SLACK_BOT_TOKEN`), scopes for
`conversations.list`/`conversations.info` (channel picker + name cache) and
`chat.postMessage` (public and private channels the bot has been invited to).

**Rate limiting:**
- Proactive: one `chat.postMessage` in flight at a time process-wide, with a
  minimum gap between calls (`SLACK_POST_MIN_INTERVAL_SECONDS`, default
  1.0s) — many due jobs can fire in the same scheduler tick.
- Reactive fallback: `slack_sdk`'s built-in `RateLimitErrorRetryHandler`
  honors `Retry-After` on any 429 that gets through anyway.

## Logging

Structured JSON lines to stdout (Loki-ingestion-ready), via `python-json-logger`
or `structlog` on top of stdlib `logging`. Level from `LOG_LEVEL` env var
(default `INFO`).

## Testing

Red/Green TDD throughout. `pytest` + `pytest-cov`. DB-touching tests run
against a real Postgres via `testcontainers-python` (no SQLite dialect
drift — this project targets Postgres only, in test and prod alike).
Template-render functions and the diff/cursor logic are pure and unit-tested
without a DB. Slack calls mocked in web/worker tests.

One genuine end-to-end test drives a real login round-trip against
`tinyoidc.authenti-kate.org` with Playwright (headless Chromium): the app
runs as a real HTTP server (not the Flask test client, since a browser needs
a real socket), Playwright opens `/login`, lands on tinyoidc's real
account-picker page (confirmed live: no password form — one "Login as
&lt;user&gt;" button per pre-seeded account: admin/it/accounts/auditor/
sysadmin/reception/contractor), clicks "Login as admin" (pre-seeded groups
`admins,Users,service_admins` — `admin` lands in our own `ADMIN_OIDC_GROUPS`
by design), and the test asserts the app's dashboard renders the logged-in
user's email and the "(admin)" badge. This is a real network dependency on
a standing third-party POC service — acceptable here since tinyoidc exists
specifically to be driven this way — and proves the discovery document,
the `groups` claim, and the redirect_uri round-trip all actually work
end-to-end, not just against mocks.

## Deployment

**Now:** multi-stage `Dockerfile`, one image with three entrypoints
(`web` / `worker` / `migrate` selected via `CMD` args). `docker-compose.yml`:
`postgres`, `migrate` (`depends_on: postgres: condition: service_healthy`,
runs `alembic upgrade head`, exits), `web` and `worker`
(`depends_on: migrate: condition: service_completed_successfully`).

**Later:** Helm chart, using the same three-entrypoint image, migration as a
pre-install/pre-upgrade hook mirroring the compose `migrate` service.

## Config (env vars)

`DATABASE_URL`, `OIDC_ISSUER` (discovery base URL), `OIDC_CLIENT_ID`,
`OIDC_CLIENT_SECRET`, `ADMIN_OIDC_GROUPS`, `TOKEN_MAX_TTL_SECONDS`,
`SLACK_BOT_TOKEN`, `SLACK_POST_MIN_INTERVAL_SECONDS`,
`TEMPLATE_TRUNCATE_CHARS`, `LOG_LEVEL`.

## Versions

Python 3.14 (latest stable, currently 3.14.7). Postgres, SQLAlchemy, Alembic,
Flask, Authlib, APScheduler, feedparser, slack_sdk, html2text — latest stable
of each at implementation time, checked against their registries rather than
assumed from memory, per the "prefer latest versions" rule.

## Out of scope (this iteration)

- Multi-worker-replica scaling (needs a distributed APScheduler lock)
- Per-entry edit-tracking / feed-provided rewrite rules for headline/body
  extraction (flagged by user as likely future work)
- Helm chart (compose only for now)
- Token scopes finer-grained than "acts as owner"
