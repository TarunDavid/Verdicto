from django.contrib import admin

from .models import Assignment, CommunityVote, Evidence, VerdictLog, VerifierStat, Vote


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VerdictLog)
class VerdictLogAdmin(ReadOnlyAdmin):
    list_display = ("claim", "old_status", "new_status", "old_verdict", "new_verdict", "reason", "created_at")


@admin.register(Vote)
class VoteAdmin(ReadOnlyAdmin):
    list_display = ("claim", "verifier", "verdict", "weight_at_vote", "scored_as")


admin.site.register(Evidence, ReadOnlyAdmin)
admin.site.register(VerifierStat)


@admin.register(CommunityVote)
class CommunityVoteAdmin(ReadOnlyAdmin):
    list_display = ("claim", "user", "verdict", "reason", "updated_at")
    list_filter = ("verdict",)


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ("claim", "verifier", "score", "status", "reason", "created_at")
    list_filter = ("status",)
