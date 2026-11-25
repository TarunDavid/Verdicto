"""End-to-end journey through the real views (server side): sign-up → chat check → AI research →
community vote → admin approval → three verifier reviews → final verdict → chat notice → archive,
share page, preview image and Kannada UI. External services (web sources, Groq) are stubbed."""
from unittest import mock

import httpx
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts import phone_otp
from accounts.models import User
from chat.models import ChatMessage, ChatThread
from claims.models import Claim
from research import ai, connectors
from verification.models import Assignment, CommunityVote, VerdictLog, VerifierStat, Vote

SOURCES = [
    {"url": "https://pib.gov.in/factcheck/holiday", "title": "PIB Fact Check: no statewide holiday",
     "snippet": "The viral circular announcing a school holiday is fake.", "published": None},
    {"url": "https://www.thehindu.com/news/schools-open", "title": "Schools to function as usual",
     "snippet": "District officials said schools will function as usual.", "published": None},
]
ASSESSMENT = ({"stances": {1: ("refutes", ""), 2: ("refutes", "")}, "summary": "Official sources deny it [1][2].",
               "facts": ["No holiday order was issued [1]"], "assumptions": [], "conflicts": []}, "stub-model")
PW = "journey-pass-2026"


def make_verifier(name, phone, approved=True):
    return User.objects.create_user(name, password=PW, phone=phone, first_name=name.title(), role="verifier",
                                    verifier_approved=approved, domains=["education"], languages=["en", "kn"])


@override_settings(RESEARCH_SYNC=True, RESEARCH_CONNECTORS=["stub"], GROQ_API_KEY="", RATELIMIT_ENABLE=False)
class FullJourneyTest(TestCase):
    def setUp(self):
        patches = [mock.patch.dict(connectors.CONNECTORS, {"stub": lambda q, l: [dict(s) for s in SOURCES]}),
                   mock.patch.object(ai, "assess", return_value=ASSESSMENT),
                   mock.patch.object(ai, "canonical_english", return_value="Schools closed tomorrow statewide.")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.admin = User.objects.create_superuser("admin", password=PW, role="admin", phone="+919000000901")
        self.v1 = make_verifier("ravi", "+919000000902")
        self.v2 = make_verifier("divya", "+919000000903")
        self.v3 = make_verifier("suma", "+919000000904", approved=False)   # approved during the journey

    def login(self, phone):
        client = self.client_class()
        resp = client.post(reverse("login"), {"username": phone, "password": PW})
        self.assertEqual(resp.status_code, 302, f"login failed for {phone}")
        return client

    def test_complete_journey(self):
        # 1. Visitors only see the login page.
        self.assertIn(reverse("login"), self.client.get("/")["Location"])

        # 2. Sign up as a regular user -> land on the chat, no news feed there.
        resp = self.client.post(reverse("signup"), {
            "role": "citizen", "name": "Asha Rao", "phone": "98450 22222", "interests": ["education"],
            "preferred_language": "en", "password": PW, "consent": "on"})
        self.assertRedirects(resp, reverse("chat"))
        asha = User.objects.get(phone="+919845022222")
        self.assertContains(self.client.get(reverse("chat")), "What do you want to check today?")

        # 3. Check a claim in the chat -> research runs -> provisional card, still PENDING.
        resp = self.client.post(reverse("chat_send"), {"text": "Govt declares holiday for all schools tomorrow",
                                                       "language": "en"})
        thread = ChatThread.objects.get(owner=asha)
        self.assertRedirects(resp, reverse("chat_thread", args=[thread.pk]))
        claim = thread.claim
        claim.refresh_from_db()
        self.assertEqual((claim.status, claim.verdict, claim.canonical_en),
                         ("PENDING", None, "Schools closed tomorrow statewide."))
        card = self.client.get(reverse("research_card", args=[claim.pk]))
        self.assertContains(card, "AI-PROVISIONAL")
        self.assertContains(card, "Likely false")
        self.assertContains(card, "pib.gov.in")
        assigned = set(claim.assignments.values_list("verifier__username", flat=True))
        self.assertEqual(assigned, {"ravi", "divya"})                 # suma isn't approved yet

        # 4. Community vote (low weight) doesn't decide anything on its own.
        self.client.post(reverse("community_vote", args=[claim.pk]), {"verdict": "FALSE", "next": "/"})
        claim.refresh_from_db()
        self.assertEqual(claim.status, "PENDING")
        self.assertEqual(CommunityVote.objects.get().user, asha)

        # 5. Pending verifier is blocked until an admin approves (approval is recorded).
        suma = self.login("9000000904")
        self.assertEqual(suma.get(reverse("verify_dashboard")).status_code, 403)
        admin = self.login("9000000901")
        admin.post("/admin/accounts/user/", {"action": "approve", "_selected_action": [self.v3.pk]})
        self.v3.refresh_from_db()
        self.assertEqual(self.v3.approved_by, self.admin)

        # 6. Three verifiers review with notes and evidence.
        for phone, name in [("9000000902", "ravi"), ("9000000903", "divya"), ("9000000904", "suma")]:
            v = self.login(phone)
            self.assertEqual(v.get(reverse("verify_dashboard")).status_code, 200)
            review = v.get(reverse("verify_review", args=[claim.pk]))
            self.assertContains(review, "AI RESEARCH (REFERENCE ONLY)")
            resp = v.post(reverse("verify_review", args=[claim.pk]), {
                "verdict": "FALSE", "note": f"{name}: no order on the official portal",
                "evidence_1": "https://pib.gov.in/factcheck/holiday"})
            self.assertRedirects(resp, reverse("verify_dashboard"))

        # 7. Final verdict, audit trail, track records, chat notice.
        claim.refresh_from_db()
        self.assertEqual((claim.status, claim.verdict), ("FINAL", "FALSE"))
        self.assertEqual(Vote.objects.filter(claim=claim).count(), 3)
        statuses = list(claim.logs.values_list("new_status", flat=True))
        self.assertEqual(statuses[0], "RESEARCHING")                 # submitted
        self.assertEqual(statuses[-1], "FINAL")
        self.assertTrue(all(log.reason for log in VerdictLog.objects.filter(claim=claim)))
        self.assertEqual(VerifierStat.objects.get(verifier=self.v1).correct, 1)
        asha.refresh_from_db()
        self.assertEqual((asha.community_correct, asha.community_total), (1, 1))
        notice = thread.messages.filter(sender=ChatMessage.Sender.SYSTEM).last()
        self.assertIn("Verified: FALSE", notice.content)
        self.assertTrue(Assignment.objects.filter(claim=claim, status="done").exists())

        # 8. Past verdicts, public share page + preview image, Kannada UI.
        self.assertContains(self.client.get(reverse("archive")), "Govt declares holiday")
        public = self.client_class()
        share = public.get(reverse("share", args=[claim.share_slug]))
        self.assertContains(share, 'property="og:image"')
        self.assertContains(share, "https://wa.me/?text=")
        self.assertEqual(public.get(reverse("og_image", args=[claim.share_slug]))["Content-Type"], "image/png")
        self.client.post(reverse("set_language"), {"language": "kn", "next": "/"})
        self.assertContains(self.client.get(reverse("claim_detail", args=[claim.pk])), "ಸುಳ್ಳು")   # FALSE badge


@override_settings(RATELIMIT_ENABLE=False, PHONE_OTP_ENABLED=True, SMS_BACKEND="twilio",
                   TWILIO_ACCOUNT_SID="ACtest", TWILIO_AUTH_TOKEN="tok", TWILIO_FROM_NUMBER="+15550001111")
class TwilioSMSTests(TestCase):
    DATA = {"role": "citizen", "name": "Ravi K", "phone": "9845033333", "interests": ["health"],
            "preferred_language": "en", "password": PW, "consent": "on"}

    def test_sms_sent_through_twilio(self):
        ok = httpx.Response(201, request=httpx.Request("POST", "https://api.twilio.com"))
        with mock.patch("httpx.post", return_value=ok) as post:
            resp = self.client.post(reverse("signup"), self.DATA)
        self.assertRedirects(resp, reverse("signup_verify"))
        kwargs = post.call_args.kwargs
        self.assertIn("/Accounts/ACtest/Messages.json", post.call_args.args[0])
        self.assertEqual((kwargs["data"]["To"], kwargs["data"]["From"]), ("+919845033333", "+15550001111"))
        self.assertRegex(kwargs["data"]["Body"], r"\b\d{6}\b")

    def test_gateway_failure_shows_error_and_keeps_no_pending_signup(self):
        with mock.patch("httpx.post", side_effect=httpx.ConnectError("down")):
            resp = self.client.post(reverse("signup"), self.DATA)
        self.assertContains(resp, "couldn&#x27;t send the SMS")
        self.assertNotIn(phone_otp.SESSION_KEY, self.client.session)
        self.assertEqual(User.objects.count(), 0)
