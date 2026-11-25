"""Insider assignment, track-record ranking, public verdict cards and AI agent verifiers.
External services are stubbed."""
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from claims.models import Claim
from research import agents, ai, connectors
from research.models import ResearchRun
from verification.assignment import assign_open_claims_to, assign_verifiers, org_keys, org_match, rank_verifiers
from verification.models import Assignment, VerifierStat, Vote
from verification.services import cast_vote

PW = "agents-pass-2026"
SOURCES = [
    {"url": "https://vtu.ac.in/notice/1", "title": "VTU: exams as scheduled", "snippet": "Exams will be held as scheduled.",
     "published": None},
    {"url": "https://www.thehindu.com/vtu-exams", "title": "No cancellation, says VTU", "snippet": "VTU denied it.",
     "published": None},
]
REFUTES = ({"stances": {1: ("refutes", ""), 2: ("refutes", "")}, "summary": "VTU denied the circular [1][2].",
            "facts": [], "assumptions": [], "conflicts": []}, "stub-model")
NOTHING = ({"stances": {}, "summary": "", "facts": [], "assumptions": [], "conflicts": []}, "stub-model")


def verifier(username, organisation="", domains=("education",), stats=(), **kw):
    u = User.objects.create_user(username, password=PW, first_name=username.title(), role="verifier",
                                 verifier_approved=True, domains=list(domains), languages=["en"],
                                 organisation=organisation, **kw)
    for domain, correct, total in stats:
        VerifierStat.objects.create(verifier=u, domain=domain, language="en", correct=correct, total=total)
    return u


class OrganisationMatchTests(TestCase):
    def test_org_keys(self):
        self.assertIn("vtu", org_keys("Visvesvaraya Technological University"))
        self.assertIn("vtu", org_keys("Board of Examinations, VTU"))
        self.assertIn("nvidia", org_keys("NVIDIA Corporation"))
        self.assertIn("nvidia", org_keys("Nvidia GPU team"))
        self.assertIn("nvidia", org_keys("", domain="nvidia.com"))
        self.assertEqual(org_keys("Government"), set())
        self.assertNotIn("udupi", org_keys("Taluk office, Udupi"))   # a place is not an organisation

    def test_insider_is_assigned_first_even_with_a_weaker_record(self):
        expert = verifier("expert", stats=[("education", 19, 20)])          # weight 0.88
        insider = verifier("board", organisation="VTU (Board of Examinations)")  # new: 0.5
        claim = Claim.objects.create(text="VTU cancelled all 5th semester exams", domain="education")
        ranked = rank_verifiers(claim)
        self.assertEqual([v for v, *_ in ranked][:2], [insider, expert])
        self.assertIn("works at VTU", ranked[0][2])

    def test_insider_matched_by_ai_entities_and_outside_their_field(self):
        dev = verifier("dev", organisation="NVIDIA", domains=["technology"])
        claim = Claim.objects.create(text="Big chip company shares will crash 50% on Monday", domain="finance",
                                     entities=[{"name": "NVIDIA Corporation", "short": "NVDA"}])
        self.assertEqual(org_match(dev.verifier_profile, claim), "NVIDIA")
        self.assertEqual([a.verifier for a in assign_verifiers(claim)], [dev])

    def test_better_track_record_ranks_higher(self):
        good = verifier("good", stats=[("education", 9, 10)])
        weak = verifier("weak", stats=[("education", 2, 10)])
        claim = Claim.objects.create(text="Schools closed tomorrow", domain="education")
        ranked = rank_verifiers(claim)
        self.assertEqual([v for v, *_ in ranked], [good, weak])
        self.assertIn("90% accurate in 10", ranked[0][2])
        self.assertEqual((good.verdict_accuracy, good.verdicts_scored), (90, 10))

    def test_approval_hands_the_insider_their_open_claims(self):
        claim = Claim.objects.create(text="Nvidia is hiring 10,000 freshers in Mysuru", domain="technology")
        dev = verifier("dev2", organisation="Nvidia", domains=["finance"])
        self.assertEqual([a.claim for a in assign_open_claims_to(dev)], [claim])


@override_settings(GROQ_API_KEY="test-key", RATELIMIT_ENABLE=False, FINAL_MIN_HUMAN_VOTES=1)
class AIAgentTests(TestCase):
    def setUp(self):
        stub = {name: (lambda q, l: [dict(s) for s in SOURCES]) for name in connectors.CONNECTORS}
        for p in (mock.patch.dict(connectors.CONNECTORS, stub),
                  mock.patch("research.pipeline.fetch_article", return_value=("", ""))):
            p.start()
            self.addCleanup(p.stop)
        self.claim = Claim.objects.create(text="URGENT: VTU has cancelled all 5th semester exams",
                                          domain="education", status="PENDING")

    def test_agents_research_and_vote_with_all_their_sources(self):
        with mock.patch.object(ai, "assess", return_value=REFUTES) as assess:
            votes = agents.run_agents(self.claim)
        self.assertEqual(len(votes), 3)
        self.assertEqual({v.verdict for v in votes}, {"FALSE"})
        self.assertEqual(len({c.kwargs["model"] for c in assess.call_args_list}), 3)   # spread over models
        vote = votes[0]
        self.assertTrue(vote.verifier.is_ai_agent)
        self.assertEqual(vote.weight_at_vote, 0.5)            # new agent: same prior as a new person
        self.assertIn("VTU denied", vote.note)
        self.assertEqual(sorted(vote.evidence.values_list("url", flat=True)), sorted(s["url"] for s in SOURCES))
        self.assertEqual(vote.agent_run.sources.count(), 2)
        self.assertFalse(ResearchRun.objects.filter(agent__isnull=False).exists())  # main runs only
        self.assertEqual(ResearchRun.all_runs.filter(agent__isnull=False).count(), 3)
        self.assertEqual(Assignment.objects.filter(claim=self.claim, status="done").count(), 3)

        # Three agents agree, but a person must vote before it is final.
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, "UNDER_REVIEW")
        cast_vote(self.claim, verifier("ravi"), "FALSE", "No circular on vtu.ac.in", ["https://vtu.ac.in/"])
        self.claim.refresh_from_db()
        self.assertEqual((self.claim.status, self.claim.verdict), ("FINAL", "FALSE"))
        agent = User.objects.get(username="ai_news_desk")
        self.assertEqual((agent.verdict_accuracy, agent.verdicts_scored), (100, 1))  # scored like people

        # Running again does nothing (one vote each).
        with mock.patch.object(ai, "assess", return_value=REFUTES):
            self.assertEqual(agents.run_agents(self.claim), [])

    def test_agents_abstain_without_enough_evidence(self):
        with mock.patch.object(ai, "assess", return_value=NOTHING):
            self.assertEqual(agents.run_agents(self.claim), [])
        self.assertFalse(Vote.objects.exists())
        self.assertIn("abstained", Assignment.objects.filter(claim=self.claim).first().reason)

    def test_rate_limited_agent_tries_other_models_then_retries_later(self):
        calls = []

        def flaky(claim, items, lang, model=None):
            calls.append(model)
            return (NOTHING[0], None) if len(calls) <= 9 else REFUTES   # all 3 models fail for 3 agents
        with mock.patch.object(ai, "assess", side_effect=flaky):
            self.assertEqual(agents.run_agents(self.claim), [])
            self.assertEqual(len(set(calls)), 3)                # each agent tried all three models
            self.assertFalse(Assignment.objects.filter(claim=self.claim).exists())   # no fake abstention
            self.assertEqual(len(agents.run_agents(self.claim)), 3)                   # retried later
        self.assertEqual(ResearchRun.all_runs.filter(agent__isnull=False, status="failed").count(), 0)

    @override_settings(GROQ_API_KEY="")
    def test_no_key_no_agents(self):
        self.assertEqual(agents.run_agents(self.claim), [])
        self.assertFalse(User.objects.filter(username__startswith="ai_").exists())

    def test_claim_page_shows_each_verdict_with_verifier_record_and_sources_to_anyone(self):
        with mock.patch.object(ai, "assess", return_value=REFUTES):
            agents.run_agents(self.claim)
        board = verifier("nandini", organisation="VTU", stats=[("education", 9, 10)], profession="Board member")
        cast_vote(self.claim, board, "FALSE", "Checked with the exam section", ["https://vtu.ac.in/notices"])
        regular = User.objects.create_user("asha", password=PW, phone="+919845011111")
        self.client.force_login(regular)
        page = self.client.get(reverse("claim_detail", args=[self.claim.pk]))
        self.assertContains(page, "VERIFIED BY")
        self.assertContains(page, "Nandini")
        self.assertContains(page, "Board member · VTU")
        self.assertContains(page, "Works at VTU, which this claim is about")
        self.assertContains(page, "Checked with the exam section")
        self.assertContains(page, "https://vtu.ac.in/notices")
        self.assertContains(page, "AI agent")
        self.assertContains(page, "Sources this AI agent used: 2")
        self.assertContains(page, "No cancellation, says VTU")
        self.assertContains(page, "91% accurate in 11 verdicts")   # 9/10 before + this one, now final
        share = self.client.get(reverse("share", args=[self.claim.share_slug]))
        self.assertContains(share, "Nandini")
