from claims.forms import OverrideForm, VoteForm

from .services import breakdown


def vote_context(request, claim, vote_form=None, override_form=None):
    user = request.user
    rows, arithmetic = breakdown(claim)
    has_voted = user.is_authenticated and claim.votes.filter(verifier=user).exists()
    return {
        "rows": rows,
        "arithmetic": arithmetic,
        "logs": claim.logs.select_related("changed_by"),
        "can_vote": user.is_authenticated and user.can_vote and not has_voted,
        "has_voted": has_voted,
        "vote_form": vote_form or VoteForm(),
        "is_admin": user.is_authenticated and user.is_admin_role,
        "override_form": override_form or OverrideForm(),
    }
