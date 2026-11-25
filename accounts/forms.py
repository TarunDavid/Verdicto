from urllib.parse import urlparse

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm

from claims.validators import sanitize_text, validate_safe_url

from .models import DOMAINS, LANGUAGES, User
from .phone import normalize_phone

FIELD = ("w-full rounded-xl bg-white/5 border border-white/10 px-3 py-2.5 text-zinc-100 "
         "placeholder-zinc-500 focus:outline-none focus:border-teal-400/60")


class LoginForm(AuthenticationForm):
    username = forms.CharField(label="Phone number", max_length=20,
                               widget=forms.TextInput(attrs={"autofocus": True, "class": FIELD,
                                                             "placeholder": "98450 12345",
                                                             "inputmode": "tel"}))
    password = forms.CharField(label="Password", strip=False,
                               widget=forms.PasswordInput(attrs={"class": FIELD}))
    error_messages = {**AuthenticationForm.error_messages,
                      "invalid_login": "Wrong phone number or password."}


class SignupForm(forms.Form):
    role = forms.ChoiceField(choices=[("citizen", "Regular user"), ("verifier", "Verifier")],
                             widget=forms.HiddenInput)
    name = forms.CharField(max_length=80)
    phone = forms.CharField(max_length=20, help_text="Indian mobile number. Never shown publicly.")
    interests = forms.MultipleChoiceField(choices=DOMAINS, widget=forms.CheckboxSelectMultiple,
                                          label="Fields of interest")
    preferred_language = forms.ChoiceField(choices=LANGUAGES, initial="en")
    password = forms.CharField(widget=forms.PasswordInput, strip=False)
    # Verifier-only details
    profession = forms.CharField(max_length=80, required=False)
    organisation = forms.CharField(max_length=120, required=False)
    work_email = forms.EmailField(required=False)
    linkedin_url = forms.CharField(max_length=300, required=False, label="LinkedIn profile URL")
    languages = forms.MultipleChoiceField(choices=LANGUAGES, required=False,
                                          widget=forms.CheckboxSelectMultiple,
                                          label="Languages you can verify in")
    district = forms.CharField(max_length=60, required=False)
    consent = forms.BooleanField(label="I understand my name and verifier stats are public; "
                                       "my phone number is never shown.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, f in self.fields.items():
            if not isinstance(f.widget, (forms.CheckboxSelectMultiple, forms.CheckboxInput,
                                         forms.HiddenInput)):
                f.widget.attrs["class"] = FIELD

    @property
    def is_verifier(self):
        return (self.data.get("role") or self.initial.get("role")) == "verifier"

    def clean_name(self):
        name = sanitize_text(self.cleaned_data["name"])
        if len(name) < 2:
            raise forms.ValidationError("Please enter your name.")
        return name

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data["phone"])
        if not phone:
            raise forms.ValidationError("Enter a valid 10-digit Indian mobile number.")
        if User.objects.filter(phone=phone).exists():
            raise forms.ValidationError("An account with this phone number already exists.")
        return phone

    def clean_linkedin_url(self):
        url = (self.cleaned_data.get("linkedin_url") or "").strip()
        if not url:
            return ""
        url = validate_safe_url(url)
        host = (urlparse(url).hostname or "").lower()
        if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
            raise forms.ValidationError("Enter a linkedin.com profile URL.")
        return url

    def clean(self):
        data = super().clean()
        for key in ("profession", "organisation", "district"):
            data[key] = sanitize_text(data.get(key, ""))
        if data.get("role") == "verifier":
            if not data.get("profession"):
                self.add_error("profession", "Verifiers must tell us their profession.")
            if not data.get("languages"):
                self.add_error("languages", "Pick at least one language.")
        if data.get("password"):
            try:
                password_validation.validate_password(data["password"])
            except forms.ValidationError as exc:
                self.add_error("password", exc)
        return data

    def save(self):
        d = self.cleaned_data
        verifier = d["role"] == "verifier"
        return User.objects.create_user(
            username="u" + d["phone"].lstrip("+"),
            password=d["password"],
            first_name=d["name"],
            phone=d["phone"],
            role=User.Role.VERIFIER if verifier else User.Role.CITIZEN,
            domains=d["interests"],
            preferred_language=d["preferred_language"],
            languages=d["languages"] if verifier else [d["preferred_language"]],
            profession=d["profession"] if verifier else "",
            organisation=d["organisation"] if verifier else "",
            work_email=d["work_email"] if verifier else "",
            linkedin_url=d["linkedin_url"] if verifier else "",
            district=d["district"] if verifier else "",
            verifier_approved=False,  # admins approve verifiers
        )


class VerifierApplicationForm(forms.Form):
    """A regular user applying to become a verifier (reviewed by an admin)."""
    profession = forms.CharField(max_length=80)
    organisation = forms.CharField(max_length=120, required=False)
    interests = forms.MultipleChoiceField(choices=DOMAINS, widget=forms.CheckboxSelectMultiple,
                                          label="Fields you can verify")
    languages = forms.MultipleChoiceField(choices=LANGUAGES, widget=forms.CheckboxSelectMultiple,
                                          label="Languages you can verify in")
    work_email = forms.EmailField(required=False)
    linkedin_url = forms.CharField(max_length=300, required=False, label="LinkedIn profile URL")
    district = forms.CharField(max_length=60, required=False)

    clean_linkedin_url = SignupForm.clean_linkedin_url

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            if not isinstance(f.widget, forms.CheckboxSelectMultiple):
                f.widget.attrs["class"] = FIELD

    def apply(self, user):
        d = self.cleaned_data
        user.role = User.Role.VERIFIER
        user.verifier_approved = False
        user.profession = sanitize_text(d["profession"])
        user.organisation = sanitize_text(d["organisation"])
        user.domains = d["interests"]
        user.languages = d["languages"]
        user.work_email = d["work_email"]
        user.linkedin_url = d["linkedin_url"]
        user.district = sanitize_text(d["district"])
        user.save()
