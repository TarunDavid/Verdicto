"""Check which API keys in .env work: `python manage.py checkkeys`. Makes one small test call per
configured service (no SMS is sent; Twilio is checked by reading the account)."""
from django.conf import settings
from django.core.management.base import BaseCommand

from research import ai, connectors


class Command(BaseCommand):
    help = "Test each configured API key with one small request."

    def line(self, name, ok, detail):
        style = self.style.SUCCESS if ok is True else (self.style.WARNING if ok is None else self.style.ERROR)
        self.stdout.write(style(f"{'OK ' if ok else ('-- ' if ok is None else 'ERR')} {name:<18} {detail}"))

    def handle(self, *args, **opts):
        if settings.GROQ_API_KEY:
            try:
                n = len(ai.client().models.list().data)
                self.line("Groq", True, f"{n} models available")
            except Exception as exc:
                self.line("Groq", False, f"{type(exc).__name__}: {str(exc)[:80]}")
        else:
            self.line("Groq", None, "GROQ_API_KEY not set (AI features use fallbacks)")

        checks = [("Google Fact Check", settings.GOOGLE_FACTCHECK_API_KEY, connectors.factcheck, "covid vaccine"),
                  ("Semantic Scholar", settings.SEMANTIC_SCHOLAR_API_KEY, connectors.semantic_scholar, "5G health"),
                  ("Reddit", settings.REDDIT_CLIENT_ID and settings.REDDIT_CLIENT_SECRET, connectors.reddit, "fact check")]
        for name, key, fn, query in checks:
            if not key:
                self.line(name, None, "key not set (source skipped)")
                continue
            try:
                self.line(name, True, f"{len(fn(query, 'en'))} results for a test query")
            except Exception as exc:
                self.line(name, False, f"{type(exc).__name__}: {str(exc)[:80]}")

        if settings.SMS_BACKEND == "twilio":
            import httpx
            try:
                r = httpx.get(f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}.json",
                              auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN), timeout=10)
                r.raise_for_status()
                self.line("Twilio", True, f"account {r.json().get('status', '?')}; sender {settings.TWILIO_FROM_NUMBER or 'NOT SET'}")
            except Exception as exc:
                self.line("Twilio", False, f"{type(exc).__name__}: {str(exc)[:80]}")
        else:
            self.line("Twilio", None, "SMS_BACKEND=console (codes print in the server log)")
