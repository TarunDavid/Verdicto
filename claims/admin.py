from django.contrib import admin

from .models import Claim


@admin.register(Claim)
class ClaimAdmin(admin.ModelAdmin):
    list_display = ("id", "text", "language", "domain", "status", "verdict", "confidence", "submission_count")
    list_filter = ("status", "verdict", "language", "domain", "is_hidden")
    readonly_fields = ("status", "verdict", "confidence", "leading")
    actions = ["hide", "unhide"]

    @admin.action(description="Hide from public (moderation)")
    def hide(self, request, queryset):
        queryset.update(is_hidden=True, moderation_note=f"Hidden by {request.user.username}")

    @admin.action(description="Unhide")
    def unhide(self, request, queryset):
        queryset.update(is_hidden=False)
