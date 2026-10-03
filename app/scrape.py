"""Public catalog fetch and parse.

The parser keeps text fields and drops images. Login-gated hosts, credentials
in the URL, and non-public addresses are rejected.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urlparse

import httpx

Resolver = Callable[..., list]


@dataclass(frozen=True)
class CatalogDonor:
    """Fields confirmed by a person before they affect a score."""

    code: str
    ancestry: str
    blood_type: str
    rh: str
    cmv: str
    panel: str
    quarantine: str
    family_limit: str
    id_release_policy: str


class _CatalogHTMLParser(HTMLParser):
    """Read article.donor data attributes. Image tags are ignored."""

    def __init__(self) -> None:
        super().__init__()
        self.donors: list[CatalogDonor] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "article":
            return
        attr = {key: value or "" for key, value in attrs}
        classes = set(attr.get("class", "").split())
        if "donor" not in classes or not attr.get("data-code"):
            return
        self.donors.append(
            CatalogDonor(
                code=attr.get("data-code", "").strip(),
                ancestry=attr.get("data-ancestry", "").strip(),
                blood_type=attr.get("data-blood", "").strip(),
                rh=attr.get("data-rh", "").strip(),
                cmv=attr.get("data-cmv", "").strip(),
                panel=attr.get("data-panel", "").strip(),
                quarantine=attr.get("data-quarantine", "").strip(),
                family_limit=attr.get("data-family-limit", "").strip(),
                id_release_policy=attr.get("data-id-release", "").strip(),
            )
        )


def parse_catalog(html: str) -> list[CatalogDonor]:
    """Parse donor articles from a catalog page.

    Args:
        html: Public catalog HTML.

    Returns:
        Donor field sets. Image URLs are not returned.
    """
    parser = _CatalogHTMLParser()
    parser.feed(html)
    return parser.donors


def robots_allows(robots_txt: str, path: str) -> bool:
    """Apply the User-agent: * rules to a path.

    Args:
        robots_txt: robots.txt body.
        path: URL path beginning with ``/``.

    Returns:
        True when the longest matching rule allows the path.
    """
    capture = False
    rules: list[tuple[str, str]] = []
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if capture and rules:
                break
            capture = value == "*"
            continue
        if capture and key in {"allow", "disallow"}:
            rules.append((key, value))
    matches = [
        (len(prefix), kind)
        for kind, prefix in rules
        if prefix and (path == prefix or path.startswith(prefix))
    ]
    if not matches:
        return not any(kind == "disallow" and prefix == "/" for kind, prefix in rules)
    best = max(length for length, _kind in matches)
    kinds = {kind for length, kind in matches if length == best}
    return "allow" in kinds


def validate_public_url(url: str, resolver: Resolver = socket.getaddrinfo) -> str:
    """Accept an http(s) URL whose addresses are public.

    Args:
        url: Catalog URL pasted by the bank.
        resolver: getaddrinfo-compatible resolver. Tests pass a fake.

    Returns:
        The same URL when it is safe to fetch.

    Raises:
        ValueError: The URL is not a public http(s) address.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Enter a public http or https catalog URL.")
    if parsed.username or parsed.password:
        raise ValueError("The catalog URL must not include credentials.")
    host = parsed.hostname
    try:
        infos = resolver(host, None)
    except socket.gaierror as exc:
        raise ValueError("The catalog host did not resolve.") from exc
    if not infos:
        raise ValueError("The catalog host did not resolve.")
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ValueError("That host is not a public catalog address.")
    return url


def fetch_catalog_html(
    url: str,
    client: httpx.Client,
    resolver: Resolver = socket.getaddrinfo,
) -> str:
    """Fetch one public catalog page without following redirects.

    Args:
        url: Catalog URL.
        client: HTTP client.
        resolver: Address resolver used by the public-URL check.

    Returns:
        Response text.

    Raises:
        ValueError: The URL, robots.txt rule, or response is not acceptable.
    """
    safe = validate_public_url(url, resolver)
    parsed = urlparse(safe)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        robots = client.get(robots_url, timeout=5.0, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise ValueError("Could not read robots.txt for that host.") from exc
    path = parsed.path or "/"
    if robots.status_code == 200 and not robots_allows(robots.text, path):
        raise ValueError("The catalog host disallows this path.")
    try:
        response = client.get(safe, timeout=8.0, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise ValueError("Could not fetch the catalog page.") from exc
    if response.status_code != 200:
        raise ValueError(f"The catalog page returned HTTP {response.status_code}.")
    if len(response.content) > 1_000_000:
        raise ValueError("The catalog page is too large.")
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type and "text/plain" not in content_type:
        raise ValueError("The catalog page was not HTML.")
    return response.text
