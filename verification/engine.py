"""Weighted verdict engine (SPEC section 6). Pure functions, no database access."""
from dataclasses import dataclass

OPTIONS = ("FALSE", "TRUE", "MISLEADING", "PARTIALLY_TRUE", "UNVERIFIED")  # order breaks ties
PRIOR_A = 2
PRIOR_B = 2
VERIFIED_PRIOR_A = 3   # trust bonus for a verified work email
MIN_VOTES = 3            # verifier votes needed before a verdict can be final
FINAL_CONFIDENCE = 0.70
COMMUNITY_WEIGHT = 0.05  # each regular user's vote
COMMUNITY_CAP = 0.20     # all community votes together: at most 20% of the total weight


def beta_weight(correct, total, work_email_verified=False):
    """weight = (correct + a) / (total + a + b). New verifier = 0.5, or 0.6 with a verified work email."""
    a = VERIFIED_PRIOR_A if work_email_verified else PRIOR_A
    return (correct + a) / (total + a + PRIOR_B)


def lookup_record(stats, domain, language):
    """stats: iterable of (domain, language, correct, total).

    1. exact (domain, language) stat
    2. otherwise the sum across languages for that domain
    3. otherwise the verifier's record across all fields (a strong track record carries over)
    4. otherwise (0, 0) -> the prior
    """
    stats = list(stats)
    for d, lang, correct, total in stats:
        if d == domain and lang == language:
            return correct, total
    same_domain = [(c, t) for d, _lang, c, t in stats if d == domain]
    if same_domain:
        return sum(c for c, _ in same_domain), sum(t for _, t in same_domain)
    return sum(s[2] for s in stats), sum(s[3] for s in stats)


def verifier_weight(stats, domain, language, work_email_verified=False):
    correct, total = lookup_record(stats, domain, language)
    return beta_weight(correct, total, work_email_verified)


def community_vote_weight(n_community, verifier_total):
    """Weight of ONE community vote: 0.05, scaled down so that the community's combined weight
    is at most COMMUNITY_CAP of the total (c / (v + c) <= 0.2  <=>  c <= 0.25 v)."""
    if n_community == 0:
        return 0.0
    raw = COMMUNITY_WEIGHT * n_community
    if verifier_total <= 0:
        return COMMUNITY_WEIGHT  # no experts yet: shows a leaning only (cannot be final)
    cap = verifier_total * COMMUNITY_CAP / (1 - COMMUNITY_CAP)
    return min(raw, cap) / n_community


def agent_vote_scale(agent_total, human_total, cap):
    """Factor applied to every AI agent's weight so that all agents together are at most `cap` of
    the verifier weight: a / (h + a) <= cap  <=>  a <= h * cap / (1 - cap). With no human votes yet
    there is nothing to compare with, so agents count in full (a claim can't be final without a
    person anyway unless FINAL_MIN_HUMAN_VOTES is 0)."""
    if agent_total <= 0 or human_total <= 0 or cap >= 1:
        return 1.0
    return min(1.0, human_total * cap / (1 - cap) / agent_total)


@dataclass
class VerdictResult:
    totals: dict
    leading: str | None
    confidence: float
    status: str          # "FINAL" or "UNDER_REVIEW"
    verdict: str | None  # set only when FINAL
    vote_count: int      # verifier votes
    community_count: int = 0
    community_vote_weight: float = 0.0
    agent_count: int = 0
    agent_scale: float = 1.0


def compute_verdict(votes, community=(), human_votes=None, min_human=0, agent_votes=(), agent_cap=1.0):
    """votes: iterable of (verdict, weight) from human verifiers. agent_votes: the same from AI agent
    verifiers, scaled so that together they are at most `agent_cap` of the verifier weight.
    community: iterable of verdicts from regular users. Final needs >= 3 verifier votes (people and
    agents) and >= 70% of the total weighted votes, with at least `min_human` votes from people."""
    votes = list(votes)
    agent_votes = list(agent_votes)
    community = list(community)
    totals = {opt: 0.0 for opt in OPTIONS}
    for verdict, weight in votes:
        totals[verdict] += weight
    scale = agent_vote_scale(sum(w for _, w in agent_votes), sum(totals.values()), agent_cap)
    for verdict, weight in agent_votes:
        totals[verdict] += weight * scale
    cw = community_vote_weight(len(community), sum(totals.values()))
    for verdict in community:
        totals[verdict] += cw
    total_weight = sum(totals.values())
    if total_weight == 0:
        return VerdictResult(totals, None, 0.0, "UNDER_REVIEW", None, 0)
    n_votes = len(votes) + len(agent_votes)

    leading = max(OPTIONS, key=lambda o: (totals[o], -OPTIONS.index(o)))
    confidence = totals[leading] / total_weight
    humans = len(votes) if human_votes is None else human_votes
    is_final = n_votes >= MIN_VOTES and confidence >= FINAL_CONFIDENCE and humans >= min_human
    return VerdictResult(
        totals=totals,
        leading=leading,
        confidence=confidence,
        status="FINAL" if is_final else "UNDER_REVIEW",
        verdict=leading if is_final else None,
        vote_count=n_votes,
        community_count=len(community),
        community_vote_weight=cw,
        agent_count=len(agent_votes),
        agent_scale=scale,
    )


def explain(votes, community=(), agent_votes=(), agent_cap=1.0):
    """Human-readable arithmetic, e.g. 'FALSE = 0.83 + 0.67 = 1.50; TRUE = 0.50; confidence = 75%'.
    AI agents' weights are shown after scaling."""
    community = list(community)
    agent_votes = list(agent_votes)
    result = compute_verdict(list(votes), community, agent_votes=agent_votes, agent_cap=agent_cap)
    votes = list(votes) + [(v, w * result.agent_scale) for v, w in agent_votes]
    parts = []
    for opt in OPTIONS:
        weights = [w for v, w in votes if v == opt]
        if not weights:
            continue
        if len(weights) == 1:
            parts.append(f"{opt} = {weights[0]:.2f}")
        else:
            parts.append(f"{opt} = {' + '.join(f'{w:.2f}' for w in weights)} = {sum(weights):.2f}")
    if community:
        cw = result.community_vote_weight
        tallies = ", ".join(f"{opt} {community.count(opt)}" for opt in OPTIONS if opt in community)
        parts.append(f"community = {len(community)} votes × {cw:.3f} = {cw * len(community):.2f}"
                     f" ({tallies})")
    if agent_votes:
        parts.append(f"AI agents: {len(agent_votes)} votes" + (
            f", weights × {result.agent_scale:.2f} (all agents together ≤ {agent_cap:.0%})"
            if result.agent_scale < 1 else ""))
    parts.append(f"confidence = {result.confidence:.0%}")
    return "; ".join(parts)


def score_delta(vote_verdict, previous_final, new_final):
    """Track-record change for one vote, with no double counting.

    Returns (correct_delta, total_delta): undo the old scoring (if any), then apply the new one.
    """
    correct_delta = total_delta = 0
    if previous_final is not None:
        total_delta -= 1
        correct_delta -= int(vote_verdict == previous_final)
    if new_final is not None:
        total_delta += 1
        correct_delta += int(vote_verdict == new_final)
    return correct_delta, total_delta
