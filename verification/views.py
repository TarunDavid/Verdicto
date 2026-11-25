from django.contrib.auth.decorators import login_not_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from accounts.decorators import admin_required, verifier_required
from claims.forms import OverrideForm, VoteForm
from claims.models import Claim
from claims.views import share_context
from factcheck.errors import json_error

from .forms_helpers import vote_context
from .services import AlreadyVoted, admin_override, cast_vote


@require_POST
@verifier_required
@ratelimit(key="user", rate="60/h", method="POST", block=True)
def vote(request, pk):
    claim = get_object_or_404(Claim, pk=pk)
    form = VoteForm(request.POST)
    if form.is_valid():
        try:
            cast_vote(claim, request.user, form.cleaned_data["verdict"], form.cleaned_data["note"],
                      form.cleaned_data["evidence_urls"])
            return redirect("claim_detail", pk=pk)
        except AlreadyVoted as exc:
            form.add_error(None, str(exc))
    claim.refresh_from_db()
    ctx = {"claim": claim, **vote_context(request, claim, vote_form=form), **share_context(request, claim)}
    return render(request, "claims/detail.html", ctx, status=400)


@require_POST
@verifier_required
@ratelimit(key="user", rate="60/h", method="POST", block=True)
def api_vote(request, pk):
    """JSON API for voting. Errors use {"error": {"code", "message", "fields"}}."""
    claim = Claim.objects.filter(pk=pk).first()
    if claim is None:
        return json_error("not_found", "Claim not found.", 404)
    form = VoteForm(request.POST)
    if not form.is_valid():
        return json_error("validation_error", "Please fix the highlighted fields.", 400,
                          {k: [str(m) for m in v] for k, v in form.errors.items()})
    try:
        cast_vote(claim, request.user, form.cleaned_data["verdict"], form.cleaned_data["note"],
                  form.cleaned_data["evidence_urls"])
    except AlreadyVoted as exc:
        return json_error("already_voted", str(exc), 409)
    claim.refresh_from_db()
    return JsonResponse({"claim": claim.pk, "status": claim.status, "verdict": claim.verdict,
                         "leading": claim.leading, "confidence": round(claim.confidence, 4)}, status=201)


@require_POST
@admin_required
def override(request, pk):
    claim = get_object_or_404(Claim, pk=pk)
    form = OverrideForm(request.POST)
    if form.is_valid():
        admin_override(claim, request.user, form.cleaned_data["verdict"], form.cleaned_data["reason"])
        return redirect("claim_detail", pk=pk)
    ctx = {"claim": claim, **vote_context(request, claim, override_form=form), **share_context(request, claim)}
    return render(request, "claims/detail.html", ctx, status=400)


@login_not_required
def api_claim(request, pk):
    claim = Claim.objects.filter(pk=pk).first()
    if claim is None:
        return json_error("not_found", "Claim not found.", 404)
    return JsonResponse({"id": claim.pk, "status": claim.status, "verdict": claim.verdict,
                         "leading": claim.leading, "confidence": round(claim.confidence, 4),
                         "votes": claim.votes.count()})


@verifier_required
def dashboard(request):
    """Verifier queue: assigned claims first (by importance), then other open claims in their domains."""
    from claims.ranking import importance
    from .models import Assignment

    voted = set(request.user.votes.values_list("claim_id", flat=True))
    assigned = [a for a in Assignment.objects.filter(verifier=request.user, status=Assignment.Status.ASSIGNED)
                .select_related("claim") if a.claim_id not in voted and not a.claim.is_hidden]
    assigned.sort(key=lambda a: -importance(a.claim))
    from datetime import timedelta
    from django.conf import settings as dj_settings
    from django.utils import timezone
    for a in assigned:
        a.due = a.created_at + timedelta(hours=dj_settings.ASSIGNMENT_TIMEOUT_HOURS)
        a.overdue = a.due <= timezone.now()
    assigned_ids = {a.claim_id for a in assigned}
    others = [c for c in Claim.objects.filter(status__in=["PENDING", "UNDER_REVIEW"], is_hidden=False,
                                              duplicate_of__isnull=True,
                                              domain__in=request.user.domains or [])
              .exclude(pk__in=voted | assigned_ids)[:20]]
    return render(request, "verification/dashboard.html", {"assigned": assigned, "others": others,
                                                           "tab": "verify"})


@verifier_required
def review(request, pk):
    """Two-pane review: claim + community signal + vote form | full AI research panel."""
    claim = get_object_or_404(Claim, pk=pk, is_hidden=False)
    form = VoteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            cast_vote(claim, request.user, form.cleaned_data["verdict"], form.cleaned_data["note"],
                      form.cleaned_data["evidence_urls"])
            return redirect("verify_dashboard")
        except AlreadyVoted as exc:
            form.add_error(None, str(exc))
    ctx = {"claim": claim, **vote_context(request, claim, vote_form=form), "tab": "verify"}
    return render(request, "verification/review.html", ctx, status=400 if form.errors else 200)
