"""Finding a capability by name.

The half of the thesis that discovery and replay do not cover. The brief's framing is "the model
discovers, the artifact becomes a reusable capability, **deterministic replay is how the AI agent
invokes it in production**" -- and an agent cannot invoke what it cannot look up.

Files on disk, not a database. A capability is a reviewable document that belongs in version
control; putting it behind a service would add an operational dependency to buy nothing this project
needs. `CapabilityStore` is the seam where a real registry would go, and its interface is
deliberately the three operations that would survive that move: list, resolve, load.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cua.catalog.approvals import ApprovalStore
from cua.domain.approval import ApprovalState
from cua.domain.capability import Capability
from cua.domain.serde import load_capability

DEFAULT_ROOT = Path("evidence/capabilities")


class CapabilityNotFoundError(LookupError):
    """No capability matched.

    Carries what *was* available, so the caller can say something more useful than "not found" --
    including files that could not be parsed, so a typo and a broken artifact do not produce the
    same message.
    """

    def __init__(self, wanted: str, available: list[str]) -> None:
        self.wanted = wanted
        self.available = available
        listing = ", ".join(available) if available else "nothing"
        super().__init__(f"no capability matching {wanted!r}; the catalog holds {listing}")


class CapabilityTamperedError(ValueError):
    """The declared content hash no longer covers the content.

    Refused rather than warned about. A catalog that serves capabilities it has not verified undoes
    the reason they are content-addressed: "which version ran?" is only answerable if the version
    that ran is the version that was reviewed.
    """


@dataclass(frozen=True)
class CatalogEntry:
    """One capability, as the catalog lists it."""

    capability: Capability
    path: Path
    approval: ApprovalState | None = None
    """Review state, filled in by `CapabilityStore.list()`.

    A second axis, not a refinement of `state` below. Integrity asks whether the bytes still
    match the hash; approval asks whether a person signed this version off for unattended
    replay. A sealed artifact can be unapproved, and an approved one can later be tampered
    with -- collapsing them into one word would hide whichever mattered.
    """

    @property
    def ref(self) -> str:
        return self.capability.ref

    @property
    def state(self) -> str:
        """`sealed`, `draft`, or `TAMPERED`.

        A tampered artifact is still *listed*. Hiding it would be the wrong failure: an operator
        whose artifact has been edited on disk should be told exactly that, not told it does not
        exist. It is refused at `load()` -- the point where serving it would do harm.

        `draft` is a capability with no hash at all -- a hand-authored artifact nobody has sealed
        yet. A legitimate state, so it loads, but never allowed to look identical to a sealed one.
        Note this axis is integrity, not review: `CapabilityApproval` is what gates unattended
        replay, and a sealed artifact can still be unapproved.
        """
        if not self.capability.content_hash:
            return "draft"
        return "sealed" if self.capability.hash_is_valid() else "TAMPERED"

    @property
    def signature(self) -> str:
        """The typed call signature, for a listing a human reads."""
        inputs = ", ".join(
            f"{spec.name}: {spec.type}" + ("" if spec.required else " = None")
            for spec in self.capability.inputs
        )
        outputs = ", ".join(f"{spec.name}: {spec.type}" for spec in self.capability.outputs)
        return f"{self.capability.id}({inputs}) -> {{{outputs}}}"


@dataclass
class CapabilityStore:
    """Capabilities on disk, addressed by `id` or `id@version`."""

    root: Path = DEFAULT_ROOT
    approvals: ApprovalStore | None = None
    """Where review state is read from. Defaults to beside the artifacts."""

    unreadable: list[tuple[Path, str]] = field(default_factory=list)
    """Files that could not be parsed at all, from the most recent `list()`."""

    def list(self) -> list[CatalogEntry]:
        """Every capability in the catalog, oldest version first within an id.

        Parsed **without** hash verification on purpose, so a tampered artifact appears in the
        listing marked `TAMPERED` rather than silently vanishing. Verification happens at `load()`.

        A file that does not parse at all is a different case -- there is no id to list it under --
        so it goes to `unreadable` and is named if a lookup then fails.
        """
        entries: list[CatalogEntry] = []
        self.unreadable = []
        if not self.root.exists():
            return entries

        for path in sorted(self.root.glob("*.yaml")):
            try:
                capability = load_capability(path.read_text("utf-8"), verify_hash=False)
            except Exception as exc:
                self.unreadable.append((path, str(exc).splitlines()[0]))
                continue
            store = self.approvals or ApprovalStore(root=self.root)
            entries.append(
                CatalogEntry(
                    capability,
                    path,
                    # Verified hash, not the declared one. The declared hash is a line in a
                    # file the editor also controls: comparing against it meant an edited
                    # artifact still read as `approved`, which is the precise hole the pinning
                    # exists to close. An artifact whose bytes no longer match cannot be
                    # approved, because nobody knows what content was reviewed.
                    approval=store.state_for(
                        capability.ref,
                        content_hash=(
                            capability.content_hash if capability.hash_is_valid() else ""
                        ),
                    ),
                )
            )

        entries.sort(key=lambda e: (e.capability.id, _version_key(e.capability.version)))
        return entries

    def resolve(self, wanted: str) -> CatalogEntry:
        """Find `id` (highest version) or an exact `id@version`.

        A bare id resolves to the newest version, because that is what asking for a capability by
        name means. Pinning stays available and stays exact.
        """
        entries = self.list()
        if "@" in wanted:
            matches = [e for e in entries if e.ref == wanted]
        else:
            matches = [e for e in entries if e.capability.id == wanted]

        if not matches:
            available = [e.ref for e in entries]
            available += [f"{path.name} (unreadable: {why})" for path, why in self.unreadable]
            raise CapabilityNotFoundError(wanted, available)
        return matches[-1]

    def load(self, wanted: str) -> Capability:
        """Resolve and verify. A tampered artifact is refused, never served."""
        entry = self.resolve(wanted)
        if entry.state == "TAMPERED":
            raise CapabilityTamperedError(
                f"{entry.ref} does not match its content hash ({entry.path}) -- it was edited "
                "after it was sealed, so what would run is not what was reviewed"
            )
        return entry.capability


def _version_key(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:  # pragma: no cover -- the schema validates semver on load
        return (0,)
