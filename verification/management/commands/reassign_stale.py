from django.core.management.base import BaseCommand

from verification.assignment import reassign_stale


class Command(BaseCommand):
    help = "Reassign claims whose verifiers did not respond within ASSIGNMENT_TIMEOUT_HOURS (run from cron)."

    def handle(self, *args, **opts):
        self.stdout.write(f"Reassigned {reassign_stale()} claim(s).")
