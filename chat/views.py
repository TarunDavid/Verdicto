from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from claims.duplicates import find_same_claim, normalize
from claims.models import Claim
from claims.uploads import clean_screenshot
from claims.validators import sanitize_text, validate_safe_url
from research import ai
from research.fetch import fetch_article
from research.pipeline import start_research
from verification.services import log_submission

from .models import ChatMessage, ChatThread



def _threads_for(request):
    return ChatThread.objects.filter(owner=request.user)


def _get_thread(request, pk):
    thread = _threads_for(request).filter(pk=pk).first()
    if thread is None:
        raise Http404
    return thread


def chat_home(request, pk=None):
    thread = _get_thread(request, pk) if pk else None
    return render(request, "chat/chat.html", {
        "thread": thread,
        "messages_list": thread.messages.select_related("card_claim") if thread else [],
        "error": request.GET.get("error", ""),
        "prefill": request.GET.get("prefill", "")[:20],
        "tab": "check",
    })


def my_checks(request):
    """Every claim this user has checked, newest first, with where each one stands."""
    from research.models import ResearchRun
    from django.db.models import Prefetch

    threads = (_threads_for(request).filter(claim__isnull=False)
               .select_related("claim")
               .prefetch_related(Prefetch("claim__research_runs",
                                          queryset=ResearchRun.objects.order_by("-created_at"), to_attr="runs"))
               .order_by("-created_at"))
    q = request.GET.get("q", "").strip()
    state = request.GET.get("state", "")
    if q:
        threads = threads.filter(Q(title__icontains=q) | Q(claim__text__icontains=q))
    if state == "open":
        threads = threads.exclude(claim__status=Claim.Status.FINAL)
    elif state == "verified":
        threads = threads.filter(claim__status=Claim.Status.FINAL)
    elif state == "false":
        threads = threads.filter(claim__status=Claim.Status.FINAL, claim__verdict="FALSE")
    mine = _threads_for(request).filter(claim__isnull=False)
    stats = {
        "total": mine.count(),
        "open": mine.exclude(claim__status=Claim.Status.FINAL).count(),
        "verified": mine.filter(claim__status=Claim.Status.FINAL).count(),
        "false": mine.filter(claim__status=Claim.Status.FINAL, claim__verdict="FALSE").count(),
    }
    return render(request, "chat/my_checks.html", {"threads": threads[:300], "stats": stats, "q": q,
                                                   "state": state, "tab": "checks"})


@require_POST
@ratelimit(key="user", rate="30/h", method="POST", block=True)
def send(request, pk=None):
    text = sanitize_text(request.POST.get("text", ""))[:3000]
    if not pk and request.FILES.get("screenshot"):
        return _new_claim(request, text)
    if not text:
        return redirect(request.META.get("HTTP_REFERER") or "chat")
    if pk:
        thread = _get_thread(request, pk)
        ChatMessage.objects.create(thread=thread, sender="user", content=text)
        _followup(thread, text)
        thread.save()  # bump updated_at
        return redirect("chat_thread", pk=thread.pk)
    return _new_claim(request, text)


def _error(message):
    return redirect("/?" + urlencode({"error": message}))


def _new_claim(request, text):
    language = request.POST.get("language") if request.POST.get("language") in ("en", "kn") else "en"
    source_url, claim_text = "", text
    screenshot = None
    if request.FILES.get("screenshot"):
        try:
            screenshot, jpeg = clean_screenshot(request.FILES["screenshot"])
        except ValidationError as exc:
            return _error(exc.messages[0])
        ocr_text, ocr_lang = ai.extract_text_from_image(jpeg)
        if not text:
            claim_text = sanitize_text(ocr_text)
            language = ocr_lang or language
        if not claim_text:
            return _error(_("We couldn't read text in that image. Please paste the message as text too."))
    elif text.split()[0].lower().startswith(("http:", "https:", "javascript:", "data:")) and len(text.split()) == 1:
        try:
            source_url = validate_safe_url(text)
        except ValidationError:
            return _error(_("Only http and https links are allowed."))
        try:
            title, body = fetch_article(source_url)
            claim_text = sanitize_text(f"{title}. {body[:600]}") if (title or body) else source_url
        except Exception:
            claim_text = source_url
    if len(claim_text) < 10:
        return _error(_("Please paste the full message (at least 10 characters)."))

    owner = request.user
    thread = ChatThread.objects.create(owner=owner, title=claim_text[:80])
    ChatMessage.objects.create(thread=thread, sender="user",
                               content=text or _("📷 Screenshot: “%(text)s”") % {"text": claim_text[:500]})

    canonical = ai.canonical_english(claim_text)
    best = find_same_claim(claim_text, canonical, source_url)
    if best:
        claim = best[1]
        Claim.objects.filter(pk=claim.pk).update(submission_count=F("submission_count") + 1)
        reply = _("This looks like a claim we've already received. Here is its current status. "
                  "Your report has been added to it. You can ask me to re-check it with the latest sources.")
    else:
        claim = Claim(text=claim_text, normalized_text=normalize(claim_text), source_url=source_url,
                      canonical_en=canonical, language=language, status=Claim.Status.RESEARCHING,
                      submitted_by=owner)
        if screenshot:
            claim.screenshot.save("upload.jpg", screenshot, save=False)
        claim.save()
        log_submission(claim, owner)
        start_research(claim, source_filter=request.POST.get("sources", "all"),
                       deep=request.POST.get("deep") == "on")
        reply = _("Thanks. I'm researching this now using live news, fact-checkers and research "
                  "databases. My answer will be provisional: verified human fact-checkers give the "
                  "final verdict.")
    thread.claim = claim
    thread.save(update_fields=["claim"])
    ChatMessage.objects.create(thread=thread, sender="bot", content=reply, card_claim=claim)
    return redirect("chat_thread", pk=thread.pk)


def _followup(thread, question):
    claim = thread.claim
    run = claim.research_runs.first() if claim else None
    answer = None
    if run and not run.is_running:
        answer = ai.answer_followup(claim, run, question, list(thread.messages.all()))
    if answer is None:
        if run and run.is_running:
            answer = _("I'm still researching. Ask again in a few seconds.")
        elif claim and claim.status == Claim.Status.FINAL:
            answer = _("Verifiers have published the final verdict: %(v)s.") % {"v": claim.get_verdict_display()}
        else:
            answer = _("I can't answer follow-up questions right now (AI unavailable). The sources "
                       "found are listed on the claim card, and verifiers are reviewing it.")
    ChatMessage.objects.create(thread=thread, sender="bot", content=answer, card_claim=None)


def card(request, claim_pk):
    """HTMX partial: the live research card. Polls while research runs, then slowly until final."""
    claim = get_object_or_404(Claim, pk=claim_pk)
    if claim.is_hidden and not request.user.is_admin_role:
        # The submitter still sees the moderation notice through their own thread.
        if not _threads_for(request).filter(claim=claim).exists():
            raise Http404
    return render(request, "research/_card.html", {"claim": claim, "run": claim.research_runs.first(),
                                                   "live": True,
                                                   "full": request.user.can_vote or request.user.is_admin_role})
