"""Research worker for RESEARCH_MODE=worker: `python manage.py run_worker` (run one or more)."""
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from research.pipeline import claim_next_queued, recover_stuck, run_research


class Command(BaseCommand):
    help = "Process queued AI research runs (use with RESEARCH_MODE=worker)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process the queue once, then exit.")
        parser.add_argument("--poll", type=float, default=2.0, help="Seconds between queue checks.")
        parser.add_argument("--pause", type=float, default=0.0,
                            help="Seconds to wait between runs (e.g. 30 to respect Groq token limits).")

    def handle(self, *args, **opts):
        recover_stuck()
        self.stdout.write("Research worker started.")
        while True:
            close_old_connections()
            run_id = claim_next_queued()
            if run_id is None:
                if opts["once"]:
                    return
                time.sleep(opts["poll"])
                continue
            self.stdout.write(f"Researching run {run_id}…")
            run_research(run_id)
            if opts["pause"]:
                time.sleep(opts["pause"])
