"""Tests for the features completed after PRD2: screenshots, OG image, profiles, work-email OTP,
source feedback, community accuracy, re-check and the Kannada UI."""
import ast
import io
import re
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts import work_email
from accounts.models import User
from chat.models import ChatThread
from claims.models import Claim
from research import ai, connectors
from research.models import ResearchRun, Source, SourceFeedback
from verification import engine
from verification.services import cast_community_vote, cast_vote

MEDIA = tempfile.mkdtemp()
OFFLINE = dict(RESEARCH_SYNC=True, RESEARCH_CONNECTORS=["fake"], GROQ_API_KEY="", RATELIMIT_ENABLE=False,
               MEDIA_ROOT=MEDIA)


def png_bytes(size=(300, 120)):
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, "PNG")
    return buf.getvalue()


def verifier(name, **kw):
    return User.objects.create_user(name, password="pw-123456", role="verifier", verifier_approved=True, **kw)


@override_settings(**OFFLINE)
class ScreenshotTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        patcher = mock.patch.dict(connectors.CONNECTORS, {"fake": lambda q, l: []})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = User.objects.create_user("asha", password="pw-123456")
        self.client.force_login(self.user)

    def upload(self, data, name="shot.png", ctype="image/png", text=""):
        return self.client.post(reverse("chat_send"), {"text": text, "language": "en",
                                "screenshot": SimpleUploadedFile(name, data, content_type=ctype)})

    def test_screenshot_is_read_and_stored_with_random_name(self):
        with mock.patch.object(ai, "extract_text_from_image",
                               return_value=("ನಾಳೆ ಎಲ್ಲಾ ಶಾಲೆಗಳಿಗೆ ರಜೆ ಎಂದು ಡಿಸಿ ಆದೇಶ", "kn")):
            resp = self.upload(png_bytes(), name="../../evil name.png")
        claim = Claim.objects.get()
        self.assertRedirects(resp, reverse("chat_thread", args=[ChatThread.objects.get().pk]))
        self.assertEqual(claim.language, "kn")
        self.assertIn("ಶಾಲೆಗಳಿಗೆ", claim.text)
        self.assertRegex(claim.screenshot.name, r"^screenshots/[0-9a-f]{32}\.jpg$")
        self.assertNotIn("evil", claim.screenshot.name)

    def test_typed_text_wins_over_ocr(self):
        with mock.patch.object(ai, "extract_text_from_image", return_value=("ocr text here", "en")):
            self.upload(png_bytes(), text="Schools closed tomorrow in all districts")
        self.assertEqual(Claim.objects.get().text, "Schools closed tomorrow in all districts")

    def test_rejects_non_images_and_big_files(self):
        resp = self.upload(b"<script>alert(1)</script>", name="x.png")
        self.assertIn("error=", resp["Location"])
        with override_settings(MAX_UPLOAD_BYTES=100):
            resp = self.upload(png_bytes())
        self.assertIn("5+MB", resp["Location"])
        self.assertEqual(Claim.objects.count(), 0)

    def test_unreadable_image_asks_for_text(self):
        with mock.patch.object(ai, "extract_text_from_image", return_value=("", None)):
            resp = self.upload(png_bytes())
        self.assertIn("error=", resp["Location"])
        self.assertEqual(Claim.objects.count(), 0)


@override_settings(RATELIMIT_ENABLE=False)
class ShareImageTests(TestCase):
    def test_og_image_is_public_png_and_linked(self):
        claim = Claim.objects.create(text="5G towers spread viruses", status="FINAL", verdict="FALSE")
        resp = self.client.get(reverse("og_image", args=[claim.share_slug]))
        self.assertEqual((resp.status_code, resp["Content-Type"]), (200, "image/png"))
        self.assertEqual(Image.open(io.BytesIO(resp.content)).size, (1200, 630))
        self.assertContains(self.client.get(reverse("share", args=[claim.share_slug])), "og.png")

    def test_hidden_claims_have_no_image(self):
        claim = Claim.objects.create(text="x" * 20, is_hidden=True)
        self.assertEqual(self.client.get(reverse("og_image", args=[claim.share_slug])).status_code, 404)


@override_settings(RATELIMIT_ENABLE=False, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class WorkEmailTests(TestCase):
    def setUp(self):
        self.v = verifier("v")
        self.client.force_login(self.v)

    def post(self, **data):
        return self.client.post(reverse("profile"), data)

    def code_from_mail(self):
        return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

    def test_free_providers_rejected(self):
        resp = self.post(action="send_code", email="me@gmail.com")
        self.assertContains(resp, "not a free email provider")
        self.assertEqual(len(mail.outbox), 0)

    @mock.patch("accounts.work_email.socket.getaddrinfo", return_value=[None])
    def test_full_flow_gives_badge_and_trust_bonus(self, _dns):
        self.post(action="send_code", email="Dr@Hospital.Example.in")
        self.assertEqual(mail.outbox[-1].to, ["dr@hospital.example.in"])
        otp = self.v.email_otps.get()
        self.assertNotIn(self.code_from_mail(), otp.code_hash)       # only a hash is stored
        self.assertContains(self.post(action="verify_code", code="000000"), "Wrong code. 4 attempts left")
        self.post(action="verify_code", code=self.code_from_mail())
        self.v.refresh_from_db()
        self.assertTrue(self.v.work_email_verified)
        self.assertEqual(self.v.org_domain, "hospital.example.in")
        claim = Claim.objects.create(text="claim text here", domain="health")
        vote = cast_vote(claim, self.v, "UNVERIFIED")
        self.assertAlmostEqual(vote.weight_at_vote, 0.6)

    @mock.patch("accounts.work_email.socket.getaddrinfo", return_value=[None])
    def test_attempt_limit(self, _dns):
        work_email.send_code(self.v, "a@org.example.in")
        for _ in range(5):
            with self.assertRaises(work_email.WorkEmailError):
                work_email.verify_code(self.v, "000000")
        with self.assertRaisesRegex(work_email.WorkEmailError, "Too many attempts"):
            work_email.verify_code(self.v, self.code_from_mail())

    def test_regular_users_cannot_use_otp(self):
        u = User.objects.create_user("u", password="pw-123456")
        self.client.force_login(u)
        self.post(action="send_code", email="x@org.example.in")
        self.assertEqual(len(mail.outbox), 0)


@override_settings(RATELIMIT_ENABLE=False)
class ProfileAndFeedbackTests(TestCase):
    def setUp(self):
        self.claim = Claim.objects.create(text="VTU exams postponed", domain="education")
        self.user = User.objects.create_user("cit", password="pw-123456", first_name="Asha")

    def test_profile_pages(self):
        v = verifier("dr", first_name="Meera")
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("profile")), "community accuracy")
        self.assertEqual(self.client.get(reverse("my_votes")).status_code, 200)
        self.client.logout()
        self.assertContains(self.client.get(reverse("verifier_profile", args=[v.pk])), "Meera")
        pending = User.objects.create_user("p", password="pw-123456", role="verifier")
        self.assertEqual(self.client.get(reverse("verifier_profile", args=[pending.pk])).status_code, 404)

    def test_profile_edit(self):
        self.client.force_login(self.user)
        self.client.post(reverse("profile"), {"action": "profile", "name": "Asha Rao", "interests": ["health"],
                                              "preferred_language": "kn", "district": "Hassan"})
        self.user.refresh_from_db()
        self.assertEqual((self.user.first_name, self.user.domains, self.user.preferred_language),
                         ("Asha Rao", ["health"], "kn"))

    def test_community_accuracy_tracked_without_double_counting(self):
        cast_community_vote(self.claim, self.user, "FALSE")
        for i in range(3):
            cast_vote(self.claim, verifier(f"v{i}"), "FALSE", "checked", ["https://vtu.ac.in/x"])
        self.user.refresh_from_db()
        self.assertEqual((self.user.community_correct, self.user.community_total), (1, 1))
        from verification.services import admin_override
        admin_override(self.claim, User.objects.create_user("a", password="x", role="admin"), "TRUE", "VTU notice")
        self.user.refresh_from_db()
        self.assertEqual((self.user.community_correct, self.user.community_total), (0, 1))

    def test_source_feedback_only_for_verifiers(self):
        run = ResearchRun.objects.create(claim=self.claim, status="done")
        src = Source.objects.create(run=run, url="https://vtu.ac.in", connector="x", tier="official", tier_weight=1)
        url = reverse("source_feedback", args=[src.pk])
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(url, {"useful": "1"}).status_code, 403)
        v = verifier("v")
        self.client.force_login(v)
        self.client.post(url, {"useful": "1"})
        self.client.post(url, {"useful": "0"})                 # changes, doesn't duplicate
        self.assertEqual(list(SourceFeedback.objects.values_list("useful", flat=True)), [False])

    @override_settings(RESEARCH_SYNC=True, RESEARCH_CONNECTORS=[], GROQ_API_KEY="")
    def test_recheck_permissions(self):
        url = reverse("recheck", args=[self.claim.pk])
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(url).status_code, 403)          # not their claim
        ChatThread.objects.create(owner=self.user, title="t", claim=self.claim)
        self.client.post(url, {"next": "/"})
        self.assertEqual(self.claim.research_runs.count(), 1)


class KannadaUITests(TestCase):
    def test_every_template_string_has_a_kannada_translation(self):
        src = (Path(settings.BASE_DIR) / "locale" / "kn_strings.py").read_text()
        kn = ast.literal_eval(next(n.value for n in ast.parse(src).body if isinstance(n, ast.Assign)))
        missing = set()
        for path in (Path(settings.BASE_DIR) / "templates").rglob("*.html"):
            missing |= set(re.findall(r'{% translate "([^"]+)" %}', path.read_text())) - set(kn)
        self.assertEqual(missing, set())

    def test_switch_to_kannada(self):
        user = User.objects.create_user("k", password="pw-123456", first_name="Kiran")
        self.client.force_login(user)
        self.client.post(reverse("set_language"), {"language": "kn", "next": "/"})
        page = self.client.get(reverse("chat"))
        self.assertContains(page, "ಇಂದು ನೀವು ಏನನ್ನು ಪರಿಶೀಲಿಸಲು ಬಯಸುತ್ತೀರಿ?")
        self.assertContains(page, "ಸುದ್ದಿ ಮತ್ತು ತೀರ್ಪುಗಳು")

    def test_preferred_language_used_by_default(self):
        user = User.objects.create_user("k2", password="pw-123456", preferred_language="kn")
        self.client.force_login(user)
        self.assertContains(self.client.get(reverse("chat")), "ಮತ್ತೆ ಸ್ವಾಗತ")

    def test_login_page_switcher_for_visitors(self):
        self.assertContains(self.client.get(reverse("login")), "ಕನ್ನಡ")
