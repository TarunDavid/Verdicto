from django.contrib import admin

from .models import ResearchRun, Source


class SourceInline(admin.TabularInline):
    model = Source
    extra = 0
    fields = ("publisher", "tier", "stance", "url", "quote")
    readonly_fields = fields


@admin.register(ResearchRun)
class ResearchRunAdmin(admin.ModelAdmin):
    list_display = ("claim", "status", "label", "truth_score", "confidence", "created_at")
    list_filter = ("status", "label")
    inlines = [SourceInline]
