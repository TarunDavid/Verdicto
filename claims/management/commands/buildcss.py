"""Build static/css/app.css with the Tailwind standalone CLI (no Node needed)."""
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Rebuild static/css/app.css from the templates (run after changing Tailwind classes)."

    def handle(self, *args, **opts):
        base = Path(settings.BASE_DIR)
        cli = base / "tools" / "tailwindcss"
        if not cli.exists():
            raise CommandError("tools/tailwindcss not found. Download the standalone CLI (v3.4.x) for your "
                               "platform from github.com/tailwindlabs/tailwindcss/releases into tools/.")
        subprocess.run([str(cli), "-c", "tailwind.config.js", "-i", "static/css/input.css",
                        "-o", "static/css/app.css", "--minify"], cwd=base, check=True)
        self.stdout.write(self.style.SUCCESS("Built static/css/app.css"))
