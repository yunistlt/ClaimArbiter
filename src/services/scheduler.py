import logging
import importlib
from datetime import datetime, timedelta
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class DeadlineSchedulerService:
    """
    Scheduler service for proactive legal reminders.

    This is an integration skeleton:
    - polls deadline/task storage on schedule
    - sends reminder notifications for upcoming due dates
    - marks notifications to avoid duplicates
    """

    def __init__(
        self,
        poll_interval_minutes: int = 60,
        reminder_horizon_hours: int = 24,
        on_deadline_hit: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.poll_interval_minutes = poll_interval_minutes
        self.reminder_horizon = timedelta(hours=reminder_horizon_hours)
        self.on_deadline_hit = on_deadline_hit
        self.scheduler = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return

        scheduler_module = importlib.import_module("apscheduler.schedulers.asyncio")
        async_scheduler_cls = getattr(scheduler_module, "AsyncIOScheduler")
        self.scheduler = async_scheduler_cls()

        self.scheduler.add_job(
            self._run_tick,
            trigger="interval",
            minutes=self.poll_interval_minutes,
            id="deadline_poll",
            replace_existing=True,
        )
        self.scheduler.start()
        self._started = True
        logger.info("Deadline scheduler started.")

    def stop(self) -> None:
        if not self._started:
            return
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
        self._started = False
        logger.info("Deadline scheduler stopped.")

    async def _run_tick(self) -> None:
        """
        Main polling hook.

        TODO:
        1) Query DB table with fields: deadline_date, responsible_user_id, notification_sent.
        2) Select records where now <= deadline_date <= now + reminder_horizon and notification_sent = False.
        3) Send proactive PM reminder to responsible user.
        4) Mark notification_sent = True.
        """
        now = datetime.utcnow()
        horizon = now + self.reminder_horizon
        logger.debug("Scheduler tick: now=%s horizon=%s", now.isoformat(), horizon.isoformat())

        # Placeholder to keep service non-breaking before DB integration.
        if self.on_deadline_hit:
            try:
                self.on_deadline_hit(
                    {
                        "type": "scheduler_tick",
                        "now": now.isoformat(),
                        "horizon": horizon.isoformat(),
                    }
                )
            except Exception as exc:
                logger.warning("Scheduler callback failed: %s", exc)
