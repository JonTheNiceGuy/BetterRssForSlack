import os
import time

from app.config import Config
from app.db import make_engine, make_session_factory
from app.logging_setup import configure_logging
from app.models.rss_watch import RssWatch
from app.slack.client import ThrottledSlackClient
from app.worker.runner import configure as configure_runner, run_check_watch
from app.worker.scheduler import build_scheduler, sync_job


def main():
    config = Config.from_env(os.environ)
    configure_logging(config.log_level)

    engine = make_engine(config.database_url)
    session_factory = make_session_factory(engine)

    slack_client = ThrottledSlackClient(
        config.slack_bot_token, min_interval_seconds=config.slack_post_min_interval_seconds
    )

    configure_runner(session_factory, slack_client, config.template_truncate_chars)

    scheduler = build_scheduler(config.database_url, job_func=run_check_watch)

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
