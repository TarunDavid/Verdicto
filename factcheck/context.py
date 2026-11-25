"""Data every app-shell page needs: sidebar threads and the verify-queue count."""


def shell(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    from django.core.cache import cache

    from chat.models import ChatThread
    from verification.assignment import reassign_stale
    from verification.models import Assignment

    # Hand overdue assignments to the next verifier at most every 10 minutes (cron-free).
    if cache.add("reassign_stale:lock", 1, 600):
        try:
            reassign_stale()
        except Exception:  # never break a page over housekeeping
            import logging
            logging.getLogger("verdicto").exception("reassign_stale failed")

    threads = ChatThread.objects.filter(owner=user).select_related("claim")
    q = request.GET.get("chats", "").strip()
    if q:
        threads = threads.filter(title__icontains=q)
    queue = 0
    if user.can_vote:
        voted = user.votes.values_list("claim_id", flat=True)
        queue = (Assignment.objects.filter(verifier=user, status=Assignment.Status.ASSIGNED,
                                           claim__is_hidden=False).exclude(claim_id__in=voted).count())
    return {"sidebar_threads": threads[:25], "chat_search": q, "queue_count": queue}


def assets(request):
    from django.conf import settings
    return {"USE_CDN_ASSETS": settings.USE_CDN_ASSETS}
