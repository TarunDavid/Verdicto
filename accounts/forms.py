from urllib.parse import urlparse

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _

from claims.validators import sanitize_text, validate_safe_url

from .models import DOMAINS, LANGUAGES, User
from .phone import normalize_phone

FIELD = "field placeholder-zinc-600"   # see .field in static/css/input.css


class LoginForm(AuthenticationForm):
    username = forms.CharField(label=_("Phone number"), max_length=20,
                               widget=forms.TextInput(attrs={"autofocus": True, "class": FIELD,
                                                             "placeholder": "98450 12345",
                                                             "inputmode": "tel"}))
    password = forms.CharField(label=_("Password"), strip=False,
                               widget=forms.PasswordInput(attrs={"class": FIELD}))
    error_messages = {**AuthenticationForm.error_messages,
                      "invalid_login": "Wrong phone number or password."}


class SignupForm(forms.Form):
    role = forms.ChoiceField(choices=[("citizen", "Regular user"), ("verifier", "Verifier")],
                             widget=forms.HiddenInput)
    name = forms.CharField(max_length=80, label=_("Name"))
    phone = forms.CharField(max_length=20, label=_("Phone"), help_text=_("Indian mobile number. Never shown publicly."))
    interests = forms.MultipleChoiceField(choices=DOMAINS, widget=forms.CheckboxSelectMultiple,
                                          label=_("Fields of interest"))
    preferred_language = forms.ChoiceField(choices=LANGUAGES, initial="en", label=_("Preferred language"))
    password = forms.CharField(widget=forms.PasswordInput, strip=False, label=_("Password"))
    # Verifier-only details
    profession = forms.CharField(max_length=80, required=False, label=_("Profession"))
    organisation = forms.CharField(max_length=120, required=False, label=_("Organisation"))
    work_email = forms.EmailField(required=False, label=_("Work email"))
    linkedin_url = forms.CharField(max_length=300, required=False, label=_("LinkedIn profile URL"))
    languages = forms.MultipleChoiceField(choices=LANGUAGES, required=False,
                                          widget=forms.CheckboxSelectMultiple,
                                          label=_("Languages you can verify in"))
    district = forms.CharField(max_length=60, required=False, label=_("District"))
    consent = forms.BooleanField(label=_("I understand my name and verifier stats are public; "
                                         "my phone number is never shown."))

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

    def pending_data(self):
        """What we keep in the session while the phone OTP is pending (password already hashed)."""
        from django.contrib.auth.hashers import make_password
        d = {k: v for k, v in self.cleaned_data.items() if k not in ("password", "consent")}
        d["password_hash"] = make_password(self.cleaned_data["password"])
        return d

    def save(self):
        return create_user_from(self.cleaned_data)


def create_user_from(d):
    """Create the account from sign-up data (plain `password` or pre-hashed `password_hash`)."""
    if User.objects.filter(phone=d["phone"]).exists():
        raise forms.ValidationError("An account with this phone number already exists.")
    verifier = d["role"] == "verifier"
    user = User.objects.create_user(
            username="u" + d["phone"].lstrip("+"),
            password=d.get("password"),
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
    if d.get("password_hash"):
        user.password = d["password_hash"]
        user.save(update_fields=["password"])
    return user


class VerifierApplicationForm(forms.Form):
    """A regular user applying to become a verifier (reviewed by an admin)."""
    profession = forms.CharField(max_length=80, label=_("Profession"))
    organisation = forms.CharField(max_length=120, required=False, label=_("Organisation"))
    interests = forms.MultipleChoiceField(choices=DOMAINS, widget=forms.CheckboxSelectMultiple,
                                          label=_("Fields you can verify"))
    languages = forms.MultipleChoiceField(choices=LANGUAGES, widget=forms.CheckboxSelectMultiple,
                                          label=_("Languages you can verify in"))
    work_email = forms.EmailField(required=False, label=_("Work email"))
    linkedin_url = forms.CharField(max_length=300, required=False, label=_("LinkedIn profile URL"))
    district = forms.CharField(max_length=60, required=False, label=_("District"))

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


class ProfileForm(forms.Form):
    name = forms.CharField(max_length=80, label=_("Name"))
    interests = forms.MultipleChoiceField(choices=DOMAINS, widget=forms.CheckboxSelectMultiple,
                                          label=_("Fields of interest"))
    preferred_language = forms.ChoiceField(choices=LANGUAGES, label=_("Preferred language"))
    district = forms.CharField(max_length=60, required=False, label=_("District"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            if not isinstance(f.widget, forms.CheckboxSelectMultiple):
                f.widget.attrs["class"] = FIELD

    def save(self, user):
        d = self.cleaned_data
        user.first_name, user.last_name = sanitize_text(d["name"]), ""
        user.domains = d["interests"]
        user.preferred_language = d["preferred_language"]
        user.district = sanitize_text(d["district"])
        user.save()


class WorkEmailForm(forms.Form):
    email = forms.EmailField(label=_("Work email"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs.update({"class": FIELD, "placeholder": "you@yourorg.in"})


class OTPForm(forms.Form):
    code = forms.RegexField(regex=r"^\d{6}$", max_length=6, label=_("6-digit code"),
                            error_messages={"invalid": "Enter the 6-digit code."})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].widget.attrs.update({"class": FIELD, "inputmode": "numeric",
                                                  "autocomplete": "one-time-code"})
