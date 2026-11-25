from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from accounts.decorators import verifier_required
from chat.models import ChatThread
from claims.models import Claim
from django.core.exceptions import PermissionDenied

from .models import Source, SourceFeedback
from .pipeline import start_research


@require_POST
@verifier_required
@ratelimit(key="user", rate="200/h", method="POST", block=True)
def source_feedback(request, pk):
    """A verifier marks an AI source useful / wrong. Returns the updated buttons (HTMX)."""
    source = get_object_or_404(Source, pk=pk)
    useful = request.POST.get("useful") == "1"
    SourceFeedback.objects.update_or_create(source=source, verifier=request.user, defaults={"useful": useful})
    return render(request, "research/_feedback.html", {"s": source, "mine": useful})


@require_POST
@ratelimit(key="user", rate="5/h", method="POST", block=True)
def recheck(request, pk):
    """Re-run AI research with the latest sources (submitter, verifiers and admins)."""
    claim = get_object_or_404(Claim, pk=pk, is_hidden=False)
    user = request.user
    allowed = (user.can_vote or user.is_admin_role
               or ChatThread.objects.filter(owner=user, claim=claim).exists())
    if not allowed:
        raise PermissionDenied("Only people who checked this claim, or verifiers, can re-check it.")
    latest = claim.research_runs.first()
    if not (latest and latest.is_running):
        start_research(claim)
    back = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        back = "/"
    return redirect(back)
