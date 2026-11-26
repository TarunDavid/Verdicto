from django.conf import settings

from claims.forms import CommunityVoteForm, OverrideForm, VoteForm

from .services import breakdown, community_summary


def vote_context(request, claim, vote_form=None, override_form=None):
    user = request.user
    rows, arithmetic = breakdown(claim)
    has_voted = user.is_authenticated and claim.votes.filter(verifier=user).exists()
    return {
        "rows": rows,
        "human_rows": [r for r in rows if not r["vote"].verifier.is_ai_agent],
        "agent_rows": [r for r in rows if r["vote"].verifier.is_ai_agent],
        "arithmetic": arithmetic,
        "logs": claim.logs.select_related("changed_by"),
        "can_vote": user.is_authenticated and user.can_vote and not has_voted,
        "has_voted": has_voted,
        "vote_form": vote_form or VoteForm(),
        "is_admin": user.is_authenticated and user.is_admin_role,
        "override_form": override_form or OverrideForm(),
        "run": claim.research_runs.first(),
        "community": community_summary(claim),
        "can_opine": user.is_authenticated and user.can_give_opinion and claim.status != "FINAL",
        "my_opinion": (claim.community_votes.filter(user=user).first() if user.is_authenticated else None),
        "community_form": CommunityVoteForm(),
        "full": user.is_authenticated and (user.can_vote or user.is_admin_role),
        "assignments": claim.assignments.select_related("verifier__verifier_profile")
                                        .filter(verifier__verifier_profile__is_ai_agent=False),
        "min_human": settings.FINAL_MIN_HUMAN_VOTES,
        "agent_cap_pct": round(settings.AI_AGENT_WEIGHT_CAP * 100),
    }
