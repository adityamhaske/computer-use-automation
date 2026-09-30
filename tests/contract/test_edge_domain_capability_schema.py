"""Edge cases for the capability schema: what it must reject, what it must seal, what it must
export.

`test_capability_schema.py` defends the happy path and the headline invariants. This file goes after
the boundaries: version and id formats, unknown fields at every depth, references hidden inside
nested predicates, the content hash's independence from authoring style, and the YAML round trip
under hostile or awkward text.

Offline and deterministic. Nothing here touches a browser, the network or the evidence directory.
"""

from __future__ import annotations

import copy
import json
import random
import warnings
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from cua.domain.action import ActionRisk
from cua.domain.capability import (
    SCHEMA_VERSION,
    Capability,
    InputSpec,
    OutputSpec,
    WaitPolicy,
)
from cua.domain.serde import dump_capability, from_yaml, load_capability, to_yaml

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/capabilities/savings_balance.yaml"


def _raw() -> dict[str, Any]:
    """The reference artifact as a plain dict, unsealed, safe to mutate."""
    raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    raw.pop("content_hash", None)
    return raw


def _cap(**changes: Any) -> Capability:
    return Capability.model_validate({**_raw(), **changes})


def _at(node: Any, path: tuple[str | int, ...]) -> Any:
    for key in path:
        node = node[key]
    return node


@pytest.fixture
def capability() -> Capability:
    return Capability.model_validate(_raw())


# ------------------------------------------------------------ version format


@pytest.mark.parametrize("version", ["0.0.0", "1.0.0", "10.20.30", "999999.0.1"])
def test_plain_semver_versions_are_accepted(version: str) -> None:
    assert _cap(version=version).ref.endswith(f"@{version}")


@pytest.mark.parametrize(
    "version",
    ["", "1", "1.0", "1.0.0.0", "v1.0.0", " 1.0.0", "1.0.0 ", "1.0.0\n", "1.x.0", "-1.0.0", "1..0"],
)
def test_malformed_versions_are_rejected_with_a_semver_message(version: str) -> None:
    """A version is the second half of the `id@version` a run record, approval and binding all
    name. One that does not parse would make "which version ran?" unanswerable, so it is refused
    at load rather than discovered at lookup time. Trailing whitespace and newlines are the
    classic way a hand-edited file slips past a `$`-anchored check."""
    with pytest.raises(ValidationError, match="semver"):
        _cap(version=version)


@pytest.mark.parametrize(
    "version",
    [
        "١.٠.٠",  # Arabic-Indic digits
        "１.０.０",  # fullwidth digits
        "1.0.٠",  # mixed
        "01.0.0",  # leading zero is forbidden by semver section 2
        "1.00.0",
    ],
)
def test_version_must_be_ascii_semver(version: str) -> None:
    """`ref` is `id@version` and is used to key approvals and bindings. A version spelled in
    another script is a different string that *looks* like the same number, which is exactly the
    lookalike a pinned reference exists to rule out."""
    with pytest.raises(ValidationError, match="semver"):
        _cap(version=version)


def test_schema_version_constant_is_the_default_stamp(capability: Capability) -> None:
    assert SCHEMA_VERSION == "1.0.0"
    assert _cap().schema_version == SCHEMA_VERSION


# ------------------------------------------------------ unknown fields, deep

UNKNOWN_FIELD_SITES: list[tuple[str | int, ...]] = [
    (),
    ("surface",),
    ("surface", "app"),
    ("entrypoint",),
    ("steps", 0),
    ("steps", 0, "action"),
    ("steps", 0, "action", "target"),
    ("steps", 0, "action", "target", "name"),
    ("steps", 0, "action", "target", "scope"),
    ("steps", 0, "action", "target", "anchor"),
    ("steps", 0, "precondition"),
    ("steps", 0, "precondition", "query"),
    ("steps", 0, "postcondition"),
    ("steps", 0, "wait"),
    ("inputs", 0),
    ("outputs", 0),
    ("outputs", 0, "source"),
    ("outcomes", 0),
    ("outcomes", 0, "detect"),
    ("recovery", 0),
    ("recovery", 0, "remedy", 0),
    ("recovery", 1, "remedy", 0),
    ("recovery", 1, "detect", "of", 0),
    ("recovery", 1, "detect", "of", 0, "query", "scope"),
    ("escalation",),
    ("policy",),
    ("provenance",),
    ("checkpoint",),
    ("checkpoint", "of", 1, "query"),
]


@pytest.mark.parametrize("site", UNKNOWN_FIELD_SITES, ids=lambda s: "/".join(map(str, s)) or "root")
def test_an_unknown_field_is_rejected_at_every_depth(site: tuple[str | int, ...]) -> None:
    """`extra="forbid"` has to hold on every nested model, not just the top one.

    A typo three levels down (`timout_ms`, `max_attmpts`) that is silently ignored is the worst
    authoring bug there is: the artifact loads, looks reviewed, and does not do what it says.
    """
    Capability.model_validate(_raw())  # control: the untouched document is valid

    raw = _raw()
    _at(raw, site)["smuggled"] = "x"

    with pytest.raises(ValidationError) as caught:
        Capability.model_validate(raw)
    assert any(
        err["type"] == "extra_forbidden" and err["loc"][-1] == "smuggled"
        for err in caught.value.errors()
    )


def test_a_renamed_python_attribute_is_not_a_second_spelling_of_a_wire_key() -> None:
    """The wait policy's wire key is `for`. Its attribute is `for_`. Both are accepted on input
    (`populate_by_name`), but the *document* always says `for` -- otherwise a hash computed from
    one spelling would differ from a hash computed from the other."""
    by_alias = WaitPolicy.model_validate({"for": "navigation", "timeout_ms": 10})
    by_name = WaitPolicy.model_validate({"for_": "navigation", "timeout_ms": 10})
    assert by_alias == by_name
    assert "for" in by_alias.model_dump(by_alias=True)
    assert "for_" not in by_alias.model_dump(by_alias=True)


# ------------------------------------------------------------------ bounds


@pytest.mark.parametrize(
    ("attempts", "valid"),
    [(-1, False), (0, False), (1, True), (2, True), (10, True), (11, False), (10**9, False)],
)
def test_recovery_attempts_are_bounded_on_both_sides(attempts: int, valid: bool) -> None:
    """Zero attempts is a rule that never acts; eleven is the start of a retry storm against a
    core banking system that is already struggling. Both edges are off-by-one prone."""
    raw = _raw()
    raw["recovery"][0]["max_attempts"] = attempts
    if valid:
        assert Capability.model_validate(raw).recovery[0].max_attempts == attempts
    else:
        with pytest.raises(ValidationError):
            Capability.model_validate(raw)


def test_recovery_attempts_cannot_be_declared_unbounded() -> None:
    """`null` must not be a way to say "retry forever". The bound is mandatory even though a
    default exists for authors who say nothing."""
    raw = _raw()
    raw["recovery"][0]["max_attempts"] = None
    with pytest.raises(ValidationError):
        Capability.model_validate(raw)

    del raw["recovery"][0]["max_attempts"]
    assert Capability.model_validate(raw).recovery[0].max_attempts == 2


def test_fractional_attempts_are_not_rounded_into_range() -> None:
    raw = _raw()
    raw["recovery"][0]["max_attempts"] = 2.5
    with pytest.raises(ValidationError):
        Capability.model_validate(raw)


# --------------------------------------------------------------- wait policy


def test_a_predicate_wait_without_a_predicate_is_refused() -> None:
    with pytest.raises(ValidationError, match="requires `until`"):
        WaitPolicy.model_validate({"for": "predicate"})


def test_a_predicate_wait_with_a_predicate_is_accepted() -> None:
    policy = WaitPolicy.model_validate(
        {"for": "predicate", "until": {"assert": "text_present", "value": "Ready"}}
    )
    assert policy.until is not None and policy.until.describe() == 'text "Ready" is present'


def test_there_is_no_sleep_mode() -> None:
    """The schema waits on conditions, never on durations. A `sleep` mode would be a determinism
    bug waiting to be authored, so it must not be spellable."""
    with pytest.raises(ValidationError):
        WaitPolicy.model_validate({"for": "sleep", "timeout_ms": 1000})


def test_wait_defaults_to_a_condition_not_a_duration() -> None:
    policy = WaitPolicy()
    assert policy.for_.value == "snapshot_stable"
    assert policy.until is None


# ----------------------------------------- references hidden in nested places

GHOST = {"$input": "ghost"}
NESTED_GHOST_SITES: dict[str, Any] = {
    "precondition under not/all_of": lambda raw: raw["steps"][0].update(
        precondition={
            "assert": "not",
            "of": {
                "assert": "all_of",
                "of": [
                    {"assert": "node_has_value", "query": {"role": "textbox"}, "value": GHOST},
                ],
            },
        }
    ),
    "postcondition": lambda raw: raw["steps"][0].update(
        postcondition={"assert": "node_has_value", "query": {"role": "textbox"}, "value": GHOST}
    ),
    "checkpoint any_of": lambda raw: raw.update(
        checkpoint={
            "assert": "any_of",
            "of": [
                {"assert": "text_present", "value": "ok"},
                {"assert": "node_has_value", "query": {"role": "cell"}, "value": GHOST},
            ],
        }
    ),
    "outcome returns": lambda raw: raw["outcomes"][0]["returns"].update(extra=GHOST),
    "recovery run_capability inputs": lambda raw: raw["recovery"][1]["remedy"][0].update(
        inputs={"member_id": GHOST}
    ),
    "recovery detector": lambda raw: raw["recovery"][0].update(
        detect={"assert": "node_has_value", "query": {"role": "cell"}, "value": GHOST}
    ),
    "wait until": lambda raw: raw["steps"][0].update(
        wait={
            "for": "predicate",
            "until": {"assert": "node_has_value", "query": {"role": "cell"}, "value": GHOST},
        }
    ),
    "type action value": lambda raw: raw["steps"][0]["action"].update(value=GHOST),
}


@pytest.mark.parametrize("where", sorted(NESTED_GHOST_SITES))
def test_an_undeclared_input_is_found_wherever_it_hides(where: str) -> None:
    """The reference walker has to see through every container the schema can nest a value in.

    Missing one place means a flow that loads cleanly and then fails with an unbound reference in
    the middle of a replay -- the exact failure `_internally_consistent` exists to prevent.
    """
    raw = _raw()
    NESTED_GHOST_SITES[where](raw)
    with pytest.raises(ValidationError, match="undeclared input 'ghost'"):
        Capability.model_validate(raw)


def test_a_declared_input_referenced_deep_inside_is_accepted() -> None:
    """The control for the test above: the same nesting with a *declared* name is fine."""
    raw = _raw()
    raw["steps"][0]["precondition"] = {
        "assert": "not",
        "of": {
            "assert": "any_of",
            "of": [
                {
                    "assert": "node_has_value",
                    "query": {"role": "textbox"},
                    "value": {"$input": "member_id"},
                }
            ],
        },
    }
    assert Capability.model_validate(raw).steps[0].precondition is not None


def test_secret_references_need_no_declaration() -> None:
    """A secret is resolved at dispatch and is deliberately not part of the caller's contract,
    so naming one must not require an input for it -- that would put it in the tool schema."""
    raw = _raw()
    raw["steps"][0]["action"]["value"] = {"$secret": "core.operator_password"}
    cap = Capability.model_validate(raw)
    assert "core.operator_password" not in json.dumps(cap.tool_schema())


def test_a_value_carrying_two_reference_kinds_is_ambiguous_and_refused() -> None:
    raw = _raw()
    raw["steps"][0]["action"]["value"] = {"$input": "member_id", "$secret": "core.pw"}
    with pytest.raises(ValidationError):
        Capability.model_validate(raw)


# --------------------------------------------------------------- duplicates


def test_duplicate_outcome_codes_are_refused() -> None:
    raw = _raw()
    raw["outcomes"][1]["code"] = raw["outcomes"][0]["code"]
    with pytest.raises(ValidationError, match="outcome codes must be unique"):
        Capability.model_validate(raw)


def test_duplicate_recovery_rule_ids_are_refused() -> None:
    raw = _raw()
    raw["recovery"][1]["id"] = raw["recovery"][0]["id"]
    with pytest.raises(ValidationError, match="recovery rule ids must be unique"):
        Capability.model_validate(raw)


@pytest.mark.parametrize("side", ["inputs", "outputs"])
def test_duplicate_input_or_output_names_do_not_reach_the_tool_contract(side: str) -> None:
    """Step ids, outcome codes and recovery ids are checked for uniqueness; parameter names are
    not, and they are the one place a duplicate changes what a *calling agent* is told.

    Either refusing the artifact (the consistent fix) or emitting a valid schema satisfies this.
    """
    raw = _raw()
    twin = copy.deepcopy(raw[side][0])
    twin.pop("pattern", None)
    raw[side].append(twin)
    try:
        cap = Capability.model_validate(raw)
    except ValidationError:
        return

    schema = cap.tool_schema()
    required = schema["input_schema"]["required"]
    assert len(required) == len(set(required)), f"duplicate entries in required: {required}"
    names = [spec.name for spec in getattr(cap, side)]
    assert len(names) == len(set(names))


# ------------------------------------------------------------ sensitive flags


def _flagged(inputs: set[str], outputs: set[str]) -> Capability:
    raw = _raw()
    raw["inputs"].append({"name": "pin", "type": "string"})
    for spec in raw["inputs"]:
        spec["sensitive"] = spec["name"] in inputs
    for spec in raw["outputs"]:
        spec["sensitive"] = spec["name"] in outputs
    return Capability.model_validate(raw)


@pytest.mark.parametrize(
    ("inputs", "outputs", "expected"),
    [
        (set(), set(), frozenset()),
        ({"member_id"}, set(), frozenset({"member_id"})),
        (set(), {"savings_balance"}, frozenset({"savings_balance"})),
        ({"member_id", "pin"}, {"as_of"}, frozenset({"member_id", "pin", "as_of"})),
    ],
)
def test_sensitive_names_is_exactly_the_declared_set(
    inputs: set[str], outputs: set[str], expected: frozenset[str]
) -> None:
    """One declaration has to reach every sink, and the sinks all read this property. Anything
    declared sensitive that is missing here is a value sitting in the clear in evidence."""
    names = _flagged(inputs, outputs).sensitive_names
    assert names == expected
    assert isinstance(names, frozenset)


def test_a_name_sensitive_on_both_sides_is_reported_once() -> None:
    raw = _raw()
    raw["steps"][4]["action"]["into"] = "member_id"
    raw["outputs"][2]["name"] = "member_id"
    raw["inputs"][0]["sensitive"] = True
    raw["outputs"][2]["sensitive"] = True
    assert Capability.model_validate(raw).sensitive_names == frozenset({"member_id"})


# ------------------------------------------------------------------ max risk


@pytest.mark.parametrize(
    ("risks", "expected"),
    [
        (["safe"] * 5, ActionRisk.SAFE),
        (["safe", "elevated", "safe", "safe", "safe"], ActionRisk.ELEVATED),
        (["irreversible", "safe", "safe", "safe", "safe"], ActionRisk.IRREVERSIBLE),
        (["safe", "safe", "safe", "safe", "irreversible"], ActionRisk.IRREVERSIBLE),
        (["elevated", "irreversible", "elevated", "safe", "safe"], ActionRisk.IRREVERSIBLE),
    ],
)
def test_max_risk_is_the_riskiest_step_wherever_it_sits(
    risks: list[str], expected: ActionRisk
) -> None:
    """It drives the approval gate, so position must not matter: an irreversible step at the very
    end is as gated as one at the very start."""
    raw = _raw()
    for step, risk in zip(raw["steps"], risks, strict=True):
        step["risk"] = risk
    assert Capability.model_validate(raw).max_risk is expected


# ----------------------------------------------------------- content hashing


def _shuffled(node: Any, rng: random.Random) -> Any:
    if isinstance(node, dict):
        items = list(node.items())
        rng.shuffle(items)
        return {key: _shuffled(value, rng) for key, value in items}
    if isinstance(node, list):
        return [_shuffled(item, rng) for item in node]
    return node


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_hash_does_not_depend_on_mapping_key_order(seed: int) -> None:
    """Two authors writing the same flow with keys in a different order have written the same
    flow. A hash that told them apart would make every reformatting look like a new version."""
    baseline = Capability.model_validate(_raw()).compute_hash()
    shuffled = _shuffled(_raw(), random.Random(seed))
    assert Capability.model_validate(shuffled).compute_hash() == baseline


def test_hash_does_not_depend_on_yaml_layout() -> None:
    """Flow style, sorted keys, no comments, one giant line: still the same artifact."""
    baseline = Capability.model_validate(_raw()).compute_hash()

    flow = yaml.safe_dump(_raw(), default_flow_style=True, sort_keys=True, width=10**7)
    assert from_yaml(Capability, flow).compute_hash() == baseline

    noisy = "# leading comment\n\n" + yaml.safe_dump(_raw(), sort_keys=False) + "\n\n# trailing\n"
    assert from_yaml(Capability, noisy).compute_hash() == baseline


MUTATIONS: dict[str, Any] = {
    "title": lambda r: r.update(title=r["title"] + "!"),
    "description": lambda r: r.update(description="different"),
    "schema_version": lambda r: r.update(schema_version="1.0.1"),
    "step description": lambda r: r["steps"][0].update(description="different"),
    "step risk": lambda r: r["steps"][0].update(risk="elevated"),
    "wait timeout": lambda r: r["steps"][0]["wait"].update(timeout_ms=3001),
    "target hint": lambda r: r["steps"][0]["action"]["target"]["hints"].update(css="x"),
    "target anchor text": lambda r: r["steps"][0]["action"]["target"]["anchor"].update(text="x"),
    "input pattern": lambda r: r["inputs"][0].update(pattern="^[0-9]{4,11}$"),
    "input sensitive": lambda r: r["inputs"][0].update(sensitive=True),
    "output type": lambda r: r["outputs"][0].update(type="string"),
    "outcome terminal": lambda r: r["outcomes"][0].update(terminal=False),
    "outcome detector": lambda r: r["outcomes"][0]["detect"].update(value="Nothing here"),
    "recovery attempts": lambda r: r["recovery"][0].update(max_attempts=2),
    "recovery backoff": lambda r: r["recovery"][0].update(backoff="linear"),
    "escalation policy": lambda r: r["escalation"].update(policy="fail_closed"),
    "policy max_steps": lambda r: r["policy"].update(max_steps=19),
    "policy domains": lambda r: r["policy"].update(allowed_domains=["https://other.example"]),
    "entrypoint": lambda r: r["entrypoint"].update(url_pattern="{base_url}/elsewhere"),
    "provenance notes": lambda r: r["provenance"].update(notes="different"),
    "checkpoint": lambda r: r["checkpoint"]["of"].pop(),
}


def test_every_mutation_changes_the_hash_and_no_two_collide() -> None:
    """The hash is the audit trail's identity for what ran, so *every* behavioural or declarative
    field has to be inside it. A field that can change without moving the hash is a field an
    attacker -- or a careless edit -- can change without anyone noticing."""
    baseline = Capability.model_validate(_raw()).compute_hash()
    seen: dict[str, str] = {}
    for label, mutate in MUTATIONS.items():
        raw = _raw()
        mutate(raw)
        digest = Capability.model_validate(raw).compute_hash()
        assert digest != baseline, f"changing {label} did not change the content hash"
        assert digest not in seen.values(), f"{label} collides with another mutation"
        seen[label] = digest


def test_canonical_form_uses_wire_names_and_excludes_the_seal(capability: Capability) -> None:
    """Hashing the wire spelling (`$input`, `assert`, `for`) rather than the Python attribute
    names means renaming an attribute in the schema code cannot silently re-address every sealed
    artifact on disk."""
    canonical = capability.with_hash().canonical_bytes()
    assert b'"content_hash"' not in canonical
    assert b'"$input":"member_id"' in canonical
    assert b'"assert":"node_exists"' in canonical
    assert b'"for":"snapshot_stable"' in canonical
    assert b'"input_name"' not in canonical
    assert b" " not in canonical.replace(b"Look up a member's savings balance", b"")[:0]
    assert json.loads(canonical)["id"] == capability.id


def test_hash_is_sha256_lowercase_hex(capability: Capability) -> None:
    digest = capability.compute_hash()
    prefix, _, hexdigest = digest.partition(":")
    assert prefix == "sha256"
    assert len(hexdigest) == 64
    assert hexdigest == hexdigest.lower()
    int(hexdigest, 16)


def test_sealing_is_idempotent_and_ignores_a_stale_declared_hash(capability: Capability) -> None:
    sealed = capability.with_hash()
    assert sealed.with_hash() == sealed
    # A wrong declared hash must not feed back into the computation.
    stale = capability.model_copy(update={"content_hash": "sha256:" + "0" * 64})
    assert stale.compute_hash() == capability.compute_hash()
    assert not stale.hash_is_valid()


def test_an_unsealed_capability_is_not_valid_against_its_own_hash(capability: Capability) -> None:
    """`hash_is_valid` answers "is this sealed and intact", and unsealed is not sealed. The
    irreversible-approval gate leans on this to refuse a draft."""
    assert capability.content_hash == ""
    assert not capability.hash_is_valid()


def test_a_declared_hash_must_match_byte_for_byte(capability: Capability) -> None:
    """Case and whitespace variants of the right digest are refused: the comparison is exact, so
    there is exactly one spelling of "this artifact"."""
    good = capability.compute_hash()
    variants = [
        good.upper().replace("SHA256:", "sha256:"),
        " " + good,
        good + " ",
        good.replace("sha256:", "SHA256:"),
        good[:-1],
    ]
    for declared in variants:
        text = to_yaml(capability.model_copy(update={"content_hash": declared}))
        with pytest.raises(ValueError, match="content hash mismatch"):
            load_capability(text)


def test_hash_verification_can_be_waived_but_a_wrong_hash_is_otherwise_fatal(
    capability: Capability,
) -> None:
    wrong = capability.model_copy(update={"content_hash": "sha256:" + "f" * 64})
    with pytest.raises(ValueError, match="content hash mismatch"):
        load_capability(to_yaml(wrong))
    assert load_capability(to_yaml(wrong), verify_hash=False).content_hash == "sha256:" + "f" * 64


def test_the_mismatch_message_names_the_artifact_and_both_hashes(capability: Capability) -> None:
    """A failure has to be debuggable without reproducing it."""
    wrong = capability.model_copy(update={"content_hash": "sha256:" + "a" * 64})
    with pytest.raises(ValueError) as caught:
        load_capability(to_yaml(wrong))
    message = str(caught.value)
    assert capability.ref in message
    assert "sha256:" + "a" * 64 in message
    assert capability.compute_hash() in message


def test_visually_identical_unicode_forms_are_different_artifacts() -> None:
    """NFC and NFD spellings of the same word are different strings and must hash differently --
    normalizing them silently would let two distinct sealed documents claim one identity."""
    composed = _cap(title="café").compute_hash()
    decomposed = _cap(title="café").compute_hash()
    assert composed != decomposed


def test_in_place_mutation_of_a_nested_mapping_is_blocked_or_detected() -> None:
    """`frozen=True` is shallow: a `dict` field inside a frozen model can still be edited in place.

    Whatever the representation, the edit must not be *undetectable*: either it is refused, or the
    seal stops verifying. This is what keeps invariant 9 honest for mapping-typed fields.
    """
    sealed = Capability.model_validate(_raw()).with_hash()
    assert sealed.hash_is_valid()
    try:
        sealed.outcomes[0].returns["injected"] = "value"
        sealed.steps[0].action.target.hints["css"] = "input.evil"  # type: ignore[union-attr]
    except (TypeError, ValidationError, AttributeError):
        return
    assert not sealed.hash_is_valid()


# -------------------------------------------------------------- serde: round trips

AWKWARD_STRINGS = [
    "no",
    "yes",
    "on",
    "off",
    "null",
    "~",
    "true",
    "1.0",
    "00123",
    "1:30",
    "0x1F",
    "1e3",
    "2026-09-30",
    "",
    " leading",
    "trailing ",
    "multi\nline",
    "tab\there",
    "colon: inside",
    "# hash",
    "- dash",
    "{brace}",
    "[bracket]",
    "'single'",
    '"double"',
    "<<",
    "!tag",
    "&anchor",
    "*alias",
    "%directive",
    "---",
    "...",
    "\\",
    "émotion \U0001f389",
    "مرحبا بالعالم",  # Arabic, RTL
    "café",  # combining acute accent
    "​zero-width-space",
    "‮right-to-left override",
    " line separator",
    "﻿bom inside",
    "\x00nul",
    "a\r\nb",
]


@pytest.mark.parametrize("text", AWKWARD_STRINGS, ids=lambda s: repr(s)[:24])
def test_awkward_strings_survive_a_seal_dump_load_cycle(text: str) -> None:
    """Every place a string can live -- a title, a step description, an outcome code, a literal
    typed into a field -- has to come back as the same string with a hash that still verifies.

    YAML 1.1 has more ways to turn a string into something else than any format a human edits:
    `no`, `~`, `1:30`, `0x1F`, `2026-09-30`, a leading colon. The dumper must quote them and the
    strict loader must not reinterpret them.
    """
    raw = _raw()
    raw["title"] = text
    raw["steps"][0]["description"] = text
    raw["steps"][0]["action"]["value"] = text
    raw["outcomes"][0]["code"] = f"code_{text}"
    raw["outcomes"][0]["returns"]["literal"] = text
    original = Capability.model_validate(raw)

    reloaded = load_capability(dump_capability(original))

    assert reloaded.title == text
    assert reloaded.steps[0].description == text
    assert reloaded.steps[0].action.value == text  # type: ignore[union-attr]
    assert reloaded.outcomes[0].code == f"code_{text}"
    assert reloaded.outcomes[0].returns["literal"] == text
    assert reloaded.hash_is_valid()
    assert reloaded.content_hash == original.compute_hash()


def test_a_next_line_character_survives_a_seal_dump_load_cycle() -> None:
    """Legacy mainframe-fronted pages do emit U+0085. A discovery run that compiles such a name
    into a target produces an artifact this system then refuses to load."""
    original = _cap(title="before\x85after")
    reloaded = load_capability(dump_capability(original))
    assert reloaded.title == "before\x85after"


def test_dump_is_a_fixed_point_after_one_generation(capability: Capability) -> None:
    """dump -> load -> dump must not keep changing. Otherwise every re-save of a reviewed artifact
    produces a diff that has nothing to do with the review."""
    first = dump_capability(capability)
    second = dump_capability(load_capability(first))
    assert first == second


def test_a_large_flow_round_trips_with_a_verifying_seal() -> None:
    raw = _raw()
    template = raw["steps"][0]
    for i in range(200):
        step = copy.deepcopy(template)
        step["id"] = f"bulk_step_{i:04d}"
        raw["steps"].append(step)
    raw["policy"]["max_steps"] = 1000
    original = Capability.model_validate(raw)

    reloaded = load_capability(dump_capability(original))

    assert len(reloaded.steps) == 205
    assert [s.id for s in reloaded.steps] == [s.id for s in original.steps]
    assert reloaded.content_hash == original.compute_hash()


def test_a_very_long_description_round_trips_exactly() -> None:
    """The dumper folds long scalars at 100 columns and the loader folds them back; a mistake in
    either direction corrupts prose silently."""
    description = " ".join(f"word{i}" for i in range(30_000))
    original = _cap(description=description)
    reloaded = load_capability(dump_capability(original))
    assert reloaded.description == description
    assert reloaded.hash_is_valid()


def test_crlf_and_bom_do_not_change_what_a_document_says() -> None:
    """Artifacts are hand-edited on machines that disagree about line endings."""
    sealed = dump_capability(Capability.model_validate(_raw()))
    assert load_capability(sealed.replace("\n", "\r\n")).hash_is_valid()
    assert load_capability("﻿" + sealed).hash_is_valid()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw["surface"].update(driver_capabilities=[]),
        lambda raw: raw["escalation"].update(triggers=[]),
        lambda raw: raw["inputs"][0].update(enum=[]),
    ],
    ids=["driver_capabilities=()", "escalation.triggers=()", "input.enum=()"],
)
def test_a_declared_empty_collection_round_trips(mutate: Any) -> None:
    """An explicit empty tuple is a *declaration* -- "requires no driver capability", "never
    escalates" -- and it differs from the field's default. Pruning it on dump turns it back into
    the default on load (so `triggers: []` silently becomes five triggers) and the sealed hash no
    longer matches, so an honest artifact cannot be reloaded."""
    raw = _raw()
    mutate(raw)
    original = Capability.model_validate(raw)
    reloaded = load_capability(dump_capability(original))
    assert reloaded.model_dump() == original.with_hash().model_dump()


@pytest.mark.parametrize("pattern", ["[", "(unclosed", "*leading-star", "(?P<n>a)(?P<n>b)"])
@pytest.mark.parametrize("site", ["input", "predicate"])
def test_an_uncompilable_regex_is_rejected_when_the_artifact_loads(site: str, pattern: str) -> None:
    """`_internally_consistent` exists to turn mid-replay surprises into load-time errors. A
    pattern that cannot compile is the purest example: it passes every schema check and then
    raises `re.error` -- not `INPUT_VALIDATION_FAILED`, not a typed failure -- on the first call."""
    raw = _raw()
    if site == "input":
        raw["inputs"][0]["pattern"] = pattern
    else:
        raw["checkpoint"]["of"][1]["query"]["name_matches"] = pattern
    with pytest.raises(ValidationError):
        Capability.model_validate(raw)


# ------------------------------------------------------------- serde: hostile


def test_python_object_tags_are_refused_not_executed() -> None:
    """A capability file is data. `!!python/object/apply` is the classic way to make a YAML loader
    run code, and the loader has to be a safe one."""
    hostile = "!!python/object/apply:os.system ['echo pwned']"
    with pytest.raises(yaml.YAMLError):
        load_capability(hostile)


@pytest.mark.parametrize("document", ["", "null", "42", "- a\n- b", '"just a string"'])
def test_a_document_that_is_not_a_mapping_is_a_validation_error(document: str) -> None:
    with pytest.raises(ValidationError):
        load_capability(document)


def test_unparseable_yaml_never_yields_a_partial_capability() -> None:
    with pytest.raises(yaml.YAMLError):
        load_capability("{id: unterminated")


def test_a_bare_on_key_survives_loading() -> None:
    """YAML 1.1 would turn `on` into True; the strict loader keeps it a string, so an outcome code
    of `no` or `on` is still the string the author wrote."""
    text = dump_capability(_cap())
    raw = yaml.safe_load(text)
    raw["outcomes"][0]["code"] = "no"
    raw["outcomes"][1]["code"] = "on"
    raw.pop("content_hash")
    cap = from_yaml(Capability, yaml.safe_dump(raw).replace("code: 'no'", "code: no"))
    assert {o.code for o in cap.outcomes} >= {"no", "on"}


# -------------------------------------------------------------- tool contract


@pytest.mark.parametrize(
    ("value_type", "json_type", "fmt"),
    [
        ("string", "string", None),
        ("integer", "integer", None),
        ("number", "number", None),
        ("boolean", "boolean", None),
        ("money", "string", "money"),
        ("date", "string", "date"),
        ("enum", "string", None),
    ],
)
def test_every_value_type_maps_to_one_json_schema_type(
    value_type: str, json_type: str, fmt: str | None
) -> None:
    """`money` and `date` are first-class so a caller does not guess what "$4,210.55" is -- they
    surface as a string *with a format*, for inputs and outputs alike."""
    for schema in (
        InputSpec(name="x", type=value_type).json_schema(),  # type: ignore[arg-type]
        OutputSpec(name="x", type=value_type).json_schema(),  # type: ignore[arg-type]
    ):
        assert schema["type"] == json_type
        assert schema.get("format") == fmt


def test_json_schema_carries_only_what_was_declared() -> None:
    bare = InputSpec(name="x").json_schema()
    assert bare == {"type": "string"}

    full = InputSpec(
        name="x",
        description="d",
        pattern="^a+$",
        enum=("a", "aa"),
        example="a",
    ).json_schema()
    assert full == {
        "type": "string",
        "description": "d",
        "pattern": "^a+$",
        "enum": ["a", "aa"],
        "examples": ["a"],
    }


def test_a_unicode_pattern_is_exported_verbatim() -> None:
    pattern = "^[٠-٩]{4,10}$"
    assert InputSpec(name="x", pattern=pattern).json_schema()["pattern"] == pattern


def test_tool_name_replaces_every_dot_and_nothing_else() -> None:
    cap = _cap(id="vendor-x.sub.domain.do_thing")
    assert cap.tool_schema()["name"] == "vendor-x_sub_domain_do_thing"


def test_tool_description_falls_back_to_the_title() -> None:
    assert _cap(description="").tool_schema()["description"] == _cap().title


def test_optional_inputs_are_declared_but_not_required() -> None:
    raw = _raw()
    raw["inputs"].append({"name": "note", "type": "string", "required": False})
    schema = Capability.model_validate(raw).tool_schema()["input_schema"]
    assert list(schema["properties"]) == ["member_id", "note"]
    assert schema["required"] == ["member_id"]


def test_a_capability_with_no_inputs_still_exports_a_closed_object_schema() -> None:
    raw = _raw()
    raw["inputs"] = []
    # The fixture's steps and outcomes reference `member_id`; a parameterless flow does not.
    raw["steps"][0]["action"]["value"] = "fixed"
    raw["steps"][0]["postcondition"]["value"] = "fixed"
    for outcome in raw["outcomes"]:
        outcome["returns"] = {}
    schema = Capability.model_validate(raw).tool_schema()["input_schema"]
    assert schema == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }


def test_outputs_are_not_all_promised_because_a_business_outcome_returns_none() -> None:
    """ "No such member" is a legitimate answer with no balance in it, so the output schema must
    not claim every declared output is always present."""
    out = _cap().tool_schema()["output_schema"]
    assert "required" not in out
    assert set(out["properties"]) == {"savings_balance", "account_status", "as_of"}


def test_outcome_descriptions_fall_back_to_their_code() -> None:
    raw = _raw()
    raw["outcomes"][0]["description"] = ""
    outcomes = {
        o["code"]: o["description"]
        for o in Capability.model_validate(raw).tool_schema()["outcomes"]
    }
    assert outcomes["member_not_found"] == "member_not_found"


def test_tool_schema_is_json_serialisable_and_survives_a_round_trip(capability: Capability) -> None:
    """The agent-facing contract must be identical whether it is exported from the in-memory
    artifact or from the one on disk -- otherwise there are two contracts."""
    direct = json.dumps(capability.tool_schema(), sort_keys=True)
    via_disk = json.dumps(
        load_capability(dump_capability(capability)).tool_schema(), sort_keys=True
    )
    assert direct == via_disk


# --------------------------------------------------------- exported JSON Schema


def _json_schema(model: Any) -> dict[str, Any]:
    with warnings.catch_warnings():
        # Recursive predicate unions make pydantic warn about a skipped discriminator. The warning
        # is about the export's precision, not about what these tests defend.
        warnings.simplefilter("ignore")
        return model.model_json_schema()


def test_exported_schema_is_deterministic() -> None:
    first = json.dumps(_json_schema(Capability), sort_keys=True)
    second = json.dumps(_json_schema(Capability), sort_keys=True)
    assert first == second


def test_exported_schema_is_closed_everywhere() -> None:
    """`additionalProperties: false` on every object, so a generic JSON Schema validator reaches
    the same verdict on a typo as the pydantic models do."""
    schema = _json_schema(Capability)
    assert schema["additionalProperties"] is False
    open_objects = [
        name
        for name, definition in schema["$defs"].items()
        if definition.get("type") == "object"
        and definition.get("additionalProperties") is not False
    ]
    assert open_objects == []


def test_exported_schema_top_level_shape_is_stable() -> None:
    schema = _json_schema(Capability)
    assert set(schema["required"]) == {
        "id",
        "version",
        "title",
        "surface",
        "entrypoint",
        "steps",
        "checkpoint",
    }
    assert set(schema["properties"]) == {
        "schema_version",
        "id",
        "version",
        "title",
        "description",
        "surface",
        "entrypoint",
        "inputs",
        "outputs",
        "steps",
        "checkpoint",
        "outcomes",
        "recovery",
        "escalation",
        "policy",
        "provenance",
        "content_hash",
    }


def test_the_checkpoint_is_required_by_the_schema_export_as_well_as_the_validator() -> None:
    """A capability without a success condition "can only tell you it finished"."""
    raw = _raw()
    del raw["checkpoint"]
    with pytest.raises(ValidationError):
        Capability.model_validate(raw)
    assert "checkpoint" in _json_schema(Capability)["required"]


def test_the_wire_spelling_of_the_predicate_discriminator_is_assert() -> None:
    from pydantic import TypeAdapter

    from cua.domain.predicates import Predicate

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        schema = TypeAdapter(Predicate).json_schema()
    assert schema["discriminator"]["propertyName"] == "assert"
