"""Sharing through a tunnel: admin and debug pages are hidden from tunnel visitors."""
from django.test import TestCase, override_settings

from accounts.models import User
from claims.models import Claim

TUNNEL = "demo-words-here.trycloudflare.com"


@override_settings(DEBUG=True)
class TunnelGuardTests(TestCase):
    def test_admin_is_local_only(self):
        self.assertEqual(self.client.get("/admin/", HTTP_HOST=TUNNEL, secure=True).status_code, 404)
        self.assertEqual(self.client.get("/admin/login/", HTTP_HOST=TUNNEL, secure=True).status_code, 404)
        self.assertEqual(self.client.get("/admin/login/", HTTP_HOST="127.0.0.1").status_code, 200)

    def test_tunnel_visitors_get_a_plain_404_not_the_debug_page(self):
        User.objects.create_user("u", password="pw-12345678", phone="+919845000111")
        self.client.login(username="u", password="pw-12345678")
        remote = self.client.get("/claims/99999/", HTTP_HOST=TUNNEL, secure=True)
        self.assertEqual(remote.status_code, 404)
        self.assertNotContains(remote, "DEBUG = True", status_code=404)
        self.assertContains(remote, "This page doesn", status_code=404)
        local = self.client.get("/claims/99999/", HTTP_HOST="127.0.0.1")
        self.assertContains(local, "DEBUG = True", status_code=404)          # unchanged on this laptop

    def test_app_works_through_the_tunnel_and_share_links_use_it(self):
        claim = Claim.objects.create(text="Schools closed tomorrow", status="PENDING")
        resp = self.client.get(f"/claims/{claim.pk}/", HTTP_HOST=TUNNEL, secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"https%3A//{TUNNEL}/c/{claim.share_slug}/")   # WhatsApp link
        login = self.client.get("/accounts/login/", HTTP_HOST=TUNNEL, secure=True)
        self.assertEqual(login.status_code, 200)
