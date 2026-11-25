"""Safe HTTP fetching: http/https only, no private/internal addresses (SSRF guard), size + time limits."""
import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from claims.validators import validate_safe_url

from django.conf import settings


def user_agent():
    # Wikimedia and others require contact details in a bot's User-Agent.
    return f"VerdictoFactCheck/1.0 ({settings.RESEARCH_CONTACT}) python-httpx"
TIMEOUT = 8.0
MAX_BYTES = 1_500_000
MAX_REDIRECTS = 3


class UnsafeURL(Exception):
    pass


def assert_public_host(url):
    validate_safe_url(url)
    host = urlparse(url).hostname
    if not host:
        raise UnsafeURL("No host.")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURL(f"Cannot resolve {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UnsafeURL(f"{host} resolves to a non-public address.")


def get(url, params=None, accept="text/html,application/json,application/xml;q=0.9"):
    """GET with the SSRF guard re-checked on every redirect hop. Returns an httpx.Response."""
    headers = {"User-Agent": user_agent(), "Accept": accept}
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            assert_public_host(url)
            with client.stream("GET", url, params=params) as resp:
                if resp.is_redirect:
                    url = urljoin(str(resp.url), resp.headers.get("location", ""))
                    params = None
                    continue
                resp.raise_for_status()
                body = b""
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        break
                resp._content = body
                return resp
    raise UnsafeURL("Too many redirects.")


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "form", "aside"}

    def __init__(self):
        super().__init__()
        self.title, self.parts, self._skip, self._in_title, self._in_p = "", [], 0, False, False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("p", "h1", "h2", "li"):
            self._in_p = True

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in ("p", "h1", "h2", "li"):
            self._in_p = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_p and not self._skip and data.strip():
            self.parts.append(data.strip())


def extract_article(html, max_chars=2500):
    """Return (title, main text) from an HTML page using paragraph text only."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # malformed HTML: use whatever was parsed
        pass
    text = " ".join(p for p in parser.parts if len(p) > 30)
    return parser.title.strip()[:300], text[:max_chars]


def fetch_article(url):
    resp = get(url, accept="text/html")
    if "html" not in resp.headers.get("content-type", ""):
        return "", ""
    return extract_article(resp.text)
