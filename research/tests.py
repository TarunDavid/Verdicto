from datetime import timedelta
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from chat.models import ChatThread
from claims.models import Claim
from verification.assignment import assign_verifiers, rank_verifiers, reassign_stale
from verification.models import Assignment, VerifierStat

from . import ai, connectors, scoring
from .credibility import classify
from .fetch import UnsafeURL, assert_public_host
from .models import ResearchRun
from .pipeline import run_research, start_research

FAKE_RESULTS = [
    {"url": "https://pib.gov.in/fc/1", "title": "PIB Fact Check: no holiday declared",
     "snippet": "The circular claiming a school holiday is fake, says the district administration.",
     "published": None},
    {"url": "https://www.thehindu.com/news/x", "title": "Schools open as usual",
     "snippet": "Schools in the district will function as usual tomorrow.", "published": None},
    {"url": "https://randomblog.example.com/p", "title": "Holiday!!",
     "snippet": "Schools are closed tomorrow, share widely.", "published": None},
]


def fake_connector(query, language):
    return [dict(r) for r in FAKE_RESULTS]


OFFLINE = dict(RESEARCH_SYNC=True, RESEARCH_CONNECTORS=["fake"], GROQ_API_KEY="",
               RATELIMIT_ENABLE=False)


class ScoringTests(SimpleTestCase):
    def test_unclear_with_little_evidence(self):
        self.assertEqual(scoring.truth_score([("supports", 0.3, None)]), (None, "UNCLEAR", "low"))

    def test_likely_false(self):
        score, label, conf = scoring.truth_score([("refutes", 1.0, None), ("refutes", 0.8, None),
                                                  ("supports", 0.5, None)])
        self.assertEqual(label, "LIKELY_FALSE")
        self.assertAlmostEqual(score, 21.7, places=1)
        self.assertEqual(conf, "medium")

    def test_neutral_sources_do_not_count(self):
        self.assertEqual(scoring.truth_score([("neutral", 1.0, None)] * 5)[1], "UNCLEAR")

    def test_old_sources_count_less(self):
        old = timezone.now() - timedelta(days=800)
        self.assertEqual(scoring.recency_factor(old), 0.75)

    def test_credibility_tiers(self):
        self.assertEqual(classify("https://pib.gov.in/x")[0], "official")
        self.assertEqual(classify("https://www.altnews.in/x")[0], "fact-checker")
        self.assertEqual(classify("https://www.thehindu.com/x")[0], "news")
        self.assertEqual(classify("https://reddit.com/r/x")[0], "social")
        self.assertEqual(classify("https://evil.gov.in.example.com/")[0], "web")


class SSRFTests(SimpleTestCase):
    def test_blocks_internal_addresses(self):
        for url in ["http://127.0.0.1/", "http://localhost:8000/admin/", "http://169.254.169.254/latest",
                    "http://10.0.0.5/", "http://[::1]/"]:
            with self.subTest(url=url), self.assertRaises(UnsafeURL):
                assert_public_host(url)

    def test_blocks_dangerous_schemes(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            assert_public_host("javascript:alert(1)")


class AssessGuardTests(SimpleTestCase):
    @override_settings(GROQ_API_KEY="test")
    def test_invented_quotes_are_dropped(self):
        sources = [{"id": 1, "title": "Schools open", "publisher": "x", "tier": "news",
                    "snippet": "Schools will function as usual tomorrow."}]
        fake = {"sources": [{"id": 1, "stance": "refutes", "quote": "the DC denied it angrily"},
                            {"id": 9, "stance": "supports", "quote": ""}], "summary": "s"}
        with mock.patch.object(ai, "chat_json", return_value=(fake, "m")):
            out, _ = ai.assess("Schools closed", sources, "en")
        self.assertEqual(out["stances"], {1: ("neutral", "")})

    @override_settings(GROQ_API_KEY="test")
    def test_real_quotes_kept(self):
        sources = [{"id": 1, "title": "t", "publisher": "x", "tier": "news",
                    "snippet": "Schools will function as usual tomorrow."}]
        fake = {"sources": [{"id": 1, "stance": "refutes", "quote": "schools will  function as usual"}]}
        with mock.patch.object(ai, "chat_json", return_value=(fake, "m")):
            out, _ = ai.assess("Schools closed", sources, "en")
        self.assertEqual(out["stances"][1][0], "refutes")

    @override_settings(GROQ_API_KEY="test")
    def test_malformed_reply_does_not_crash(self):
        sources = [{"id": 1, "title": "t", "publisher": "x", "tier": "news", "snippet": "s"}]
        for fake in ({"sources": ["1: refutes"]}, {"sources": "refutes"}, {"sources": [None, {"id": "x"}]}):
            with mock.patch.object(ai, "chat_json", return_value=(fake, "m")):
                out, model = ai.assess("Schools closed", sources, "en")
            self.assertEqual((out["stances"], model), ({}, "m"))


def make_verifier(name, domains, languages=("en",), approved=True, **kw):
    return User.objects.create_user(name, password="pw-123456", role=User.Role.VERIFIER,
                                    verifier_approved=approved, domains=list(domains),
                                    languages=list(languages), **kw)


@override_settings(**OFFLINE)
class PipelineTests(TestCase):
    def setUp(self):
        self.patch = mock.patch.dict(connectors.CONNECTORS, {"fake": fake_connector})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        make_verifier("edu1", ["education"])
        make_verifier("edu2", ["education"], ["kn"])
        self.claim = Claim.objects.create(text="Schools closed tomorrow in Chikkamagaluru, says DC",
                                          domain="education", status=Claim.Status.RESEARCHING)

    def test_runs_without_ai_and_never_sets_a_verdict(self):
        run = start_research(self.claim)
        run.refresh_from_db()
        self.claim.refresh_from_db()
        self.assertEqual(run.status, "done")
        self.assertEqual(run.sources.count(), 3)
        self.assertEqual(run.label, "UNCLEAR")  # no AI -> stances not judged
        self.assertEqual(self.claim.status, "PENDING")
        self.assertIsNone(self.claim.verdict)
        self.assertEqual(self.claim.assignments.count(), 2)
        self.assertIn("Not a verdict", self.claim.logs.last().reason)

    def test_ai_provisional_label_does_not_finalise(self):
        def assess(claim, items, lang, model=None):   # the blog supports it, the others refute it
            stances = {it["id"]: ("supports" if "randomblog" in it["url"] else "refutes", "") for it in items}
            return {"stances": stances, "summary": "Official sources deny it [1][2].", "facts": [],
                    "assumptions": [], "conflicts": []}, "m"
        with mock.patch.object(ai, "assess", side_effect=assess):
            run = start_research(self.claim)
        run.refresh_from_db()
        self.claim.refresh_from_db()
        self.assertEqual(run.label, "LIKELY_FALSE")
        self.assertLess(run.truth_score, 30)
        self.assertEqual(self.claim.status, "PENDING")
        self.assertIsNone(self.claim.verdict)

    def test_connector_failure_is_tolerated(self):
        def boom(q, lang):
            raise RuntimeError("down")
        with mock.patch.dict(connectors.CONNECTORS, {"fake": boom}):
            run = start_research(self.claim)
        run.refresh_from_db()
        self.assertEqual(run.status, "done")
        self.assertEqual(run.connectors["fake"], "error: RuntimeError")
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, "PENDING")

    def test_private_individual_claims_are_held(self):
        info = {**ai.fallback_understanding("x"), "targets_private_individual": True,
                "moderation_reason": "Personal life of a private person"}
        with mock.patch.object(ai, "understand", return_value=(info, "m")):
            start_research(self.claim)
        self.claim.refresh_from_db()
        self.assertTrue(self.claim.is_hidden)
        self.assertEqual(self.claim.status, "REJECTED")
        self.assertEqual(self.claim.assignments.count(), 0)
        self.assertEqual(self.client.get(reverse("claim_detail", args=[self.claim.pk])).status_code, 404)
        self.client.force_login(User.objects.create_user("reader", password="pw-123456"))
        self.assertNotContains(self.client.get(reverse("feed")), "Chikkamagaluru, says DC")


class AssignmentTests(TestCase):
    def setUp(self):
        self.claim = Claim.objects.create(text="Dengue cure claim", domain="health", language="kn")
        self.expert = make_verifier("expert", ["health"], ["kn"])
        VerifierStat.objects.create(verifier=self.expert, domain="health", language="kn", correct=9, total=10)
        self.novice = make_verifier("novice", ["health"], ["kn"])
        self.english = make_verifier("english", ["health"], ["en"])
        self.related = make_verifier("scientist", ["science"], ["kn"])
        self.unrelated = make_verifier("techie", ["technology"], ["kn"])
        self.pending = make_verifier("pending", ["health"], ["kn"], approved=False)

    def test_ranking_order_and_eligibility(self):
        names = [v.username for v, _, _ in rank_verifiers(self.claim)]
        self.assertEqual(names, ["expert", "novice", "scientist", "english"])

    def test_workload_lowers_priority(self):
        busy = Claim.objects.create(text="other", domain="health")
        for _ in range(1):
            Assignment.objects.create(claim=busy, verifier=self.expert, score=1, reason="x")
        other = Claim.objects.create(text="x2", domain="health")
        Assignment.objects.create(claim=other, verifier=self.expert, score=1, reason="x")
        names = [v.username for v, _, _ in rank_verifiers(self.claim)]
        self.assertEqual(names[0], "novice")  # 0.5 free beats 0.79 / 3

    @override_settings(ASSIGN_TOP_N=3, ASSIGNMENT_TIMEOUT_HOURS=2)
    def test_assign_top_n_and_reassign_stale(self):
        created = assign_verifiers(self.claim)
        self.assertEqual([a.verifier.username for a in created], ["expert", "novice", "scientist"])
        self.assertEqual(assign_verifiers(self.claim), [])  # already full
        Assignment.objects.filter(verifier=self.novice).update(created_at=timezone.now() - timedelta(hours=3))
        Assignment.objects.exclude(verifier=self.novice).update(created_at=timezone.now())
        self.assertEqual(reassign_stale(), 1)
        statuses = dict(self.claim.assignments.values_list("verifier__username", "status"))
        self.assertEqual(statuses["novice"], "expired")
        self.assertEqual(statuses["english"], "assigned")


@override_settings(**OFFLINE)
class ChatTests(TestCase):
    def setUp(self):
        self.patch = mock.patch.dict(connectors.CONNECTORS, {"fake": fake_connector})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.user = User.objects.create_user("asha", password="pw-123456", first_name="Asha")
        self.client.force_login(self.user)

    def test_submission_creates_thread_claim_and_research(self):
        resp = self.client.post(reverse("chat_send"), {"text": "Free laptops for all PU students, register now",
                                                       "language": "en"})
        thread = ChatThread.objects.get()
        self.assertRedirects(resp, reverse("chat_thread", args=[thread.pk]))
        self.assertEqual(thread.messages.count(), 2)
        self.assertEqual(thread.claim.status, "PENDING")
        self.assertEqual(ResearchRun.objects.get().status, "done")
        card = self.client.get(reverse("research_card", args=[thread.claim.pk]))
        self.assertContains(card, "AI-PROVISIONAL")
        self.assertContains(card, "pib.gov.in")

    def test_duplicate_attaches_to_existing_claim(self):
        text = "Free laptops for all PU students, register now"
        self.client.post(reverse("chat_send"), {"text": text})
        self.client.post(reverse("chat_send"), {"text": "Forwarded: FREE laptops for all PU students!! register now"})
        self.assertEqual(Claim.objects.count(), 1)
        self.assertEqual(Claim.objects.get().submission_count, 2)
        self.assertEqual(ChatThread.objects.count(), 2)

    def test_threads_are_private(self):
        self.client.post(reverse("chat_send"), {"text": "Free laptops for all PU students, register now"})
        thread = ChatThread.objects.get()
        other = self.client_class()
        other.force_login(User.objects.create_user("someone", password="pw-123456"))
        self.assertEqual(other.get(reverse("chat_thread", args=[thread.pk])).status_code, 404)
        self.assertEqual(self.client_class().get(reverse("chat_thread", args=[thread.pk])).status_code, 302)

    def test_rejects_javascript_link(self):
        resp = self.client.post(reverse("chat_send"), {"text": "javascript:alert(1)"})
        self.assertIn("error=", resp["Location"])
        self.assertEqual(Claim.objects.count(), 0)

    def test_followup_without_ai_falls_back(self):
        self.client.post(reverse("chat_send"), {"text": "Free laptops for all PU students, register now"})
        thread = ChatThread.objects.get()
        self.client.post(reverse("chat_reply", args=[thread.pk]), {"text": "Is it official?"})
        self.assertIn("can't answer", thread.messages.last().content)

    def test_archive_lists_only_final(self):
        Claim.objects.create(text="Open one", status="PENDING")
        Claim.objects.create(text="Done one", status="FINAL", verdict="FALSE", finalized_at=timezone.now())
        resp = self.client.get(reverse("archive"))
        self.assertContains(resp, "Done one")
        self.assertNotContains(resp, "Open one")


class RelevanceTests(SimpleTestCase):
    def test_names_weigh_more_and_query_words_dont_count(self):
        from .pipeline import _key_terms, _relevance
        terms = _key_terms(["WhatsApp will start charging every user Rs 499 per month"])
        self.assertGreater(_relevance({"title": "Fact check: WhatsApp is not becoming a paid app"}, terms),
                           _relevance({"title": "Mobile data per user per month"}, terms))
        self.assertEqual(_relevance({"title": "PIB Fact Check Unit"}, terms), 0)

    def test_kannada_words_stay_whole(self):
        from .pipeline import _key_terms
        self.assertIn("ಚಿಕ್ಕಮಗಳೂರು", _key_terms(["ಚಿಕ್ಕಮಗಳೂರು ಜಿಲ್ಲೆಯ ಶಾಲೆಗಳಿಗೆ ರಜೆ"]))
