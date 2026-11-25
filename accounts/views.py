from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit

from .forms import LoginForm, SignupForm


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
        user = form.save()
        login(request, user, backend="accounts.backends.PhoneBackend")
        return redirect("chat")
    return render(request, "registration/signup.html", {"form": form, "role": role})


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
