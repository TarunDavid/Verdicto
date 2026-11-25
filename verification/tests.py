from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from claims.duplicates import normalize
from claims.models import Claim
from claims.validators import sanitize_text, validate_safe_url

from . import engine
from .models import Evidence, VerdictLog, VerifierStat, Vote
from .services import AlreadyVoted, admin_override, cast_vote


class EngineTests(SimpleTestCase):
    def test_weights(self):
        self.assertAlmostEqual(engine.beta_weight(8, 8), 0.8333, places=3)
        self.assertAlmostEqual(engine.beta_weight(2, 2), 0.6667, places=3)
        self.assertEqual(engine.beta_weight(0, 0), 0.5)

    def test_work_email_trust_bonus(self):
        self.assertEqual(engine.beta_weight(0, 0, work_email_verified=True), 0.6)

    def test_worked_example(self):
        votes = [("FALSE", engine.beta_weight(8, 8)), ("FALSE", engine.beta_weight(2, 2)),
                 ("TRUE", engine.beta_weight(0, 0))]
        result = engine.compute_verdict(votes)
        self.assertEqual(result.verdict, "FALSE")
        self.assertEqual(result.status, "FINAL")
        self.assertAlmostEqual(result.confidence, 0.75)
        self.assertEqual(engine.explain(votes),
                         "FALSE = 0.83 + 0.67 = 1.50; TRUE = 0.50; confidence = 75%")

    def test_fewer_than_three_votes_stays_under_review(self):
        result = engine.compute_verdict([("FALSE", 0.9), ("FALSE", 0.9)])
        self.assertEqual(result.status, "UNDER_REVIEW")
        self.assertIsNone(result.verdict)
        self.assertEqual(result.leading, "FALSE")

    def test_low_confidence_stays_under_review(self):
        result = engine.compute_verdict([("FALSE", 0.5), ("TRUE", 0.5), ("FALSE", 0.5)])
        self.assertEqual(result.status, "UNDER_REVIEW")  # 67% < 70%

    def test_stat_lookup_falls_back_to_domain_then_prior(self):
        stats = [("health", "en", 4, 5), ("health", "kn", 2, 3)]
        self.assertEqual(engine.lookup_record(stats, "health", "kn"), (2, 3))
        self.assertEqual(engine.lookup_record(stats, "health", "hi"), (6, 8))
        self.assertEqual(engine.lookup_record(stats, "education", "en"), (0, 0))

    def test_score_delta_no_double_counting(self):
        self.assertEqual(engine.score_delta("FALSE", None, "FALSE"), (1, 1))
        self.assertEqual(engine.score_delta("FALSE", "FALSE", "TRUE"), (-1, 0))
        self.assertEqual(engine.score_delta("FALSE", "FALSE", None), (-1, -1))


class UrlAndTextSafetyTests(SimpleTestCase):
    def test_blocks_dangerous_schemes(self):
        for bad in ["javascript:alert(1)", "  JaVaScRiPt:alert(1)", "java\tscript:alert(1)",
                    "data:text/html;base64,PHNjcmlwdD4=", "DATA:text/html,x", "ftp://example.com",
                    "not a url"]:
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_safe_url(bad)

    def test_allows_http_and_https(self):
        self.assertEqual(validate_safe_url(" https://pib.gov.in/x "), "https://pib.gov.in/x")
        validate_safe_url("http://example.com")

    def test_sanitize_strips_tags(self):
        self.assertEqual(sanitize_text("<script>alert(1)</script><b>Hi</b> & bye"), "Hi & bye")

    def test_normalize_keeps_kannada(self):
        self.assertEqual(normalize("Forwarded many times 🔴 ನಾಳೆ ಶಾಲೆಗಳಿಗೆ ರಜೆ!!! https://x.co"),
                         "ನಾಳೆ ಶಾಲೆಗಳಿಗೆ ರಜೆ")


def make_verifier(name, approved=True, **kw):
    return User.objects.create_user(name, password="pw-123456", role=User.Role.VERIFIER,
                                    verifier_approved=approved, **kw)


class VoteAndLogTests(TestCase):
    def setUp(self):
        self.claim = Claim.objects.create(text="Hot lemon water cures dengue", domain="health",
                                          language="en")
        self.a, self.b, self.c = make_verifier("a"), make_verifier("b"), make_verifier("c")
        VerifierStat.objects.create(verifier=self.a, domain="health", language="en", correct=8, total=8)
        VerifierStat.objects.create(verifier=self.b, domain="health", language="en", correct=2, total=2)

    def test_worked_example_end_to_end(self):
        cast_vote(self.claim, self.a, "FALSE", evidence_urls=["https://mohfw.gov.in/dengue"])
        cast_vote(self.claim, self.b, "FALSE", evidence_urls=["https://example.org"])
        cast_vote(self.claim, self.c, "TRUE", evidence_urls=["https://example.org"])
        self.claim.refresh_from_db()
        self.assertEqual((self.claim.status, self.claim.verdict), ("FINAL", "FALSE"))
        self.assertAlmostEqual(self.claim.confidence, 0.75)
        self.assertTrue(Evidence.objects.get(url="https://mohfw.gov.in/dengue").is_official)
        log = self.claim.logs.last()
        self.assertEqual(log.new_status, "FINAL")
        self.assertIn("75%", log.reason)
        # Track record updated once per voter.
        a_stat = VerifierStat.objects.get(verifier=self.a)
        c_stat = VerifierStat.objects.get(verifier=self.c)
        self.assertEqual((a_stat.correct, a_stat.total), (9, 9))
        self.assertEqual((c_stat.correct, c_stat.total), (0, 1))

    def test_one_vote_per_verifier_in_database(self):
        Vote.objects.create(claim=self.claim, verifier=self.a, verdict="FALSE", weight_at_vote=0.5)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Vote.objects.create(claim=self.claim, verifier=self.a, verdict="TRUE", weight_at_vote=0.5)

    def test_one_vote_per_verifier_in_service(self):
        cast_vote(self.claim, self.a, "UNVERIFIED")
        with self.assertRaises(AlreadyVoted):
            cast_vote(self.claim, self.a, "FALSE", evidence_urls=["https://x.org"])
        self.assertEqual(self.claim.votes.count(), 1)

    def test_verdict_log_is_append_only(self):
        log = VerdictLog.objects.create(claim=self.claim, new_status="UNDER_REVIEW", reason="Created")
        log.reason = "tampered"
        with self.assertRaises(ValidationError):
            log.save()
        with self.assertRaises(ValidationError):
            log.delete()
        with self.assertRaises(ValidationError):
            VerdictLog.objects.all().update(reason="x")

    def test_admin_override_rescores_without_double_count(self):
        admin = User.objects.create_user("boss", password="pw-123456", role=User.Role.ADMIN)
        for v, verdict in ((self.a, "FALSE"), (self.b, "FALSE"), (self.c, "TRUE")):
            cast_vote(self.claim, v, verdict, evidence_urls=["https://x.org"])
        admin_override(self.claim, admin, "TRUE", "Official clarification published")
        a_stat = VerifierStat.objects.get(verifier=self.a)
        c_stat = VerifierStat.objects.get(verifier=self.c)
        self.assertEqual((a_stat.correct, a_stat.total), (8, 9))
        self.assertEqual((c_stat.correct, c_stat.total), (1, 1))
        self.assertEqual(self.claim.logs.last().changed_by, admin)


@override_settings(RATELIMIT_ENABLE=False)
class RoleTests(TestCase):
    def setUp(self):
        self.claim = Claim.objects.create(text="VTU exams postponed to June", domain="education")
        self.citizen = User.objects.create_user("cit", password="pw-123456")
        self.pending = make_verifier("pending", approved=False)
        self.verifier = make_verifier("ver")
        self.admin = User.objects.create_user("adm", password="pw-123456", role=User.Role.ADMIN)
        self.vote_data = {"verdict": "FALSE", "note": "No such circular on VTU site",
                          "evidence_1": "https://vtu.ac.in/notice"}

    def post_vote(self, user, url_name="vote"):
        self.client.force_login(user)
        return self.client.post(reverse(url_name, args=[self.claim.pk]), self.vote_data)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.post(reverse("vote", args=[self.claim.pk]), self.vote_data)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])

    def test_citizen_cannot_vote(self):
        self.assertEqual(self.post_vote(self.citizen).status_code, 403)
        self.assertEqual(Vote.objects.count(), 0)

    def test_unapproved_verifier_cannot_vote(self):
        self.assertEqual(self.post_vote(self.pending).status_code, 403)

    def test_approved_verifier_can_vote(self):
        self.assertEqual(self.post_vote(self.verifier).status_code, 302)
        self.assertEqual(Vote.objects.count(), 1)

    def test_only_admin_can_override(self):
        data = {"verdict": "TRUE", "reason": "Checked with VTU"}
        for user in (self.citizen, self.verifier):
            self.client.force_login(user)
            resp = self.client.post(reverse("override", args=[self.claim.pk]), data)
            self.assertEqual(resp.status_code, 403)
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("override", args=[self.claim.pk]), data)
        self.assertEqual(resp.status_code, 302)
        self.claim.refresh_from_db()
        self.assertEqual((self.claim.status, self.claim.verdict), ("FINAL", "TRUE"))

    def test_override_requires_reason(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("override", args=[self.claim.pk]), {"verdict": "TRUE", "reason": " "})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(VerdictLog.objects.count(), 0)

    def test_api_structured_errors(self):
        resp = self.post_vote(self.citizen, "api_vote")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"]["code"], "forbidden")

        self.client.force_login(self.verifier)
        url = reverse("api_vote", args=[self.claim.pk])
        resp = self.client.post(url, {"verdict": "FALSE", "note": "x", "evidence_1": "javascript:alert(1)"})
        self.assertEqual(resp.status_code, 400)
        body = resp.json()["error"]
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("evidence_1", body["fields"])

        self.assertEqual(self.client.post(url, self.vote_data).status_code, 201)
        resp = self.client.post(url, self.vote_data)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"]["code"], "already_voted")

    def test_submit_rejects_javascript_source_url(self):
        self.client.force_login(self.citizen)
        resp = self.client.post(reverse("submit"), {"text": "Schools closed tomorrow in all districts",
                                                    "source_url": "javascript:alert(1)",
                                                    "language": "en", "domain": "education"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Claim.objects.count(), 1)

    def test_public_pages_render(self):
        # Share links and claim pages are public; everything else needs login.
        self.assertEqual(self.client.get(reverse("claim_detail", args=[self.claim.pk])).status_code, 200)
        resp = self.client.get(reverse("share", args=[self.claim.share_slug]))
        self.assertContains(resp, 'property="og:title"')
        self.assertContains(resp, "https://wa.me/?text=")
