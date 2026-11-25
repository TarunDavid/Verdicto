"""Copy verifier details from the user table into VerifierProfile (one per verifier)."""
from django.db import migrations

FIELDS = ["languages", "profession", "organisation", "work_email", "work_email_verified", "org_domain",
          "linkedin_url", "approved_by", "approved_at"]


def forwards(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    VerifierProfile = apps.get_model("accounts", "VerifierProfile")
    for user in User.objects.filter(role="verifier"):
        VerifierProfile.objects.update_or_create(user=user, defaults={
            "status": "approved" if user.verifier_approved else "pending",
            **{f: getattr(user, f) for f in FIELDS}})


def backwards(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    VerifierProfile = apps.get_model("accounts", "VerifierProfile")
    for p in VerifierProfile.objects.select_related("user"):
        u = p.user
        u.verifier_approved = p.status == "approved"
        for f in FIELDS:
            setattr(u, f, getattr(p, f))
        u.save()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0005_verifierprofile")]
    operations = [migrations.RunPython(forwards, backwards)]
