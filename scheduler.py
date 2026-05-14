"""
Scheduler module - Schedule periodic lead scraping runs.
"""

import logging
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
except ImportError:
    BackgroundScheduler = None
    CronTrigger = None
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ScrapingScheduler:
    """Manage scheduled lead scraping tasks."""
    
    def __init__(self, timezone: str = 'UTC'):
        """
        Initialize scheduler.
        
        Args:
            timezone: Timezone for scheduling
        """
        self.scheduler = BackgroundScheduler(timezone=timezone) if BackgroundScheduler else None
        self.is_running = False
    
    def start(self) -> None:
        """Start the scheduler."""
        if not self.scheduler:
            logger.warning("APScheduler is not installed; scheduled jobs are disabled.")
            return
        if not self.is_running:
            self.scheduler.start()
            self.is_running = True
            logger.info("Scheduler started")
    
    def stop(self) -> None:
        """Stop the scheduler."""
        if not self.scheduler:
            return
        if self.is_running:
            self.scheduler.shutdown()
            self.is_running = False
            logger.info("Scheduler stopped")
    
    def schedule_daily(
        self,
        func: Callable,
        hour: int = 8,
        minute: int = 0,
        job_id: str = 'daily_scrape'
    ) -> None:
        """
        Schedule a job to run daily at a specific time.
        
        Args:
            func: Function to execute
            hour: Hour to run (0-23)
            minute: Minute to run (0-59)
            job_id: Unique job identifier
        """
        if not self.scheduler:
            logger.warning("APScheduler is not installed; cannot schedule daily job.")
            return
        trigger = CronTrigger(hour=hour, minute=minute)
        self.scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            name=f'Daily scrape at {hour:02d}:{minute:02d}'
        )
        logger.info(f"Scheduled daily job: {job_id} at {hour:02d}:{minute:02d}")
    
    def schedule_hourly(
        self,
        func: Callable,
        minute: int = 0,
        job_id: str = 'hourly_scrape'
    ) -> None:
        """
        Schedule a job to run hourly.
        
        Args:
            func: Function to execute
            minute: Minute past the hour to run (0-59)
            job_id: Unique job identifier
        """
        if not self.scheduler:
            logger.warning("APScheduler is not installed; cannot schedule hourly job.")
            return
        trigger = CronTrigger(minute=minute)
        self.scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            name=f'Hourly scrape at minute {minute:02d}'
        )
        logger.info(f"Scheduled hourly job: {job_id} at minute {minute:02d}")
    
    def schedule_interval(
        self,
        func: Callable,
        hours: int = 24,
        minutes: int = 0,
        job_id: str = 'interval_scrape'
    ) -> None:
        """
        Schedule a job to run at regular intervals.
        
        Args:
            func: Function to execute
            hours: Hours between runs
            minutes: Additional minutes between runs
            job_id: Unique job identifier
        """
        total_seconds = (hours * 3600) + (minutes * 60)
        if not self.scheduler:
            logger.warning("APScheduler is not installed; cannot schedule interval job.")
            return
        self.scheduler.add_job(
            func,
            'interval',
            seconds=total_seconds,
            id=job_id,
            replace_existing=True,
            name=f'Interval scrape every {hours}h {minutes}m'
        )
        logger.info(f"Scheduled interval job: {job_id} every {hours}h {minutes}m")
    
    def remove_job(self, job_id: str) -> None:
        """Remove a scheduled job."""
        if not self.scheduler:
            return
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
            logger.info(f"Removed job: {job_id}")
    
    def list_jobs(self) -> list:
        """List all scheduled jobs."""
        if not self.scheduler:
            return []
        jobs = self.scheduler.get_jobs()
        for job in jobs:
            logger.info(f"Job: {job.id}, Next run: {job.next_run_time}")
        return jobs
    
    def pause_job(self, job_id: str) -> None:
        """Pause a scheduled job."""
        if not self.scheduler:
            return
        job = self.scheduler.get_job(job_id)
        if job:
            job.pause()
            logger.info(f"Paused job: {job_id}")
    
    def resume_job(self, job_id: str) -> None:
        """Resume a paused job."""
        if not self.scheduler:
            return
        job = self.scheduler.get_job(job_id)
        if job:
            job.resume()
            logger.info(f"Resumed job: {job_id}")
