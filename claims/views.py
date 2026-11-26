from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db.models import F, Prefetch, Q
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from django.contrib.auth.decorators import login_not_required

from accounts.decorators import login_required
from verification.forms_helpers import vote_context
from research.pipeline import start_research
from verification.services import log_submission

from .duplicates import find_similar, normalize
from research import ai
from research.models import ResearchRun
from verification.services import ClaimClosed, cast_community_vote

from .forms import FILTER_DOMAIN, FILTER_LANG, ClaimForm, CommunityVoteForm

STATUS_OPTIONS = [("FALSE", _("False")), ("TRUE", _("True")), ("PARTIALLY_TRUE", _("Partially true")),
                  ("MISLEADING", _("Misleading")), ("UNVERIFIED", _("Unverified")),
                  ("PENDING", _("Pending verification")), ("UNDER_REVIEW", _("Under investigation"))]
LATEST_RUN = Prefetch("research_runs", queryset=ResearchRun.objects.order_by("-created_at"),
                      to_attr="runs")
from .models import Claim
from .og_image import render as render_og
from .ranking import importance


def visible_claims():
    return Claim.objects.filter(duplicate_of__isnull=True, is_hidden=False)


def feed(request):
    qs = visible_claims()
    lang, domain, status, q = (request.GET.get(k, "") for k in ("language", "domain", "status", "q"))
    if lang:
        qs = qs.filter(language=lang)
    if domain:
        qs = qs.filter(domain=domain)
    if status in ("TRUE", "FALSE", "UNVERIFIED", "MISLEADING", "PARTIALLY_TRUE"):
        qs = qs.filter(status=Claim.Status.FINAL, verdict=status)
    elif status in ("PENDING", "UNDER_REVIEW", "RESEARCHING"):
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(text__icontains=q) | Q(district__icontains=q))
    sort = request.GET.get("sort", "important")
    claims = list(qs.order_by("-created_at").prefetch_related(LATEST_RUN)[:200])
    if sort != "latest":
        now = timezone.now()
        claims.sort(key=lambda c: -importance(c, now))
    return render(request, "claims/feed.html", {
        "claims": claims[:50], "langs": FILTER_LANG, "domains": FILTER_DOMAIN,
        "f": {"language": lang, "domain": domain, "status": status, "q": q, "sort": sort},
        "tab": "news", "subtab": "latest" if sort == "latest" else "important",
        "status_options": STATUS_OPTIONS,
    })


def archive(request):
    """Previous verdicts: finalised claims only, newest verdict first, searchable."""
    qs = visible_claims().filter(status=Claim.Status.FINAL).order_by("-finalized_at")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(text__icontains=q) | Q(normalized_text__icontains=normalize(q)))
    return render(request, "claims/archive.html", {"claims": qs[:100], "q": q, "tab": "news",
                                                   "subtab": "past"})


@require_POST
@ratelimit(key="user", rate="60/h", method="POST", block=True)
def community_vote(request, pk):
    """A regular user's own verdict (low weight). Approved verifiers use the verifier vote instead."""
    claim = _get_visible(request, pk=pk)
    back = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        back = reverse("claim_detail", args=[pk])
    if not request.user.can_give_opinion:
        raise PermissionDenied("Approved verifiers vote from the Verify Queue.")
    form = CommunityVoteForm(request.POST)
    if form.is_valid():
        try:
            cast_community_vote(claim, request.user, form.cleaned_data["verdict"],
                                form.cleaned_data["reason"])
        except ClaimClosed:
            pass
    return redirect(back)


@login_required
@ratelimit(key="user", rate="10/h", method="POST", block=True)
def submit(request):
    form = ClaimForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        claim = form.save(commit=False)
        claim.normalized_text = normalize(claim.text)
        claim.canonical_en = ai.canonical_english(claim.text)
        claim.submitted_by = request.user
        claim.status = Claim.Status.RESEARCHING
        claim.save()
        log_submission(claim, request.user)
        start_research(claim)
        return redirect("claim_detail", pk=claim.pk)
    return render(request, "claims/submit.html", {"form": form})


def similar(request):
    """HTMX endpoint: 'Similar claims already checked' while the user types."""
    text = request.GET.get("text", "")[:2000]
    source_url = request.GET.get("source_url", "").strip()
    matches = find_similar(text, source_url) if (len(text) >= 8 or source_url) else []
    return render(request, "claims/_similar.html", {"matches": matches})


@login_required
@require_POST
@ratelimit(key="user", rate="30/h", method="POST", block=True)
def same_claim(request, pk):
    """User says 'this is my claim': count the report instead of creating a duplicate."""
    Claim.objects.filter(pk=pk).update(submission_count=F("submission_count") + 1)
    return redirect("claim_detail", pk=pk)


def _get_visible(request, **lookup):
    claim = get_object_or_404(Claim, **lookup)
    if claim.is_hidden and not (request.user.is_authenticated and request.user.is_admin_role):
        raise Http404
    return claim


@login_not_required
def detail(request, pk):
    claim = _get_visible(request, pk=pk)
    if claim.duplicate_of_id:
        return redirect("claim_detail", pk=claim.duplicate_of_id)
    return render(request, "claims/detail.html", {"claim": claim, **vote_context(request, claim),
                                                  **share_context(request, claim)})


def site_url(request):
    """SITE_URL, or the address the visitor actually used when they came through a tunnel, so
    WhatsApp share links work for them too."""
    from factcheck.middleware import is_remote
    if request is not None and is_remote(request):
        return request.build_absolute_uri("/").rstrip("/")
    return settings.SITE_URL.rstrip("/")


def share_context(request, claim):
    base = site_url(request)
    share_url = base + reverse("share", args=[claim.share_slug])
    label = claim.get_verdict_display().upper() if claim.display_verdict else "PENDING VERIFICATION"
    title = f"{label}: {claim.text[:90]}"
    message = f"Fact-check — {title}\nConfidence {claim.confidence_pct}%. Details:"
    return {"share_url": share_url, "og_title": title,
            "og_image": base + reverse("og_image", args=[claim.share_slug]),
            "whatsapp_url": "https://wa.me/?text=" + quote(f"{message} {share_url}")}


@login_not_required
def og_image(request, slug):
    """Preview image for WhatsApp / social cards. Cached until the claim's status changes."""
    claim = _get_visible(request, share_slug=slug)
    key = f"og:{claim.pk}:{claim.status}:{claim.verdict}:{claim.confidence_pct}"
    png = cache.get(key)
    if png is None:
        label = claim.get_verdict_display().upper() if claim.display_verdict else "PENDING VERIFICATION"
        if claim.language == "kn":
            run = claim.research_runs.filter(status="done").first()
            snippet = f"Kannada forward about {claim.get_domain_display().lower()}" + (
                f" in {claim.district}" if claim.district else "")
            if run and run.checkable_claim and run.checkable_claim.isascii():
                snippet = run.checkable_claim
        else:
            snippet = claim.text
        png = render_og(claim, label, snippet[:220])
        cache.set(key, png, 3600)
    return HttpResponse(png, content_type="image/png")


@login_not_required
def share(request, slug):
    claim = _get_visible(request, share_slug=slug)
    verifiers = claim.votes.select_related("verifier__verifier_profile")
    return render(request, "claims/share.html", {"claim": claim, "verifiers": verifiers,
                                                 **share_context(request, claim)})
