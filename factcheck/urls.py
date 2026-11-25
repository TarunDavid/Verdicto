from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path, re_path
from django.views.i18n import set_language
from django.views.static import serve

from accounts import views as account_views
from claims import views as claim_views
from research import views as research_views
from verification import views as verification_views

handler400 = "factcheck.errors.bad_request"
handler403 = "factcheck.errors.permission_denied"
handler404 = "factcheck.errors.not_found"
handler500 = "factcheck.errors.server_error"


urlpatterns = [
    path("", include("chat.urls")),                       # Check a Claim (chat) is home
    path("news/", claim_views.feed, name="feed"),           # News & Verdicts
    path("news/past/", claim_views.archive, name="archive"),
    path("verify/", verification_views.dashboard, name="verify_dashboard"),
    path("verify/<int:pk>/", verification_views.review, name="verify_review"),
    path("claims/<int:pk>/opinion/", claim_views.community_vote, name="community_vote"),
    path("claims/<int:pk>/recheck/", research_views.recheck, name="recheck"),
    path("sources/<int:pk>/feedback/", research_views.source_feedback, name="source_feedback"),
    path("claims/new/", claim_views.submit, name="submit"),
    path("claims/similar/", claim_views.similar, name="similar"),
    path("claims/<int:pk>/", claim_views.detail, name="claim_detail"),
    path("claims/<int:pk>/same/", claim_views.same_claim, name="same_claim"),
    path("claims/<int:pk>/vote/", verification_views.vote, name="vote"),
    path("claims/<int:pk>/override/", verification_views.override, name="override"),
    path("c/<str:slug>/", claim_views.share, name="share"),
    path("c/<str:slug>/og.png", claim_views.og_image, name="og_image"),
    path("api/claims/<int:pk>/", verification_views.api_claim, name="api_claim"),
    path("api/claims/<int:pk>/votes/", verification_views.api_vote, name="api_vote"),
    path("accounts/login/", account_views.PhoneLoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/signup/", account_views.signup, name="signup"),
    path("accounts/signup/verify/", account_views.signup_verify, name="signup_verify"),
    path("accounts/become-verifier/", account_views.become_verifier, name="become_verifier"),
    path("profile/", account_views.profile, name="profile"),
    path("my-votes/", account_views.my_votes, name="my_votes"),
    path("verifiers/<int:pk>/", account_views.verifier_profile, name="verifier_profile"),
    path("i18n/setlang/", login_not_required(set_language), name="set_language"),
    path("admin/", admin.site.urls),
]

if settings.DEBUG:  # in production a web server serves /media/
    urlpatterns += [re_path(r"^media/(?P<path>.*)$", login_not_required(serve),
                            {"document_root": settings.MEDIA_ROOT})]
