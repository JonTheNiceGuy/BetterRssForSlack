import os

from app.config import Config
from app.db import make_engine, make_session_factory
from app.logging_setup import configure_logging
from app.slack.client import ThrottledSlackClient
from app.web.app_factory import create_app
from app.worker.runner import configure as configure_runner, run_check_watch
from app.worker.scheduler import build_scheduler

config = Config.from_env(os.environ)
configure_logging(config.log_level)

engine = make_engine(config.database_url)
session_factory = make_session_factory(engine)

slack_client = ThrottledSlackClient(
    config.slack_bot_token, min_interval_seconds=config.slack_post_min_interval_seconds
)

configure_runner(session_factory, slack_client, config.template_truncate_chars)

scheduler = build_scheduler(config.database_url, job_func=run_check_watch)
# Deliberately left globally paused (build_scheduler starts it paused) --
# the web process only uses this scheduler handle to add/pause/resume/
# remove job *records* in the shared jobstore table via the watch routes.
# Only the separate worker process (worker_main.py) calls scheduler.resume()
# to actually start executing due jobs; if web resumed too, both processes
# would fire the same jobs against the shared persistent jobstore.

app = create_app(config, session_factory, scheduler=scheduler, slack_client=slack_client)
