"""Allowlist edge cases: URL-parsing traps, scheme smuggling, narrowing, and hostile configuration.

The allowlist is the primary defense against a prompt-injection redirect, so the question worth
asking of it is not "does it admit localhost" but "is there any spelling of a URL that a browser
reads as one host and this check reads as another". Every row below is a spelling of that kind. A
row that is refused while naming an allowed host (userinfo, a trailing dot) is a deliberate false
positive: refusing is the cheap error here and admitting is the expensive one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cua.policy.allowlist import AllowlistCheck
from cua.policy.config import Allowlist, parse_policy

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"


@pytest.fixture(scope="module")
def shipped() -> Allowlist:
    return parse_policy(POLICY.read_text(encoding="utf-8")).allowlist


@pytest.fixture(scope="module")
def check(shipped: Allowlist) -> AllowlistCheck:
    return AllowlistCheck(shipped)


# Each of these names, or can be made to look like it names, the allowed host, while the bytes are
# something else. Keyed by the trick so a failure reads as the attack that got through.
HOSTILE_URLS = {
    # userinfo / authority confusion
    "userinfo names allowed host, real host is evil": "http://localhost:8811@evil.example.com/",
    "userinfo evil, real host allowed": "http://evil.example.com@localhost:8811/",
    "userinfo with password": "http://user:pw@localhost:8811/",
    "backslash hides the real host (evil first)": "http://evil.example.com\\@localhost:8811/",
    "backslash hides the real host (allowed first)": "http://localhost:8811\\@evil.example.com/",
    "encoded slash in authority": "http://localhost:8811%2f@evil.example.com/",
    "encoded colon in host": "http://localhost%3a8811/",
    "NUL splits the host": "http://localhost:8811\x00.evil.example.com/",
    "space inside the host": "http://local host:8811/",
    # host spelling
    "trailing dot": "http://localhost.:8811/",
    "trailing dot after the port": "http://localhost:8811./",
    "uppercase with trailing dot": "http://LOCALHOST.:8811/",
    "loopback with trailing dot": "http://127.0.0.1.:8811/",
    "subdomain of an allowed host": "http://evil.localhost:8811/",
    "allowed host as a prefix of an evil domain": "http://localhost:8811.evil.example.com/",
    "second loopback address": "http://127.0.0.2:8811/",
    "IPv6 loopback": "http://[::1]:8811/",
    "IPv4-mapped IPv6 loopback": "http://[::ffff:127.0.0.1]:8811/",
    "0.0.0.0 is not localhost": "http://0.0.0.0:8811/",
    "bare zero host": "http://0:8811/",
    "short-form loopback": "http://127.1:8811/",
    "hex-form loopback": "http://0x7f.0.0.1:8811/",
    "decimal-form loopback": "http://2130706433:8811/",
    # port spelling
    "leading zero in the port": "http://localhost:08811/",
    "port with an extra digit": "http://localhost:88110/",
    "neighbouring port": "http://localhost:8813/",
    "port below the allowed pair": "http://localhost:8810/",
    "empty port": "http://localhost:/",
    "default port only": "http://localhost/",
    "doubled port": "http://localhost:8811:8812/",
    "allowed port on an evil host": "http://evil.example.com:8811/",
    # unicode
    "Cyrillic o homoglyph": "http://lоcalhost:8811/",
    "fullwidth letters": "http://ｌｏｃａｌｈｏｓｔ:8811/",
    "fullwidth digits in the port": "http://localhost:８８１１/",
    "Arabic-Indic digits in the port": "http://localhost:٨٨١١/",
    "zero-width space in the host": "http://local​host:8811/",
    "zero-width space before the colon": "http://localhost​:8811/",
    "right-to-left override": "http://‮localhost:8811/",
    "combining mark on the host": "http://localhosṫ:8811/",
    "ideographic full stop": "http://localhost。:8811/",
    "punycode label": "http://xn--localhost-8gd:8811/",
    # the allowed host appears, but only as data
    "allowed host in the path of an evil URL": "http://evil.example.com/localhost:8811",
    "allowed URL in the query of an evil URL": "http://evil.example.com/?u=http://localhost:8811/",
    "allowed host in the fragment of an evil URL": "http://evil.example.com/#localhost:8811",
    # missing host
    "no authority at all": "http:localhost:8811/",
    "single slash": "http:/localhost:8811/",
    "triple slash": "http:///localhost:8811/",
    "four slashes": "http:////localhost:8811/",
    "port with no host": "http://:8811/",
    "nothing after the scheme": "http://",
}


@pytest.mark.parametrize("url", list(HOSTILE_URLS.values()), ids=list(HOSTILE_URLS))
def test_a_spelling_that_could_name_a_different_host_is_refused(
    check: AllowlistCheck, url: str
) -> None:
    verdict = check.check(url)

    assert verdict.allowed is False
    assert verdict.reason, "a refusal without a reason cannot be debugged from the evidence"


# The mirror image: if these were refused, the allowlist would be blocking the app it protects.
# What matters in each is that the *authority* is exactly an allowed host; everything around it is
# the application's own business, and the destination of a redirect is checked where it lands.
BENIGN_URLS = {
    "plain": "http://localhost:8811/",
    "no trailing slash": "http://localhost:8811",
    "uppercase scheme and host": "HTTP://LOCALHOST:8811/Search",
    "https on an allowed host": "https://localhost:8811/search?q=1",
    "second allowed host and port": "http://127.0.0.1:8812/console#top",
    "query directly after the authority": "http://localhost:8811?x=1",
    "fragment directly after the authority": "http://localhost:8811#frag",
    "evil URL in the query is only data": "http://localhost:8811/?next=http://evil.example.com/",
    "evil host in the fragment is only data": "http://localhost:8811#@evil.example.com",
    "at-sign in the path is not userinfo": "http://localhost:8811/a@evil.example.com",
    "dot-dot segments are the app's business": "http://localhost:8811/../../etc/passwd",
    "encoded dot-dot is the app's business": "http://localhost:8811/%2e%2e/",
}


@pytest.mark.parametrize("url", list(BENIGN_URLS.values()), ids=list(BENIGN_URLS))
def test_only_the_authority_is_judged_so_surrounding_url_parts_do_not_cause_refusals(
    check: AllowlistCheck, url: str
) -> None:
    verdict = check.check(url)

    assert verdict.allowed is True, verdict.reason
    assert verdict.reason == ""


NON_WEB_URLS = {
    "javascript": "javascript:alert(1)",
    "javascript uppercase": "JAVASCRIPT:alert(1)",
    "javascript split by a newline": "java\nscript:alert(1)",
    "javascript split by a tab": "java\tscript:alert(1)",
    "data": "data:text/html,<script>alert(1)</script>",
    "blob wrapping an allowed origin": "blob:http://localhost:8811/3f9c",
    "file": "file:///etc/passwd",
    "file on an allowed-looking host": "file://localhost:8811/etc/passwd",
    "ftp": "ftp://localhost:8811/",
    "websocket": "ws://localhost:8811/",
    "secure websocket": "wss://localhost:8811/",
    "about": "about:blank",
    "view-source": "view-source:http://localhost:8811/",
    "chrome internal": "chrome://settings",
    "scheme-relative": "//localhost:8811/",
    "path only": "/search",
    "host and port with no scheme": "localhost:8811/",
    "leading-space javascript": "  javascript:alert(1)",
}


@pytest.mark.parametrize("url", list(NON_WEB_URLS.values()), ids=list(NON_WEB_URLS))
def test_anything_that_is_not_http_or_https_is_refused_on_its_scheme(
    check: AllowlistCheck, url: str
) -> None:
    """Refused by scheme, before the host is even consulted, so a permissive host list can never
    admit `file://` or `javascript:` by accident."""
    verdict = check.check(url)

    assert verdict.allowed is False
    assert "scheme" in verdict.reason


@pytest.mark.parametrize("url", ["", None], ids=["empty", "none"])
def test_a_missing_url_is_refused_with_its_own_reason(check: AllowlistCheck, url: Any) -> None:
    verdict = check.check(url)

    assert verdict.allowed is False
    assert verdict.reason == "empty url"


@pytest.mark.parametrize("url", [" ", "\t\n", " ", "​"], ids=["space", "tab-nl", "nbsp", "zwsp"])
def test_a_whitespace_only_url_is_refused(check: AllowlistCheck, url: str) -> None:
    assert check.check(url).allowed is False


def test_an_empty_allowlist_denies_every_url_even_the_ones_the_app_needs() -> None:
    """Deny-by-default: a policy that lists nothing permits nothing, rather than everything."""
    verdict = AllowlistCheck(Allowlist()).check("http://localhost:8811/")

    assert verdict.allowed is False
    assert "(empty)" in verdict.reason


def test_a_refusal_names_the_offending_host_and_the_list_it_missed(check: AllowlistCheck) -> None:
    verdict = check.check("http://evil.example.com/collect")

    assert "evil.example.com" in verdict.reason
    assert "localhost:8811" in verdict.reason


def test_a_very_long_url_is_judged_not_crashed_on(check: AllowlistCheck) -> None:
    """Page text is untrusted and may hand the agent a megabyte of URL. Both directions must come
    back as a verdict."""
    assert check.check("http://localhost:8811/" + "a" * 1_000_000).allowed is True
    assert check.check("http://" + "a" * 200_000 + ":8811/").allowed is False
    assert (
        check.check("http://localhost:8811/" + "?" * 200_000 + "@evil.example.com").allowed is True
    )


# ------------------------------------------------------------ the config side of matching


def test_configured_domains_are_matched_case_insensitively() -> None:
    """A reviewer who writes `LocalHost:8811` in the YAML has not thereby locked themselves out."""
    allowlist = AllowlistCheck(Allowlist(domains=("LocalHost:8811",)))

    assert allowlist.check("http://localhost:8811/").allowed
    assert allowlist.check("http://LOCALHOST:8811/").allowed
    assert not allowlist.check("http://localhost:8812/").allowed


def test_a_domain_without_a_port_does_not_admit_the_same_host_on_a_port() -> None:
    """`example.com` and `example.com:8443` are different origins."""
    allowlist = AllowlistCheck(Allowlist(domains=("example.com",)))

    assert allowlist.check("https://example.com/").allowed
    assert not allowlist.check("https://example.com:8443/").allowed


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8811/",
        "http://localhost:8811",
        "https://127.0.0.1:8812/console?x=1",
    ],
)
def test_the_pattern_and_domain_routes_each_admit_the_shipped_hosts(
    shipped: Allowlist, url: str
) -> None:
    """Two mechanisms describe one allowlist. If they drift, one of them is silently the real one,
    so each is exercised alone."""
    by_domain = AllowlistCheck(Allowlist(domains=shipped.domains))
    by_pattern = AllowlistCheck(Allowlist(url_patterns=shipped.url_patterns))

    assert by_domain.check(url).allowed
    assert by_pattern.check(url).allowed


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:88110/",
        "http://localhost:8813/",
        "http://localhost:8811.evil.example.com/",
        "http://localhost:8811@evil.example.com/",
        "http://localhost:8811\\@evil.example.com/",
        "http://evil.example.com/localhost:8811",
        "http://127.0.0.11:8811/",
        "ftp://localhost:8811/",
    ],
)
def test_the_shipped_pattern_is_anchored_so_it_cannot_be_suffix_or_prefix_tricked(
    shipped: Allowlist, url: str
) -> None:
    """Run alone, with no domain list behind it: a regex that matched a prefix of the URL would be
    an open door, and with both mechanisms present the domain list would mask it."""
    by_pattern = AllowlistCheck(Allowlist(url_patterns=shipped.url_patterns))

    assert by_pattern.check(url).allowed is False


def test_a_host_admitted_by_a_pattern_is_still_subject_to_a_capabilitys_narrowing(
    shipped: Allowlist,
) -> None:
    """The narrowing is applied after the global check and cannot be reopened by a pattern -- the
    regression noted in `AllowlistCheck`. Here the host is admitted *only* by pattern."""
    narrowed = AllowlistCheck(
        Allowlist(url_patterns=shipped.url_patterns), extra_domains=("localhost:8811",)
    )

    assert narrowed.check("http://localhost:8811/").allowed
    assert not narrowed.check("http://127.0.0.1:8811/").allowed


# -------------------------------------------------------------------- narrowing


@pytest.mark.parametrize(
    "declared",
    [
        "localhost:8811",
        "http://localhost:8811",
        "http://localhost:8811/some/path",
        "LOCALHOST:8811",
    ],
)
def test_a_narrowing_may_be_written_as_a_host_a_url_or_in_any_case(
    shipped: Allowlist, declared: str
) -> None:
    narrowed = AllowlistCheck(shipped, extra_domains=(declared,))

    assert narrowed.check("http://localhost:8811/").allowed
    assert not narrowed.check("http://localhost:8812/").allowed
    assert not narrowed.check("http://127.0.0.1:8811/").allowed


def test_a_narrowing_with_two_hosts_admits_exactly_those_two(shipped: Allowlist) -> None:
    narrowed = AllowlistCheck(shipped, extra_domains=("localhost:8811", "127.0.0.1:8812"))

    assert narrowed.check("http://localhost:8811/").allowed
    assert narrowed.check("http://127.0.0.1:8812/").allowed
    assert not narrowed.check("http://localhost:8812/").allowed
    assert not narrowed.check("http://127.0.0.1:8811/").allowed


@pytest.mark.parametrize(
    "declared",
    [
        "",
        " ",
        "{base_url}",
        "localhost",
        "localhost:8811 ",
        "user@localhost:8811",
        "localhost:8811@evil.example.com",
    ],
    ids=[
        "empty",
        "space",
        "unresolved-placeholder",
        "no-port",
        "trailing-space",
        "userinfo",
        "userinfo-evil",
    ],
)
def test_a_narrowing_that_does_not_name_an_allowed_origin_admits_nothing(
    shipped: Allowlist, declared: str
) -> None:
    """Fail closed: a narrowing that is malformed, unresolved, or names a different origin must not
    quietly fall back to "no narrowing"."""
    narrowed = AllowlistCheck(shipped, extra_domains=(declared,))

    for url in ("http://localhost:8811/", "http://127.0.0.1:8811/", "http://localhost:8812/"):
        assert narrowed.check(url).allowed is False, (declared, url)


def test_narrowing_to_a_host_outside_the_global_list_still_admits_nothing(
    shipped: Allowlist,
) -> None:
    """A capability cannot widen: the global refusal is reported first, and the host stays refused
    even though the capability names it."""
    widened = AllowlistCheck(shipped, extra_domains=("evil.example.com",))
    verdict = widened.check("http://evil.example.com/")

    assert verdict.allowed is False
    assert "allowlist" in verdict.reason


def test_a_narrowing_refusal_says_which_capability_domains_were_missed(shipped: Allowlist) -> None:
    narrowed = AllowlistCheck(shipped, extra_domains=("localhost:8811",))
    verdict = narrowed.check("http://localhost:8812/")

    assert "capability" in verdict.reason
    assert "localhost:8811" in verdict.reason


# ----------------------------------------------------- hostile configuration


def test_a_url_with_no_host_is_never_allowed_even_if_the_config_lists_an_empty_domain() -> None:
    """`http:evil.example.com` has no authority for urlparse, but a browser reads it as
    `http://evil.example.com/`. A stray `- ""` in the YAML must not turn that into an open door."""
    allowlist = AllowlistCheck(parse_policy("allowlist:\n  domains: ['']\n").allowlist)

    assert allowlist.check("http:evil.example.com").allowed is False
    assert allowlist.check("http:///evil.example.com").allowed is False


def test_a_refusal_does_not_echo_credentials_embedded_in_the_url(check: AllowlistCheck) -> None:
    """The reason string is written to the evidence trace, where only pattern redaction applies and
    `admin:pw-9f3a@localhost:8811` matches none of the rules."""
    verdict = check.check("http://admin:pw-9f3a-LEAK@localhost:8811/")

    assert verdict.allowed is False
    # Compared case-insensitively: the old reason lowercased the authority, so an exact-case check
    # passed while the secret was still being written to the trace.
    assert "pw-9f3a-leak" not in verdict.reason.lower()
