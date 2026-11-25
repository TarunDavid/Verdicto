from django.conf import settings
from django.db import models

from claims.models import Claim


class ChatThread(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True,
                              blank=True, related_name="chat_threads")
    session_key = models.CharField(max_length=40, blank=True, db_index=True)  # guests
    title = models.CharField(max_length=120)
    claim = models.ForeignKey(Claim, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="threads")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]


class ChatMessage(models.Model):
    class Sender(models.TextChoices):
        USER = "user", "User"
        BOT = "bot", "Verdicto"

    thread = models.ForeignKey(ChatThread, on_delete=models.CASCADE, related_name="messages")
    sender = models.CharField(max_length=4, choices=Sender.choices)
    content = models.TextField(max_length=4000)
    # A bot message that shows a claim's live research card.
    card_claim = models.ForeignKey(Claim, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
