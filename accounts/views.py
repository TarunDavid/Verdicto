from django.contrib.auth import login
from django.shortcuts import redirect, render
from django_ratelimit.decorators import ratelimit

from .forms import SignupForm


@ratelimit(key="ip", rate="10/h", method="POST", block=True)
def signup(request):
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()  # role defaults to citizen
        login(request, user)
        return redirect("feed")
    return render(request, "registration/signup.html", {"form": form})
