"""python manage.py run_agents [--claim ID ...] — let the AI agent verifiers research and vote on
open claims they haven't handled yet (new claims get this automatically after AI research)."""
from django.core.management.base import BaseCommand, CommandError

from claims.models import Claim
from research import ai
from research.agents import ensure_agents, run_agents


class Command(BaseCommand):
    help = "Run the AI agent verifiers on open claims."

    def add_arguments(self, parser):
        parser.add_argument("--claim", type=int, action="append", help="Only this claim (repeatable).")
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **opts):
        if not ai.available():
            raise CommandError("GROQ_API_KEY is not set: AI agents need it.")
        agents = [a for a, _ in ensure_agents()]
        qs = Claim.objects.filter(status__in=["PENDING", "UNDER_REVIEW"], duplicate_of__isnull=True,
                                  is_hidden=False, locked_by_admin=False)
        if opts["claim"]:
            qs = qs.filter(pk__in=opts["claim"])
        todo = [c for c in qs.order_by("-created_at")
                if c.research_runs.model.all_runs.filter(claim=c, agent__in=agents).count() < len(agents)]
        for claim in todo[:opts["limit"]]:
            self.stdout.write(f"#{claim.pk} {claim.text[:70]}")
            for vote in run_agents(claim):
                self.stdout.write(f"   {vote.verifier.display_name}: {vote.verdict} (weight {vote.weight_at_vote:.2f})")
            claim.refresh_from_db()
            self.stdout.write(f"   -> {claim.get_status_display()}"
                              + (f" {claim.get_verdict_display()}" if claim.verdict else ""))
        self.stdout.write(self.style.SUCCESS(f"Done: {min(len(todo), opts['limit'])} claim(s)."))
