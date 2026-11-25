from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class VerdictoUserAdmin(UserAdmin):
    list_display = ("display_name", "phone", "role", "verifier_approved", "profession",
                    "organisation", "domains", "district", "work_email_verified")
    list_display_links = ("display_name",)
    list_filter = ("role", "verifier_approved", "work_email_verified")
    search_fields = ("username", "first_name", "last_name", "phone", "profession", "organisation")
    ordering = ("verifier_approved", "role", "first_name")  # pending verifiers float to the top
    fieldsets = UserAdmin.fieldsets + (
        ("Verdicto", {"fields": ("role", "phone", "preferred_language", "languages", "domains",
                                 "district", "verifier_approved")}),
        ("Verifier background (check manually)", {"fields": (
            "profession", "organisation", "work_email", "work_email_verified", "org_domain",
            "linkedin_url")}),
    )
    actions = ["approve", "reject"]

    @admin.display(description="Name", ordering="first_name")
    def display_name(self, obj):
        return obj.display_name

    @admin.action(description="Approve selected verifiers")
    def approve(self, request, queryset):
        n = queryset.filter(role=User.Role.VERIFIER).update(verifier_approved=True)
        self.message_user(request, f"Approved {n} verifier(s).")

    @admin.action(description="Reject: turn back into regular users")
    def reject(self, request, queryset):
        n = queryset.filter(role=User.Role.VERIFIER, verifier_approved=False).update(
            role=User.Role.CITIZEN)
        self.message_user(request, f"Turned {n} pending verifier(s) into regular users.")
