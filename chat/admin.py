from django.contrib import admin

from .models import ChatMessage, ChatThread


class MessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    readonly_fields = ("sender", "content", "card_claim", "created_at")


@admin.register(ChatThread)
class ChatThreadAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "claim", "updated_at")
    inlines = [MessageInline]
