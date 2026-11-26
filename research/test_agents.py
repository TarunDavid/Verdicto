"""Insider assignment, track-record ranking, public verdict cards and AI agent verifiers.
External services are stubbed."""
from unittest import mock

from django.test import TestCase, TransactionTestCase, override_settings
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


class AgentRosterTests(TestCase):
    def test_more_than_twenty_distinct_agents(self):
        names = [a["username"] for a in agents.AGENTS]
        self.assertGreater(len(names), 20)
        self.assertEqual(len(names), len(set(names)))
        users = agents.ensure_agents()
        self.assertTrue(all(u.is_ai_agent and u.can_vote and not u.has_usable_password() for u, _ in users))
        display = [u.display_name for u, _ in users]
        self.assertEqual(len(set(display)), len(display))                    # each has its own name
        self.assertFalse(any("AI Agent" in d for d in display))
        self.assertEqual(users[0][0].display_name, "Satya")

    def test_picks_field_specialists_plus_generalist_fact_checkers(self):
        roster = agents.ensure_agents()
        claim = Claim.objects.create(text="VTU cancelled exams", domain="education", language="en")
        picked = [s["username"] for _, s in agents.pick_agents(claim, roster, 6)]
        self.assertEqual(len(picked), 6)
        self.assertIn("ai_education_desk", picked)
        self.assertIn("ai_exams_desk", picked)
        self.assertIn("ai_news_desk", picked)
        self.assertNotIn("ai_health_desk", picked)
        self.assertNotIn("ai_kannada_media", picked)               # English claim
        kn = Claim.objects.create(text="ಶಾಲೆಗಳಿಗೆ ರಜೆ", domain="education", language="kn")
        self.assertIn("ai_kannada_media", [s["username"] for _, s in agents.pick_agents(kn, roster, 6)])

    def test_better_track_record_gets_picked(self):
        roster = agents.ensure_agents()
        claim = Claim.objects.create(text="Dengue cure", domain="health", language="en")
        before = [s["username"] for _, s in agents.pick_agents(claim, roster, 4)]
        star = next(u for u, s in roster if s["username"] == "ai_reference_desk")   # a generalist not picked
        self.assertNotIn("ai_reference_desk", before)
        VerifierStat.objects.create(verifier=star, domain="health", language="en", correct=20, total=20)
        self.assertIn("ai_reference_desk", [s["username"] for _, s in agents.pick_agents(claim, roster, 4)])


@override_settings(GROQ_API_KEY="test-key", RATELIMIT_ENABLE=False, FINAL_MIN_HUMAN_VOTES=1,
                   AI_AGENTS_PER_CLAIM=3, AI_AGENT_WEIGHT_CAP=0.5, AI_AGENT_WORKERS=1, AI_AGENT_RETRIES=0)
class AIAgentTests(TestCase):
    def setUp(self):
        self.calls = []

        def stub_for(name):
            def search(q, l):
                self.calls.append((name, q, l))
                return [dict(s) for s in SOURCES]
            return search
        stub = {name: stub_for(name) for name in connectors.CONNECTORS}
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
        agent = votes[0].verifier
        self.assertEqual((agent.verdict_accuracy, agent.verdicts_scored), (100, 1))  # scored like people

        # Agents shared searches: no identical request went out twice.
        self.assertEqual(len(self.calls), len(set(self.calls)))

        # Running again does nothing (one vote each).
        with mock.patch.object(ai, "assess", return_value=REFUTES):
            self.assertEqual(agents.run_agents(self.claim), [])

    def test_many_agents_cannot_outweigh_people(self):
        with mock.patch.object(ai, "assess", return_value=REFUTES):
            agents.run_agents(self.claim, n=6)                     # 6 agents say FALSE
        self.assertEqual(Vote.objects.count(), 6)
        cast_vote(self.claim, verifier("p1"), "TRUE", "Saw the notice", ["https://vtu.ac.in/a"])
        cast_vote(self.claim, verifier("p2"), "TRUE", "Confirmed", ["https://vtu.ac.in/b"])
        self.claim.refresh_from_db()
        # people TRUE 1.0; agents FALSE 3.0 scaled to 1.0 (cap 50%) -> a tie, not a FALSE verdict
        self.assertEqual(self.claim.status, "UNDER_REVIEW")
        self.assertAlmostEqual(self.claim.confidence, 0.5)
        page = self.client.get(reverse("claim_detail", args=[self.claim.pk]))
        self.assertContains(page, "AI agents: 6 votes, weights × 0.33")

    @override_settings(AI_AGENTS_PER_CLAIM=0)
    def test_all_agents_give_a_verdict_by_default(self):
        with mock.patch.object(ai, "assess", return_value=REFUTES):
            votes = agents.run_agents(self.claim)
        self.assertEqual(len(votes), len(agents.AGENTS))                   # all 26, Kannada desk too
        self.assertEqual({v.verifier.display_name for v in votes}, set(agents.NAMES.values()))

    @override_settings(AI_AGENT_RETRIES=1, AI_AGENT_RETRY_DELAY=0)
    def test_rate_limited_agents_are_retried_automatically(self):
        calls = []

        def flaky(claim, items, lang, model=None):
            calls.append(model)
            return (NOTHING[0], None) if len(calls) <= 9 else REFUTES   # first round: every model fails
        with mock.patch.object(ai, "assess", side_effect=flaky):
            self.assertEqual(len(agents.run_agents(self.claim)), 3)

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
        self.assertContains(page, "🤖 AI")
        self.assertContains(page, "AI VERIFIERS")
        self.assertContains(page, "3 of 3 AI verifiers have answered")
        self.assertContains(page, "Sources this AI agent used: 2")
        self.assertContains(page, "No cancellation, says VTU")
        self.assertContains(page, "91% accurate in 11 verdicts")   # 9/10 before + this one, now final
        share = self.client.get(reverse("share", args=[self.claim.share_slug]))
        self.assertContains(share, "Nandini")


@override_settings(GROQ_API_KEY="test-key", RATELIMIT_ENABLE=False, AI_AGENTS_PER_CLAIM=0, AI_AGENT_WORKERS=3,
                   AI_AGENT_RETRIES=0, AI_AGENT_WEIGHT_CAP=0.5)
class ParallelAgentTests(TransactionTestCase):
    """Real threads (TransactionTestCase: other threads can see the data)."""

    def test_all_agents_vote_in_parallel_without_lost_votes(self):
        stub = {name: (lambda q, l: [dict(s) for s in SOURCES]) for name in connectors.CONNECTORS}
        claim = Claim.objects.create(text="URGENT: VTU has cancelled all 5th semester exams", domain="education",
                                     status="PENDING")
        with mock.patch.dict(connectors.CONNECTORS, stub), \
                mock.patch("research.pipeline.fetch_article", return_value=("", "")), \
                mock.patch.object(ai, "assess", return_value=REFUTES):
            votes = agents.run_agents(claim)
        self.assertEqual(len(votes), len(agents.AGENTS))
        self.assertEqual(Vote.objects.filter(claim=claim).count(), len(agents.AGENTS))
        claim.refresh_from_db()
        self.assertEqual((claim.status, claim.leading, claim.confidence), ("UNDER_REVIEW", "FALSE", 1.0))
        self.assertEqual(ResearchRun.all_runs.filter(claim=claim, status="done").count(), len(agents.AGENTS))


@override_settings(GROQ_API_KEY="test-key", RATELIMIT_ENABLE=False, AI_AGENTS_PER_CLAIM=3, AI_AGENT_WORKERS=1,
                   AI_AGENT_RETRIES=0)
class AIBriefAndMyChecksTests(TestCase):
    def setUp(self):
        stub = {name: (lambda q, l: [dict(s) for s in SOURCES]) for name in connectors.CONNECTORS}
        for p in (mock.patch.dict(connectors.CONNECTORS, stub),
                  mock.patch("research.pipeline.fetch_article", return_value=("", ""))):
            p.start()
            self.addCleanup(p.stop)
        self.claim = Claim.objects.create(text="URGENT: VTU has cancelled all 5th semester exams",
                                          domain="education", status="PENDING")

    def test_verifier_sees_ai_brief_and_can_start_from_the_suggestion(self):
        from research.brief import ai_brief
        with mock.patch.object(ai, "assess", return_value=REFUTES):
            agents.run_agents(self.claim)
        brief = ai_brief(self.claim)
        self.assertEqual((brief["suggested"], brief["share"], brief["basis"]), ("FALSE", 100, "agents"))
        self.assertEqual(sorted(brief["links"]), sorted(s["url"] for s in SOURCES))
        self.assertIn("AI verifiers: 3 of 3 said False", brief["draft"])
        ravi = verifier("ravi")
        self.client.force_login(ravi)
        page = self.client.get(reverse("verify_review", args=[self.claim.pk]))
        self.assertContains(page, "AI BRIEF")
        self.assertContains(page, "Use AI suggestion")
        self.assertContains(page, "100% of the AI verifiers' weight")
        self.assertContains(page, "Each AI verifier's verdict")
        self.assertFalse(Vote.objects.filter(verifier=ravi).exists())       # nothing is voted for them

    def test_suggestion_falls_back_to_ai_research_without_agent_votes(self):
        from research.brief import ai_brief
        run = ResearchRun.objects.create(claim=self.claim, status="done", label="LIKELY_TRUE", truth_score=90)
        run.sources.create(url="https://pib.gov.in/x", title="t", connector="x", tier="official",
                           tier_weight=1.0, stance="supports")
        brief = ai_brief(self.claim)
        self.assertEqual((brief["suggested"], brief["basis"], brief["links"]),
                         ("TRUE", "research", ["https://pib.gov.in/x"]))

    def test_my_checks_lists_every_claim_the_user_checked(self):
        from chat.models import ChatThread
        asha = User.objects.create_user("asha", password=PW, phone="+919845011112")
        other = User.objects.create_user("bob", password=PW, phone="+919845011113")
        final = Claim.objects.create(text="Old rumour about dengue cures", status="FINAL", verdict="FALSE")
        for c in (self.claim, final):
            ChatThread.objects.create(owner=asha, title=c.text[:80], claim=c)
        ChatThread.objects.create(owner=other, title="not mine", claim=Claim.objects.create(text="Bob's claim here"))
        self.client.force_login(asha)
        page = self.client.get(reverse("my_checks"))
        self.assertContains(page, "VTU has cancelled")
        self.assertContains(page, "Old rumour about dengue")
        self.assertNotContains(page, "Bob&#x27;s claim")
        self.assertEqual(page.context["stats"], {"total": 2, "open": 1, "verified": 1, "false": 1})
        listed = lambda **f: [t.claim.text for t in self.client.get(reverse("my_checks"), f).context["threads"]]
        self.assertEqual(listed(state="false"), ["Old rumour about dengue cures"])
        self.assertEqual(listed(state="open"), [self.claim.text])
        self.assertEqual(listed(q="dengue"), ["Old rumour about dengue cures"])
