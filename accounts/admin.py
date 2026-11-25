from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone

from verification.assignment import assign_open_claims_to

from .models import User, VerifierProfile


def _after_approval(request, users):
    """Give newly approved verifiers the open claims about their own organisation."""
    n = sum(len(assign_open_claims_to(u)) for u in users)
    if n:
        from django.contrib import messages
        messages.info(request, f"Assigned {n} open claim(s) about their organisation(s).")


class VerifierProfileInline(admin.StackedInline):
    model = VerifierProfile
    fk_name = "user"
    can_delete = False
    extra = 0
    verbose_name = "Verifier profile (check background manually)"
    fields = ("status", "languages", "profession", "organisation", "work_email", "work_email_verified",
              "org_domain", "linkedin_url", "approved_by", "approved_at")
    readonly_fields = ("approved_by", "approved_at")


@admin.register(User)
class VerdictoUserAdmin(UserAdmin):
    list_display = ("display_name", "phone", "role", "verifier_status", "approved_by_name", "profession",
                    "organisation", "domains", "district", "work_email_ok")
    list_display_links = ("display_name",)
    list_filter = ("role", "verifier_profile__status", "verifier_profile__work_email_verified")
    search_fields = ("username", "first_name", "last_name", "phone", "verifier_profile__profession",
                     "verifier_profile__organisation")
    fieldsets = UserAdmin.fieldsets + (
        ("Verdicto", {"fields": ("role", "phone", "preferred_language", "domains", "district",
                                 "community_correct", "community_total")}),
    )
    readonly_fields = ("community_correct", "community_total")
    inlines = [VerifierProfileInline]
    actions = ["approve", "reject"]

    def get_queryset(self, request):
        # Pending verifiers first, then everyone else by name.
        return (super().get_queryset(request).select_related("verifier_profile__approved_by")
                .annotate(pending_first=Case(When(verifier_profile__status="pending", then=Value(0)),
                                             default=Value(1), output_field=IntegerField()))
                .order_by("pending_first", "role", "first_name"))

    def save_formset(self, request, form, formset, change):
        """Record who approved a verifier when the status is changed on the edit page."""
        approved = []
        for f in formset.forms:
            if isinstance(f.instance, VerifierProfile) and "status" in f.changed_data \
                    and f.instance.status == VerifierProfile.Status.APPROVED:
                f.instance.approved_by, f.instance.approved_at = request.user, timezone.now()
                approved.append(f.instance.user)
        super().save_formset(request, form, formset, change)
        _after_approval(request, approved)

    @admin.display(description="Name", ordering="first_name")
    def display_name(self, obj):
        return obj.display_name

    @admin.display(description="Verifier status", ordering="verifier_profile__status")
    def verifier_status(self, obj):
        profile = obj._profile()
        return profile.get_status_display() if profile else "—"

    @admin.display(description="Approved by")
    def approved_by_name(self, obj):
        return obj.approved_by.display_name if obj.approved_by else "—"

    @admin.display(description="Profession")
    def profession(self, obj):
        return obj.profession or "—"

    @admin.display(description="Organisation")
    def organisation(self, obj):
        return obj.organisation or "—"

    @admin.display(description="Work email", boolean=True)
    def work_email_ok(self, obj):
        return obj.work_email_verified

    @admin.action(description="Approve selected verifiers")
    def approve(self, request, queryset):
        profiles = VerifierProfile.objects.filter(user__in=queryset).exclude(status="approved")
        users = list(User.objects.filter(pk__in=profiles.values("user_id")))
        n = profiles.update(status="approved", approved_by=request.user, approved_at=timezone.now())
        self.message_user(request, f"Approved {n} verifier(s).")
        _after_approval(request, users)

    @admin.action(description="Reject: turn back into regular users")
    def reject(self, request, queryset):
        profiles = VerifierProfile.objects.filter(user__in=queryset, status="pending")
        users = list(profiles.values_list("user_id", flat=True))
        profiles.update(status="rejected", approved_by=None, approved_at=None)
        User.objects.filter(pk__in=users).update(role=User.Role.CITIZEN)
        self.message_user(request, f"Turned {len(users)} pending verifier(s) into regular users.")


@admin.register(VerifierProfile)
class VerifierProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "status", "is_ai_agent", "profession", "organisation", "work_email_verified",
                    "approved_by", "approved_at")
    list_filter = ("status", "is_ai_agent", "work_email_verified")
    search_fields = ("user__first_name", "user__phone", "profession", "organisation")
    readonly_fields = ("approved_by", "approved_at", "created_at")
    actions = ["approve", "reject"]

    def get_queryset(self, request):
        return (super().get_queryset(request).select_related("user", "approved_by")
                .annotate(pending_first=Case(When(status="pending", then=Value(0)),
                                             default=Value(1), output_field=IntegerField()))
                .order_by("pending_first", "user__first_name"))

    def save_model(self, request, obj, form, change):
        approved = "status" in form.changed_data and obj.status == VerifierProfile.Status.APPROVED
        if approved:
            obj.approved_by, obj.approved_at = request.user, timezone.now()
        super().save_model(request, obj, form, change)
        if approved:
            _after_approval(request, [obj.user])

    @admin.action(description="Approve selected verifiers")
    def approve(self, request, queryset):
        pending = queryset.exclude(status="approved")
        users = list(User.objects.filter(pk__in=pending.values("user_id")))
        n = pending.update(status="approved", approved_by=request.user, approved_at=timezone.now())
        self.message_user(request, f"Approved {n} verifier(s).")
        _after_approval(request, users)

    @admin.action(description="Reject: turn back into regular users")
    def reject(self, request, queryset):
        pending = queryset.filter(status="pending")
        users = list(pending.values_list("user_id", flat=True))
        pending.update(status="rejected", approved_by=None, approved_at=None)
        User.objects.filter(pk__in=users).update(role=User.Role.CITIZEN)
        self.message_user(request, f"Turned {len(users)} pending verifier(s) into regular users.")
