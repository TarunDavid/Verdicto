from django.contrib import admin

from .models import Claim


@admin.register(Claim)
class ClaimAdmin(admin.ModelAdmin):
    list_display = ("id", "text", "language", "domain", "status", "verdict", "confidence", "submission_count")
    list_filter = ("status", "verdict", "language", "domain")
    readonly_fields = ("status", "verdict", "confidence", "leading")
