from urllib.parse import quote

from django.conf import settings
from django.db.models import F, Q
from django.http import Http404
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from accounts.decorators import login_required
from verification.forms_helpers import vote_context
from research.pipeline import start_research
from verification.services import log_submission

from .duplicates import find_similar, normalize
from .forms import FILTER_DOMAIN, FILTER_LANG, ClaimForm
from .models import Claim
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
    claims = list(qs.order_by("-created_at")[:200])
    if sort != "latest":
        now = timezone.now()
        claims.sort(key=lambda c: -importance(c, now))
    return render(request, "claims/feed.html", {
        "claims": claims[:50], "langs": FILTER_LANG, "domains": FILTER_DOMAIN,
        "f": {"language": lang, "domain": domain, "status": status, "q": q, "sort": sort},
    })


def archive(request):
    """Previous verdicts: finalised claims only, newest verdict first, searchable."""
    qs = visible_claims().filter(status=Claim.Status.FINAL).order_by("-finalized_at")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(text__icontains=q) | Q(normalized_text__icontains=normalize(q)))
    return render(request, "claims/archive.html", {"claims": qs[:100], "q": q})


@login_required
@ratelimit(key="user", rate="10/h", method="POST", block=True)
def submit(request):
    form = ClaimForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        claim = form.save(commit=False)
        claim.normalized_text = normalize(claim.text)
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


def detail(request, pk):
    claim = _get_visible(request, pk=pk)
    if claim.duplicate_of_id:
        return redirect("claim_detail", pk=claim.duplicate_of_id)
    return render(request, "claims/detail.html", {"claim": claim, **vote_context(request, claim),
                                                  **share_context(request, claim)})


def share_context(request, claim):
    share_url = settings.SITE_URL.rstrip("/") + reverse("share", args=[claim.share_slug])
    label = claim.get_verdict_display().upper() if claim.display_verdict else "PENDING VERIFICATION"
    title = f"{label}: {claim.text[:90]}"
    message = f"Fact-check — {title}\nConfidence {claim.confidence_pct}%. Details:"
    return {"share_url": share_url, "og_title": title,
            "whatsapp_url": "https://wa.me/?text=" + quote(f"{message} {share_url}")}


def share(request, slug):
    claim = _get_visible(request, share_slug=slug)
    return render(request, "claims/share.html", {"claim": claim, **share_context(request, claim)})
