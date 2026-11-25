"""Browser tests with Playwright (real Chromium against a live test server).

Setup once:   pip install -r requirements-dev.txt && playwright install chromium
Run:          python manage.py test claims.test_browser        (skipped if Playwright isn't installed)
Watch it:     HEADED=1 python manage.py test claims.test_browser
External services (web sources, Groq) are stubbed, so the tests run offline.
"""
import os
from unittest import mock, skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings, tag

from accounts.models import User
from claims.models import Claim
from research import ai, connectors
from research.models import ResearchRun
from research.pipeline import run_research
from verification.models import CommunityVote, Vote

try:
    from playwright.sync_api import expect, sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:  # pragma: no cover
    HAS_PLAYWRIGHT = False

SOURCES = [{"url": "https://pib.gov.in/factcheck/1", "title": "PIB Fact Check: circular is fake",
            "snippet": "The viral circular is fake.", "published": None}]
ASSESSMENT = ({"stances": {1: ("refutes", "")}, "summary": "Official fact-check says it is fake [1].",
               "facts": [], "assumptions": [], "conflicts": []}, "stub")
PW = "browser-pass-2026"


@tag("browser")
@skipUnless(HAS_PLAYWRIGHT, "Playwright not installed (pip install -r requirements-dev.txt)")
@override_settings(RESEARCH_SYNC=True, RESEARCH_CONNECTORS=["stub"], GROQ_API_KEY="", RATELIMIT_ENABLE=False,
                   USE_CDN_ASSETS=False)
class BrowserTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"   # Playwright's sync API runs an event loop
        super().setUpClass()
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch(headless=not os.environ.get("HEADED"))
        except Exception as exc:  # browser not downloaded
            cls.pw.stop()
            super().tearDownClass()
            raise __import__("unittest").SkipTest(f"Chromium not available: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        super().tearDownClass()

    def setUp(self):
        for p in (mock.patch.dict(connectors.CONNECTORS, {"stub": lambda q, l: [dict(s) for s in SOURCES]}),
                  mock.patch.object(ai, "assess", return_value=ASSESSMENT),
                  mock.patch.object(ai, "canonical_english", return_value="")):
            p.start()
            self.addCleanup(p.stop)
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.page = self.context.new_page()
        self.page.set_default_timeout(10_000)
        self.addCleanup(self.context.close)

    def url(self, path):
        return self.live_server_url + path

    def login(self, phone):
        self.page.goto(self.url("/accounts/login/"))
        self.page.fill("input[name=username]", phone)
        self.page.fill("input[name=password]", PW)
        self.page.get_by_role("button", name="Log in").click()
        self.page.wait_for_url(self.url("/"))

    def test_regular_user_signs_up_checks_a_claim_and_votes(self):
        page = self.page
        page.goto(self.url("/"))
        expect(page).to_have_url(self.url("/accounts/login/?next=/"))
        # Local Tailwind build is applied (dark background, not the browser's default white).
        bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
        self.assertNotIn(bg, ("rgba(0, 0, 0, 0)", "rgb(255, 255, 255)"))

        page.get_by_role("link", name="Create an account").click()
        page.get_by_text("I want to check claims").click()
        page.fill("input[name=name]", "Asha Rao")
        page.fill("input[name=phone]", "98450 44444")
        page.check("input[name=interests][value=education]")
        page.fill("input[name=password]", PW)
        page.check("input[name=consent]")
        page.get_by_role("button", name="Create account").click()
        expect(page.get_by_text("What do you want to check today?")).to_be_visible()
        expect(page.get_by_text("WELCOME BACK, ASHA RAO")).to_be_visible()

        page.fill("#claimtext", "Schools in all districts closed tomorrow by government order")
        page.locator("button[aria-label='Check it']").click()
        expect(page.get_by_text("AI-PROVISIONAL")).to_be_visible()
        expect(page.get_by_text("Likely false").first).to_be_visible()
        expect(page.get_by_text("PENDING VERIFICATION").first).to_be_visible()

        page.get_by_text("Give your verdict").click()
        opinion = page.locator("form[action*='/opinion/']")
        opinion.locator("label", has_text="False").click()
        opinion.get_by_role("button", name="Submit").click()
        page.wait_for_load_state()
        self.assertEqual(CommunityVote.objects.get().verdict, "FALSE")

        page.get_by_role("button", name="ಕನ್ನಡ").click()
        expect(page.get_by_text("ಸುದ್ದಿ ಮತ್ತು ತೀರ್ಪುಗಳು").first).to_be_visible()

    def test_verifier_reviews_and_votes(self):
        User.objects.create_user("ravi", password=PW, phone="+919000000951", first_name="Ravi",
                                 role="verifier", verifier_approved=True, domains=["education"], languages=["en"])
        claim = Claim.objects.create(text="VTU exams cancelled this semester for everyone", domain="education",
                                     status="RESEARCHING")
        run_research(ResearchRun.objects.create(claim=claim).pk)   # also assigns Ravi

        self.login("9000000951")
        page = self.page
        page.get_by_role("link", name="Verify Queue").first.click()
        page.get_by_text("VTU exams cancelled").click()
        expect(page.get_by_text("AI RESEARCH (REFERENCE ONLY)")).to_be_visible()
        expect(page.get_by_text("PIB Fact Check: circular is fake").first).to_be_visible()

        form = page.locator("form", has=page.get_by_role("button", name="Submit verdict"))
        form.locator("label", has_text="False").first.click()
        form.locator("textarea[name=note]").fill("No such notice on the VTU site")
        form.locator("input[name=evidence_1]").fill("https://vtu.ac.in/notices")
        form.get_by_role("button", name="Submit verdict").click()
        page.wait_for_url(self.url("/verify/"))
        vote = Vote.objects.get()
        self.assertEqual((vote.verdict, vote.verifier.username), ("FALSE", "ravi"))
