"""Data every app-shell page needs: sidebar threads and the verify-queue count."""


def shell(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    from chat.models import ChatThread
    from verification.models import Assignment

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
