from django import forms

from accounts.models import DOMAINS, LANGUAGES

from .models import Claim, Verdict
from .validators import sanitize_text, validate_safe_url

INPUT = "w-full rounded bg-zinc-900 border border-zinc-700 p-2 text-zinc-100"


class ClaimForm(forms.ModelForm):
    class Meta:
        model = Claim
        fields = ["text", "source_url", "language", "domain", "district"]
        widgets = {"text": forms.Textarea(attrs={"rows": 5})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs["class"] = INPUT

    def clean_text(self):
        text = sanitize_text(self.cleaned_data["text"])
        if len(text) < 10:
            raise forms.ValidationError("Please paste the full message (at least 10 characters).")
        return text

    def clean_source_url(self):
        url = self.cleaned_data.get("source_url", "")
        return validate_safe_url(url) if url else ""

    def clean_district(self):
        return sanitize_text(self.cleaned_data.get("district", ""))


class VoteForm(forms.Form):
    verdict = forms.ChoiceField(choices=Verdict.choices)
    note = forms.CharField(max_length=500, widget=forms.Textarea(attrs={"rows": 3}),
                           help_text="Required: explain your verdict.",
                           error_messages={"required": "Please explain your verdict."})
    evidence_1 = forms.CharField(required=False, max_length=500)
    evidence_2 = forms.CharField(required=False, max_length=500)
    evidence_3 = forms.CharField(required=False, max_length=500)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs["class"] = INPUT

    def clean_note(self):
        note = sanitize_text(self.cleaned_data.get("note", ""))
        if not note:
            raise forms.ValidationError("Please explain your verdict.")
        return note

    def clean(self):
        data = super().clean()
        urls, errors = [], {}
        for name in ("evidence_1", "evidence_2", "evidence_3"):
            raw = (data.get(name) or "").strip()
            if not raw:
                continue
            try:
                urls.append(validate_safe_url(raw))
            except forms.ValidationError as exc:
                errors[name] = exc.messages
        for name, msgs in errors.items():
            self.add_error(name, msgs)
        if data.get("verdict") != "UNVERIFIED" and not urls and not errors:
            self.add_error("evidence_1", "At least one evidence link is required for this verdict.")
        data["evidence_urls"] = urls
        return data


class CommunityVoteForm(forms.Form):
    verdict = forms.ChoiceField(choices=[("TRUE", "True"), ("FALSE", "False"),
                                         ("MISLEADING", "Misleading"), ("UNVERIFIED", "Not sure")])
    reason = forms.CharField(max_length=200, required=False)

    def clean_reason(self):
        return sanitize_text(self.cleaned_data.get("reason", ""))


class OverrideForm(forms.Form):
    verdict = forms.ChoiceField(choices=Verdict.choices)
    reason = forms.CharField(max_length=250)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs["class"] = INPUT

    def clean_reason(self):
        reason = sanitize_text(self.cleaned_data["reason"])
        if not reason:
            raise forms.ValidationError("A reason is required.")
        return reason


FILTER_LANG = [("", "All languages")] + LANGUAGES
FILTER_DOMAIN = [("", "All domains")] + DOMAINS
