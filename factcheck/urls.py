from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from django_ratelimit.decorators import ratelimit

from accounts import views as account_views
from claims import views as claim_views
from verification import views as verification_views

handler400 = "factcheck.errors.bad_request"
handler403 = "factcheck.errors.permission_denied"
handler404 = "factcheck.errors.not_found"
handler500 = "factcheck.errors.server_error"

login_view = ratelimit(key="ip", rate="20/h", method="POST", block=True)(auth_views.LoginView.as_view())

urlpatterns = [
    path("", claim_views.feed, name="feed"),
    path("claims/new/", claim_views.submit, name="submit"),
    path("claims/similar/", claim_views.similar, name="similar"),
    path("claims/<int:pk>/", claim_views.detail, name="claim_detail"),
    path("claims/<int:pk>/same/", claim_views.same_claim, name="same_claim"),
    path("claims/<int:pk>/vote/", verification_views.vote, name="vote"),
    path("claims/<int:pk>/override/", verification_views.override, name="override"),
    path("c/<str:slug>/", claim_views.share, name="share"),
    path("api/claims/<int:pk>/", verification_views.api_claim, name="api_claim"),
    path("api/claims/<int:pk>/votes/", verification_views.api_vote, name="api_vote"),
    path("accounts/login/", login_view, name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/signup/", account_views.signup, name="signup"),
    path("admin/", admin.site.urls),
]
