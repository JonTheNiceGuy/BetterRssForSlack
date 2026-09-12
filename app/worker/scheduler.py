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
