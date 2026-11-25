from django.core.management.base import BaseCommand

from claims.models import Claim
from research import ai


class Command(BaseCommand):
    help = "Store the AI's English restatement for claims that don't have one (cross-language matching)."

    def handle(self, *args, **opts):
        todo = Claim.objects.filter(canonical_en="")
        for claim in todo:
            claim.canonical_en = ai.canonical_english(claim.text)
            claim.save(update_fields=["canonical_en"])
            self.stdout.write(f"{claim.language} | {claim.canonical_en[:90]}")
        self.stdout.write(self.style.SUCCESS(f"Done. Claims still without one: {Claim.objects.filter(canonical_en='').count()}"))
