from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit

from django_ratelimit.core import is_ratelimited

from verification import engine

from django.conf import settings

from . import phone_otp, work_email
from .forms import LoginForm, SignupForm
from .models import User


@method_decorator(ratelimit(key="ip", rate="20/h", method="POST", block=True), name="post")
class PhoneLoginView(LoginView):
    form_class = LoginForm
    redirect_authenticated_user = True


@login_not_required
@ratelimit(key="ip", rate="10/h", method="POST", block=True)
def signup(request):
    if request.user.is_authenticated:
        return redirect("chat")
    role = request.POST.get("role") or request.GET.get("role")
    if role not in ("citizen", "verifier"):
        return render(request, "registration/signup_role.html")
    form = SignupForm(request.POST or None, initial={"role": role})
    if request.method == "POST" and form.is_valid():
        if settings.PHONE_OTP_ENABLED:
            try:
                phone_otp.start(request, form.pending_data())
                return redirect("signup_verify")
            except phone_otp.SMSError as exc:
                request.session.pop(phone_otp.SESSION_KEY, None)
                form.add_error(None, str(exc))
                return render(request, "registration/signup.html", {"form": form, "role": role})
        user = form.save()
        login(request, user, backend="accounts.backends.PhoneBackend")
        return redirect("chat")
    return render(request, "registration/signup.html", {"form": form, "role": role})


@login_not_required
@ratelimit(key="ip", rate="20/h", method="POST", block=True)
def signup_verify(request):
    """Second sign-up step when PHONE_OTP_ENABLED: enter the 6-digit SMS code."""
    from .forms import OTPForm, create_user_from
    pending = request.session.get(phone_otp.SESSION_KEY)
    if not pending:
        return redirect("signup")
    form, error = OTPForm(request.POST or None), ""
    if request.method == "POST" and form.is_valid():
        data, error = phone_otp.check(request, form.cleaned_data["code"])
        if data:
            try:
                user = create_user_from(data)
            except Exception as exc:  # e.g. the number was registered meanwhile
                error = str(getattr(exc, "message", exc))
            else:
                login(request, user, backend="accounts.backends.PhoneBackend")
                return redirect("chat")
        if not request.session.get(phone_otp.SESSION_KEY):
            return render(request, "registration/signup_verify.html", {"form": form, "error": error,
                                                                        "expired": True})
    return render(request, "registration/signup_verify.html", {
        "form": form, "error": error, "phone": pending["data"]["phone"][-4:]})


def become_verifier(request):
    from .forms import VerifierApplicationForm
    if request.user.role != request.user.Role.CITIZEN:
        return redirect("chat")
    form = VerifierApplicationForm(request.POST or None,
                                   initial={"interests": request.user.domains,
                                            "languages": request.user.languages})
    if request.method == "POST" and form.is_valid():
        form.apply(request.user)
        return redirect("chat")
    return render(request, "registration/become_verifier.html", {"form": form})


def _verifier_stats(user):
    rows = []
    for s in user.verifier_stats.order_by("domain", "language"):
        rows.append({"domain": s.get_domain_display(), "language": s.get_language_display(),
                     "correct": s.correct, "total": s.total,
                     "accuracy": round(100 * s.correct / s.total) if s.total else None,
                     "weight": engine.beta_weight(s.correct, s.total, user.work_email_verified)})
    return rows


def profile(request):
    """Your own profile: details, interests, track record, and (for verifiers) work-email check."""
    from .forms import OTPForm, ProfileForm, WorkEmailForm
    user = request.user
    form = ProfileForm(request.POST if request.POST.get("action") == "profile" else None, initial={
        "name": user.display_name, "interests": user.domains,
        "preferred_language": user.preferred_language, "district": user.district})
    email_form, otp_form, notice, error = WorkEmailForm(), OTPForm(), "", ""
    action = request.POST.get("action")
    if action == "profile" and form.is_valid():
        form.save(user)
        notice = "Profile saved."
    elif action in ("send_code", "verify_code") and user.role == user.Role.VERIFIER:
        key = f"otp:{user.pk}"
        if is_ratelimited(request, group=key, key="user", rate="5/h", method="POST", increment=True):
            error = "Too many attempts. Try again later."
        elif action == "send_code":
            email_form = WorkEmailForm(request.POST)
            if email_form.is_valid():
                try:
                    sent_to = work_email.send_code(user, email_form.cleaned_data["email"])
                    notice = f"We sent a 6-digit code to {sent_to}. It expires in 10 minutes."
                except work_email.WorkEmailError as exc:
                    error = str(exc)
        else:
            otp_form = OTPForm(request.POST)
            if otp_form.is_valid():
                try:
                    work_email.verify_code(user, otp_form.cleaned_data["code"])
                    notice = f"Work email verified · {user.org_domain}"
                except work_email.WorkEmailError as exc:
                    error = str(exc)
    pending_otp = user.email_otps.filter(used=False).first()
    return render(request, "accounts/profile.html", {
        "form": form, "email_form": email_form, "otp_form": otp_form, "notice": notice, "error": error,
        "pending_otp": pending_otp, "stats": _verifier_stats(user), "tab": "profile",
        "checks": user.chat_threads.count(),
    })


@login_not_required
def verifier_profile(request, pk):
    """Public verifier profile: badges, accuracy per field/language, recent verdicts."""
    from django.shortcuts import get_object_or_404
    verifier = get_object_or_404(User, pk=pk, role=User.Role.VERIFIER, verifier_profile__status="approved",
                                 is_active=True)
    votes = (verifier.votes.select_related("claim").filter(claim__is_hidden=False)
             .order_by("-created_at")[:20])
    return render(request, "accounts/verifier_profile.html", {
        "verifier": verifier, "stats": _verifier_stats(verifier), "votes": votes,
        "scored": verifier.votes.exclude(scored_as=None).count(),
        "correct": sum(1 for v in verifier.votes.exclude(scored_as=None) if v.verdict == v.scored_as),
    })


def my_votes(request):
    user = request.user
    return render(request, "accounts/my_votes.html", {
        "community": user.community_votes.select_related("claim").order_by("-updated_at"),
        "verifier_votes": user.votes.select_related("claim").order_by("-created_at"),
        "tab": "votes",
    })
