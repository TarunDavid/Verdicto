from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from claims.duplicates import find_similar, normalize
from claims.models import Claim
from claims.validators import sanitize_text, validate_safe_url
from research import ai
from research.fetch import fetch_article
from research.pipeline import start_research
from verification.services import log_submission

from .models import ChatMessage, ChatThread

DUPLICATE_SCORE = 90


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


@require_POST
@ratelimit(key="user", rate="30/h", method="POST", block=True)
def send(request, pk=None):
    text = sanitize_text(request.POST.get("text", ""))[:3000]
    if not text:
        return redirect(request.META.get("HTTP_REFERER") or "chat")
    if pk:
        thread = _get_thread(request, pk)
        ChatMessage.objects.create(thread=thread, sender="user", content=text)
        _followup(thread, text)
        thread.save()  # bump updated_at
        return redirect("chat_thread", pk=thread.pk)
    return _new_claim(request, text)


def _new_claim(request, text):
    language = request.POST.get("language") if request.POST.get("language") in ("en", "kn") else "en"
    source_url, claim_text = "", text
    if text.split()[0].lower().startswith(("http:", "https:", "javascript:", "data:")) and len(text.split()) == 1:
        try:
            source_url = validate_safe_url(text)
        except ValidationError:
            return redirect("/chat/?error=Only+http+and+https+links+are+allowed.")
        try:
            title, body = fetch_article(source_url)
            claim_text = sanitize_text(f"{title}. {body[:600]}") if (title or body) else source_url
        except Exception:
            claim_text = source_url
    if len(claim_text) < 10:
        return redirect("/chat/?error=Please+paste+the+full+message+(at+least+10+characters).")

    owner = request.user
    thread = ChatThread.objects.create(owner=owner, title=claim_text[:80])
    ChatMessage.objects.create(thread=thread, sender="user", content=text)

    matches = find_similar(claim_text, source_url)
    best = next(((s, c) for s, c in matches if s >= DUPLICATE_SCORE and not c.is_hidden), None)
    if best:
        claim = best[1]
        Claim.objects.filter(pk=claim.pk).update(submission_count=F("submission_count") + 1)
        reply = ("This looks like a claim we've already received. Here is its current status. "
                 "Your report has been added to it.")
    else:
        claim = Claim.objects.create(text=claim_text, normalized_text=normalize(claim_text),
                                     source_url=source_url, language=language,
                                     status=Claim.Status.RESEARCHING, submitted_by=owner)
        log_submission(claim, owner)
        start_research(claim, source_filter=request.POST.get("sources", "all"),
                       deep=request.POST.get("deep") == "on")
        reply = ("Thanks. I'm researching this now using live news, fact-checkers and research "
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
            answer = "I'm still researching. Ask again in a few seconds."
        elif claim and claim.status == Claim.Status.FINAL:
            answer = f"Verifiers have published the final verdict: {claim.get_verdict_display()}."
        else:
            answer = ("I can't answer follow-up questions right now (AI unavailable). The sources "
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
