from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from chat.models import ChatMessage, ChatThread
from claims.models import Claim
from verification import engine
from verification.models import CommunityVote, VerifierStat
from verification.services import cast_community_vote, cast_vote

from .models import User
from .phone import normalize_phone

SIGNUP = {"name": "Asha Rao", "phone": "+91 98450 12345", "interests": ["health", "education"],
          "preferred_language": "en", "password": "strong-pass-2026", "consent": "on"}


class PhoneTests(SimpleTestCase):
    def test_normalize(self):
        for raw in ["+91 98450 12345", "098450-12345", "9845012345", "919845012345"]:
            self.assertEqual(normalize_phone(raw), "+919845012345")
        for bad in ["12345", "5845012345", "abc", ""]:
            self.assertEqual(normalize_phone(bad), "")


class CommunityWeightTests(SimpleTestCase):
    def test_each_vote_is_small(self):
        self.assertAlmostEqual(engine.community_vote_weight(2, 2.0), 0.05)

    def test_combined_weight_capped_at_20_percent(self):
        votes = [("FALSE", 0.83), ("FALSE", 0.67), ("TRUE", 0.5)]   # verifier total 2.0
        result = engine.compute_verdict(votes, ["TRUE"] * 100)        # brigade of 100 users
        community_total = result.community_vote_weight * 100
        self.assertAlmostEqual(community_total / (2.0 + community_total), 0.20)
        self.assertEqual(result.leading, "FALSE")                    # experts still lead

    def test_community_alone_never_finalises(self):
        result = engine.compute_verdict([], ["FALSE"] * 500)
        self.assertEqual(result.status, "UNDER_REVIEW")
        self.assertEqual(result.leading, "FALSE")

    def test_worked_example_unchanged_without_community(self):
        votes = [("FALSE", engine.beta_weight(8, 8)), ("FALSE", engine.beta_weight(2, 2)),
                 ("TRUE", engine.beta_weight(0, 0))]
        result = engine.compute_verdict(votes)
        self.assertEqual((result.status, result.verdict), ("FINAL", "FALSE"))
        self.assertAlmostEqual(result.confidence, 0.75)


@override_settings(RATELIMIT_ENABLE=False)
class AuthFlowTests(TestCase):
    def test_logged_out_visitor_only_sees_login(self):
        for name in ("chat", "feed", "archive", "verify_dashboard"):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 302, name)
            self.assertIn(reverse("login"), resp["Location"])
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)
        self.assertContains(self.client.get(reverse("signup")), "I want to verify claims")

    def test_regular_signup_lands_on_chat_without_news(self):
        resp = self.client.post(reverse("signup"), {**SIGNUP, "role": "citizen"})
        self.assertRedirects(resp, reverse("chat"))
        user = User.objects.get(phone="+919845012345")
        self.assertEqual((user.role, user.first_name, user.domains), ("citizen", "Asha Rao", ["health", "education"]))
        Claim.objects.create(text="Some news claim that must not show on chat home")
        page = self.client.get(reverse("chat"))
        self.assertContains(page, "WELCOME BACK, ASHA RAO")
        self.assertContains(page, "News & Verdicts")
        self.assertNotContains(page, "Some news claim")
        self.assertNotContains(page, "Verify Queue")

    def test_phone_is_unique_and_never_public(self):
        self.client.post(reverse("signup"), {**SIGNUP, "role": "citizen"})
        self.client.logout()
        resp = self.client.post(reverse("signup"), {**SIGNUP, "role": "citizen", "phone": "9845012345"})
        self.assertContains(resp, "already exists")

    def test_verifier_signup_requires_details_and_starts_pending(self):
        resp = self.client.post(reverse("signup"), {**SIGNUP, "role": "verifier"})
        self.assertContains(resp, "Verifiers must tell us their profession")
        resp = self.client.post(reverse("signup"), {**SIGNUP, "role": "verifier", "profession": "Doctor",
                                                    "languages": ["kn", "en"],
                                                    "linkedin_url": "https://www.linkedin.com/in/asha"})
        self.assertRedirects(resp, reverse("chat"))
        user = User.objects.get()
        self.assertTrue(user.is_pending_verifier)
        self.assertContains(self.client.get(reverse("chat")), "Verification pending")
        self.assertEqual(self.client.get(reverse("verify_dashboard")).status_code, 403)

    def test_linkedin_must_be_linkedin(self):
        resp = self.client.post(reverse("signup"), {**SIGNUP, "role": "verifier", "profession": "x",
                                                    "languages": ["en"], "linkedin_url": "javascript:alert(1)"})
        self.assertEqual(User.objects.count(), 0)
        self.assertContains(resp, "not allowed")

    def test_login_with_phone(self):
        User.objects.create_user("u1", password="strong-pass-2026", phone="+919845012345")
        resp = self.client.post(reverse("login"), {"username": "98450 12345", "password": "strong-pass-2026"})
        self.assertRedirects(resp, reverse("chat"))
        self.client.logout()
        resp = self.client.post(reverse("login"), {"username": "9845012345", "password": "wrong"})
        self.assertContains(resp, "Wrong phone number or password")

    def test_become_verifier(self):
        user = User.objects.create_user("u1", password="pw-123456")
        self.client.force_login(user)
        self.client.post(reverse("become_verifier"), {"profession": "Teacher", "interests": ["education"],
                                                      "languages": ["kn"]})
        user.refresh_from_db()
        self.assertTrue(user.is_pending_verifier)


@override_settings(RATELIMIT_ENABLE=False)
class CommunityVoteTests(TestCase):
    def setUp(self):
        self.claim = Claim.objects.create(text="VTU exams postponed", domain="education")
        self.user = User.objects.create_user("cit", password="pw-123456")
        self.client.force_login(self.user)

    def vote(self, verdict):
        return self.client.post(reverse("community_vote", args=[self.claim.pk]),
                                {"verdict": verdict, "reason": "<b>seen it</b>", "next": "/"})

    def test_one_changeable_vote_per_user(self):
        self.assertRedirects(self.vote("TRUE"), "/", fetch_redirect_response=False)
        self.vote("FALSE")
        vote = CommunityVote.objects.get()
        self.assertEqual((vote.verdict, vote.reason), ("FALSE", "seen it"))
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, "PENDING")  # community alone never moves it on

    def test_approved_verifier_cannot_cast_community_vote(self):
        v = User.objects.create_user("v", password="pw-123456", role="verifier", verifier_approved=True)
        self.client.force_login(v)
        self.assertEqual(self.vote("TRUE").status_code, 403)

    def test_closed_after_final(self):
        self.claim.status, self.claim.verdict = "FINAL", "FALSE"
        self.claim.save()
        self.vote("TRUE")
        self.assertEqual(CommunityVote.objects.count(), 0)

    def test_finalisation_posts_system_message_to_chat(self):
        thread = ChatThread.objects.create(owner=self.user, title="t", claim=self.claim)
        cast_community_vote(self.claim, self.user, "FALSE")
        for i in range(3):
            v = User.objects.create_user(f"v{i}", password="pw-123456", role="verifier", verifier_approved=True)
            cast_vote(self.claim, v, "FALSE", "checked", ["https://vtu.ac.in/x"])
        msg = thread.messages.last()
        self.assertEqual(msg.sender, ChatMessage.Sender.SYSTEM)
        self.assertIn("Verified: FALSE", msg.content)
        self.assertIn("by 3 verifiers", msg.content)

    def test_verifier_review_screen(self):
        v = User.objects.create_user("v", password="pw-123456", role="verifier", verifier_approved=True)
        self.client.force_login(v)
        url = reverse("verify_review", args=[self.claim.pk])
        self.assertContains(self.client.get(url), "AI RESEARCH (REFERENCE ONLY)")
        resp = self.client.post(url, {"verdict": "FALSE", "evidence_1": "https://vtu.ac.in/x"})
        self.assertContains(resp, "Please explain your verdict", status_code=400)
        resp = self.client.post(url, {"verdict": "FALSE", "note": "Checked VTU", "evidence_1": "https://vtu.ac.in/x"})
        self.assertRedirects(resp, reverse("verify_dashboard"))
        self.assertEqual(self.claim.votes.count(), 1)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 403)
