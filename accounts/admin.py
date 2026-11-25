from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class VerdictoUserAdmin(UserAdmin):
    list_display = ("username", "role", "verifier_approved", "work_email_verified", "district")
    list_filter = ("role", "verifier_approved", "work_email_verified")
    fieldsets = UserAdmin.fieldsets + (
        ("Verdicto", {"fields": ("role", "preferred_language", "languages", "domains", "district",
                                 "verifier_approved", "work_email_verified", "org_domain")}),
    )
    actions = ["approve"]

    @admin.action(description="Approve selected verifiers")
    def approve(self, request, queryset):
        queryset.filter(role=User.Role.VERIFIER).update(verifier_approved=True)
