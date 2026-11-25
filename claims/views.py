from urllib.parse import quote

from django.conf import settings
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from accounts.decorators import login_required
from verification.forms_helpers import vote_context
from verification.services import log_submission

from .duplicates import find_similar, normalize
from .forms import FILTER_DOMAIN, FILTER_LANG, ClaimForm
from .models import Claim


def feed(request):
    qs = Claim.objects.filter(duplicate_of__isnull=True)
    lang, domain, status, q = (request.GET.get(k, "") for k in ("language", "domain", "status", "q"))
    if lang:
        qs = qs.filter(language=lang)
    if domain:
        qs = qs.filter(domain=domain)
    if status in ("TRUE", "FALSE", "UNVERIFIED"):
        qs = qs.filter(status=Claim.Status.FINAL, verdict=status)
    elif status == "UNDER_REVIEW":
        qs = qs.filter(status=Claim.Status.UNDER_REVIEW)
    if q:
        qs = qs.filter(Q(text__icontains=q) | Q(district__icontains=q))
    trending = Claim.objects.filter(duplicate_of__isnull=True).order_by("-submission_count", "-created_at")[:5]
    return render(request, "claims/feed.html", {
        "claims": qs[:50], "trending": trending, "langs": FILTER_LANG, "domains": FILTER_DOMAIN,
        "f": {"language": lang, "domain": domain, "status": status, "q": q},
    })


@login_required
@ratelimit(key="user", rate="10/h", method="POST", block=True)
def submit(request):
    form = ClaimForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        claim = form.save(commit=False)
        claim.normalized_text = normalize(claim.text)
        claim.submitted_by = request.user
        claim.save()
        log_submission(claim, request.user)
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


def detail(request, pk):
    claim = get_object_or_404(Claim, pk=pk)
    if claim.duplicate_of_id:
        return redirect("claim_detail", pk=claim.duplicate_of_id)
    return render(request, "claims/detail.html", {"claim": claim, **vote_context(request, claim),
                                                  **share_context(request, claim)})


def share_context(request, claim):
    share_url = settings.SITE_URL.rstrip("/") + reverse("share", args=[claim.share_slug])
    label = claim.display_verdict or "UNDER REVIEW"
    title = f"{label}: {claim.text[:90]}"
    message = f"Fact-check — {title}\nConfidence {claim.confidence_pct}%. Details:"
    return {"share_url": share_url, "og_title": title,
            "whatsapp_url": "https://wa.me/?text=" + quote(f"{message} {share_url}")}


def share(request, slug):
    claim = get_object_or_404(Claim, share_slug=slug)
    return render(request, "claims/share.html", {"claim": claim, **share_context(request, claim)})
