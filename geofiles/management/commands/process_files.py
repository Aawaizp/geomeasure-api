"""Background worker: python manage.py process_files

Polls the database for QUEUED files and processes them one at a time.
Run several copies to process files in parallel; row locking makes sure
two workers never take the same file.
"""
import logging
import time

from django.core.management.base import BaseCommand
from django.db import OperationalError, ProgrammingError

from geofiles.services import claim_next_job, process_geofile, requeue_stale_jobs

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process queued geospatial files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once", action="store_true", help="Process everything queued, then exit."
        )
        parser.add_argument(
            "--interval", type=float, default=2.0, help="Seconds to wait when the queue is empty."
        )

    def handle(self, *args, **options):
        self.stdout.write("Worker started.")
        requeued = self._safe(requeue_stale_jobs) or 0
        if requeued:
            self.stdout.write(f"Re-queued {requeued} stale job(s).")

        while True:
            geofile = self._safe(claim_next_job)
            if geofile is not None:
                process_geofile(geofile)
                self.stdout.write(f"{geofile.original_filename}: {geofile.status}")
                continue
            if options["once"]:
                return
            time.sleep(options["interval"])

    def _safe(self, func):
        """Run a DB call, waiting instead of crashing if the DB isn't ready yet."""
        try:
            return func()
        except (OperationalError, ProgrammingError) as exc:
            logger.warning("Database not ready (%s); retrying.", exc.__class__.__name__)
            time.sleep(2)
            return None