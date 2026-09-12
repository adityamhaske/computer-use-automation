"""Allowlist, risk classification and redaction, as pure units.

The engine's behaviour is covered by the invariant tests; these cover the parts it is assembled
from, including the cases where each one is individually fooled.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cua.domain.action import ActionRisk, Click, Extract, Navigate
from cua.domain.snapshot import UiNode
from cua.domain.target import TargetDescriptor
from cua.policy.allowlist import AllowlistCheck
from cua.policy.config import parse_policy
from cua.policy.redact import Redactor
from cua.policy.risk import RiskClassifier
from cua.policy.secrets import SecretNotFoundError, SecretResolver

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"


@pytest.fixture
def config():
    return parse_policy(POLICY.read_text())


# ------------------------------------------------------------- allowlist


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("http://localhost:8811/search", True),
        ("http://127.0.0.1:8811/member/12345", True),
        ("http://evil.example.com/collect", False),
        ("https://localhost:9999/", False),
        ("file:///etc/passwd", False),
        ("javascript:alert(1)", False),
        ("", False),
    ],
)
def test_allowlist_decisions(config, url: str, allowed: bool) -> None:
    assert AllowlistCheck(config.allowlist).check(url).allowed is allowed


def test_non_http_schemes_are_refused_by_scheme_not_by_host(config) -> None:
    """`file://`, `data:` and `javascript:` are ways to leave the sandbox or execute
    attacker-controlled content, and none is a legitimate app navigation. Rejecting them on scheme
    means a permissive host list cannot accidentally admit them."""
    verdict = AllowlistCheck(config.allowlist).check("file:///etc/passwd")
    assert "scheme" in verdict.reason


def test_a_capability_can_narrow_but_never_widen(config) -> None:
    """An artifact must not be able to grant itself reach the deployment did not intend..."""
    widening = AllowlistCheck(config.allowlist, extra_domains=("evil.example.com",))
    assert not widening.check("http://evil.example.com/").allowed

    # ...and a narrowing must actually bind. An earlier version intersected the domain sets and
    # then fell through to the global url_patterns, so a capability scoped to one host still
    # reached anything a pattern matched. A narrowing a pattern can reopen is not a narrowing.
    assert not widening.check("http://localhost:8811/search").allowed


def test_a_capability_narrowing_permits_only_its_own_host(config) -> None:
    narrowed = AllowlistCheck(config.allowlist, extra_domains=("localhost:8811",))
    assert narrowed.check("http://localhost:8811/search").allowed
    assert not narrowed.check("http://localhost:8812/console").allowed


# ------------------------------------------------------------------ risk


def test_each_signal_can_classify_on_its_own(config) -> None:
    classifier = RiskClassifier(config.risk)
    target = TargetDescriptor(role="button")

    by_lexicon = classifier.classify(
        Click(target=target), target=UiNode(node_id="n", role="button", name="Delete Account")
    )
    assert by_lexicon.tier is ActionRisk.IRREVERSIBLE

    by_annotation = classifier.classify(
        Click(target=target),
        target=UiNode(node_id="n", role="button", name="Go"),
        declared=ActionRisk.IRREVERSIBLE,
    )
    assert by_annotation.tier is ActionRisk.IRREVERSIBLE


def test_the_lexicon_alone_is_fooled_and_the_annotation_catches_it(config) -> None:
    """Why three signals rather than one.

    A button labelled "Continue" that posts a wire transfer is invisible to a word list. Only a
    reviewer's annotation catches it -- which is the argument for combining signals rather than
    picking the best one.
    """
    classifier = RiskClassifier(config.risk)
    deceptive = UiNode(node_id="n", role="button", name="Continue")

    unannotated = classifier.classify(
        Click(target=TargetDescriptor(role="button")), target=deceptive
    )
    assert unannotated.tier is ActionRisk.SAFE
    assert (
        classifier.classify(
            Click(target=TargetDescriptor(role="button")),
            target=deceptive,
            declared=ActionRisk.IRREVERSIBLE,
        ).tier
        is ActionRisk.IRREVERSIBLE
    )


def test_reading_from_a_dangerous_screen_is_still_safe(config) -> None:
    """An extraction cannot change state whatever it is pointed at. Classifying it by the words on
    screen would make every read from an admin page demand a human, and a guardrail that fires
    constantly is a guardrail people route around."""
    assessment = RiskClassifier(config.risk).classify(
        Extract(target=TargetDescriptor(role="cell"), into="balance"),
        target=UiNode(node_id="n", role="cell", name="Delete Account"),
    )
    assert assessment.tier is ActionRisk.SAFE


def test_the_highest_tier_wins(config) -> None:
    """Combining conservatively: a false positive costs a confirmation prompt, a false negative
    could cost a customer's money. Those are not symmetric."""
    assessment = RiskClassifier(config.risk).classify(
        Navigate(url="http://localhost:8811/"),
        target=UiNode(node_id="n", role="button", name="Wire Transfer"),
        declared=ActionRisk.SAFE,
    )
    assert assessment.tier is ActionRisk.IRREVERSIBLE


def test_an_assessment_explains_itself(config) -> None:
    assessment = RiskClassifier(config.risk).classify(
        Click(target=TargetDescriptor(role="button")),
        target=UiNode(node_id="n", role="button", name="Transfer Funds"),
    )
    assert "lexicon" in assessment.explain()


# -------------------------------------------------------------- secrets


def test_a_missing_secret_is_fatal_not_empty() -> None:
    """Continuing with an empty string would type nothing into a password field and surface as a
    confusing authentication failure several steps later."""
    from cua.domain.values import SecretRef

    with pytest.raises(SecretNotFoundError):
        SecretResolver().resolve(SecretRef.model_validate({"$secret": "nope.missing"}))


def test_issued_secrets_are_tracked_for_scrubbing() -> None:
    from cua.domain.values import SecretRef

    resolver = SecretResolver(overrides={"core.pw": "a-real-looking-password"})
    resolver.resolve(SecretRef.model_validate({"$secret": "core.pw"}))
    assert "a-real-looking-password" in resolver.issued_values


# ------------------------------------------------------------ redaction


def test_short_secrets_are_not_registered(config) -> None:
    """Scrubbing every occurrence of a two-character value would corrupt unrelated text far more
    than it would protect anything."""
    redactor = Redactor(config.redaction)
    redactor.register_secret("ab")
    assert "ab" not in redactor.extra_secrets
