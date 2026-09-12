from types import SimpleNamespace

from app.worker.scheduler import (
    build_scheduler, sync_job, pause_job, resume_job, remove_job,
)


def watch(id=1, check_interval_seconds=60):
    return SimpleNamespace(id=id, check_interval_seconds=check_interval_seconds)


def _noop_job(watch_id):
    pass


def test_sync_job_adds_job_with_correct_interval(postgres_container):
    scheduler = build_scheduler(postgres_container.get_connection_url(), job_func=_noop_job)
    try:
        sync_job(scheduler, watch(id=1, check_interval_seconds=120))
        job = scheduler.get_job("1")
        assert job is not None
        assert job.trigger.interval.total_seconds() == 120
    finally:
        scheduler.shutdown(wait=False)


def test_sync_job_reschedules_on_interval_change(postgres_container):
    scheduler = build_scheduler(postgres_container.get_connection_url(), job_func=_noop_job)
    try:
        sync_job(scheduler, watch(id=2, check_interval_seconds=60))
        sync_job(scheduler, watch(id=2, check_interval_seconds=300))
        job = scheduler.get_job("2")
        assert job.trigger.interval.total_seconds() == 300
    finally:
        scheduler.shutdown(wait=False)


def test_pause_and_resume_job(postgres_container):
    scheduler = build_scheduler(postgres_container.get_connection_url(), job_func=_noop_job)
    try:
        sync_job(scheduler, watch(id=3))
        pause_job(scheduler, 3)
        assert scheduler.get_job("3").next_run_time is None
        resume_job(scheduler, 3)
        assert scheduler.get_job("3").next_run_time is not None
    finally:
        scheduler.shutdown(wait=False)


def test_remove_job(postgres_container):
    scheduler = build_scheduler(postgres_container.get_connection_url(), job_func=_noop_job)
    try:
        sync_job(scheduler, watch(id=4))
        remove_job(scheduler, 4)
        assert scheduler.get_job("4") is None
    finally:
        scheduler.shutdown(wait=False)
