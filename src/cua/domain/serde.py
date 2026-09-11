"""YAML serialization for domain documents.

Pure by design: these functions take and return strings. Reading and writing files happens in
`cua.catalog`, so `cua.domain` stays free of I/O and the artifact schema remains testable without
a filesystem.

YAML rather than JSON because a capability is a **reviewed** document. Diffing a flow change in a
pull request should show the step that changed, not a reflowed line of JSON, and a reviewer should
be able to read it without tooling.
"""

from __future__ import annotations

from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

from cua.domain.capability import Capability

_Model = TypeVar("_Model", bound=BaseModel)


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader without YAML 1.1's boolean coercion of on/off/yes/no.

    YAML 1.1 resolves a bare `on`, `off`, `yes` or `no` -- as a key or a value -- to a boolean. In a
    document humans hand-edit, that is a landmine: an outcome code of `no` becomes `False`, and a
    key named `on` becomes `True`, and both surface as a confusing schema error far from the cause.
    (Same family as the "Norway problem", where the country code NO becomes False.)

    Only `true`/`false` remain booleans here. Anything else stays the string it looks like.
    """


_AMBIGUOUS = ["y", "Y", "n", "N", "o", "O"]
_StrictLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    if key in _AMBIGUOUS
    else resolvers
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_StrictLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    __import__("re").compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


class _BlockStyleDumper(yaml.SafeDumper):
    """Indent list items under their key, which is what most reviewers expect to read."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow=flow, indentless=False)


def _prune(value: Any) -> Any:
    """Drop nulls and empty containers.

    An artifact should show what it *does*, not every field it declined to use. A page of
    `precondition: null` is noise that makes the real content harder to review.
    """
    if isinstance(value, dict):
        cleaned = {k: _prune(v) for k, v in value.items() if v is not None}
        return {k: v for k, v in cleaned.items() if v != {} and v != []}
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


def to_dict(model: BaseModel, *, prune: bool = True) -> dict[str, Any]:
    payload = model.model_dump(by_alias=True, mode="json")
    return _prune(payload) if prune else payload


def to_yaml(model: BaseModel, *, prune: bool = True) -> str:
    return yaml.dump(
        to_dict(model, prune=prune),
        Dumper=_BlockStyleDumper,
        sort_keys=False,
        default_flow_style=False,
        width=100,
        allow_unicode=True,
    )


def from_yaml(model_type: type[_Model], text: str) -> _Model:
    return model_type.model_validate(yaml.load(text, Loader=_StrictLoader))


def load_capability(text: str, *, verify_hash: bool = True) -> Capability:
    """Parse a capability, verifying its content hash.

    Hash verification is on by default because an artifact is an executable contract. Silently
    running a tampered or hand-edited one -- and then recording a `RunRecord` that names a version
    whose content no longer matches -- would make the audit trail a fiction.

    A capability with no hash at all is accepted: that is a hand-authored draft, not a tampered
    document. A capability with a *wrong* hash is rejected.
    """
    capability = from_yaml(Capability, text)
    if verify_hash and capability.content_hash and not capability.hash_is_valid():
        raise ValueError(
            f"content hash mismatch for {capability.ref}: "
            f"declared {capability.content_hash}, computed {capability.compute_hash()}. "
            "The artifact was modified after it was sealed."
        )
    return capability


def dump_capability(capability: Capability) -> str:
    """Serialize a capability, sealing it with a freshly computed hash."""
    return to_yaml(capability.with_hash())
