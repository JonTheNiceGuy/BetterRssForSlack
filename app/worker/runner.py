"""Module-level job entrypoint for APScheduler's persistent jobstore.

APScheduler's SQLAlchemyJobStore stores each job's callable as a
"module:qualname" string and re-imports it wherever the job later fires.
That means the callable must be a genuine module-level function reachable
under the same import path in every process that touches the jobstore
(web, to add/reschedule/pause/resume/remove job records; worker, to
actually execute them) -- a closure defined inside main() or wsgi.py's
top level would not resolve, and re-importing a whole entrypoint module
just to resolve one attribute would re-run its startup side effects.

Call configure() once at process startup before any job can fire, then
pass run_check_watch itself (not a lambda wrapping it) as build_scheduler's
job_func.
"""
_session_factory = None
_slack_client = None
_truncate_chars = None


def configure(session_factory, slack_client, truncate_chars: int) -> None:
    global _session_factory, _slack_client, _truncate_chars
    _session_factory = session_factory
    _slack_client = slack_client
    _truncate_chars = truncate_chars


def run_check_watch(watch_id: int) -> None:
    from app.worker.feed import parse_feed
    from app.worker.jobs import check_watch

    db_session = _session_factory()
    try:
        check_watch(
            db_session, watch_id,
            fetch_entries=parse_feed, slack_client=_slack_client,
            truncate_chars=_truncate_chars,
        )
        db_session.commit()
    finally:
        db_session.close()
