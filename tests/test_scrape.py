"""Tests for public catalog parsing and URL checks."""

import socket
from pathlib import Path

import httpx
import pytest

from app.scrape import (
    fetch_catalog_html,
    parse_catalog,
    robots_allows,
    validate_public_url,
)

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.html"


def _resolver(*addresses: str):
    def resolve(_host, _port):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))
            for address in addresses
        ]

    return resolve


def _failing_resolver(_host, _port):
    raise socket.gaierror("no such host")


def test_parse_catalog_keeps_donor_fields_and_drops_images():
    donors = parse_catalog(FIXTURE.read_text(encoding="utf-8"))

    assert [(row.code, row.blood_type, row.family_limit) for row in donors] == [
        ("CAT-1", "O", "10"),
        ("CAT-2", "A", "3"),
    ]
    assert all("http" not in value for row in donors for value in row.__dict__.values())


def test_robots_allows_uses_the_longest_star_rule():
    robots = """
    User-agent: BadBot
    Disallow: /

    User-agent: *
    Disallow: /private
    Disallow: /cat
    Allow: /catalog
    User-agent: Other
    Disallow: /
    """

    assert robots_allows("", "/catalog") is True
    assert robots_allows("User-agent: *\nDisallow: /\n", "/catalog") is False
    assert robots_allows(robots, "/catalog") is True
    assert robots_allows(robots, "/cat") is False
    assert robots_allows(robots, "/elsewhere") is True
    assert robots_allows("User-agent: *\nAllow: /a\nDisallow: /a\n", "/a") is True


def test_validate_public_url_rejects_unsafe_targets():
    with pytest.raises(ValueError, match="public http or https"):
        validate_public_url("ftp://example.com/catalog")
    with pytest.raises(ValueError, match="credentials"):
        validate_public_url("https://user:pass@example.com/catalog")
    with pytest.raises(ValueError, match="did not resolve"):
        validate_public_url("https://example.com/catalog", resolver=_failing_resolver)
    with pytest.raises(ValueError, match="did not resolve"):
        validate_public_url(
            "https://example.com/catalog", resolver=lambda _host, _port: []
        )
    with pytest.raises(ValueError, match="not a public"):
        validate_public_url(
            "https://example.com/catalog", resolver=_resolver("8.8.8.8", "10.1.1.1")
        )
    with pytest.raises(ValueError, match="not a public"):
        validate_public_url(
            "https://example.com/catalog", resolver=_resolver("127.0.0.1")
        )

    raw = "  https://example.com/catalog  "
    assert validate_public_url(raw, resolver=_resolver("8.8.8.8")) == raw


def test_fetch_catalog_html_returns_a_public_page():
    html = FIXTURE.read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /catalog\n")
        assert request.url.path == "/catalog"
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, text=html
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        body = fetch_catalog_html(
            "https://example.com/catalog",
            client,
            resolver=_resolver("8.8.8.8"),
        )

    assert "CAT-1" in body


def test_fetch_catalog_html_rejects_blocked_or_unusable_responses():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            if request.url.host == "blocked.example":
                return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
            if request.url.host == "down-robots.example":
                raise httpx.ConnectError("robots down")
            if request.url.host == "no-robots.example":
                return httpx.Response(503, text="missing")
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.host == "missing.example":
            return httpx.Response(404, text="nope")
        if request.url.host == "huge.example":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"x" * 1_000_001,
            )
        if request.url.host == "image.example":
            return httpx.Response(
                200, headers={"content-type": "image/png"}, content=b"png"
            )
        if request.url.host == "page-down.example":
            raise httpx.ConnectError("page down")
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="ok")

    def fetch(host: str) -> str:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return fetch_catalog_html(
                f"https://{host}/catalog",
                client,
                resolver=_resolver("8.8.8.8"),
            )

    with pytest.raises(ValueError, match="disallows"):
        fetch("blocked.example")
    with pytest.raises(ValueError, match="robots.txt"):
        fetch("down-robots.example")
    with pytest.raises(ValueError, match="HTTP 404"):
        fetch("missing.example")
    with pytest.raises(ValueError, match="too large"):
        fetch("huge.example")
    with pytest.raises(ValueError, match="not HTML"):
        fetch("image.example")
    with pytest.raises(ValueError, match="Could not fetch"):
        fetch("page-down.example")
    assert fetch("no-robots.example") == "ok"
    assert fetch("plain.example") == "ok"
