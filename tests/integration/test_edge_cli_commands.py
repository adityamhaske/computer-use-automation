"""The command line, exercised through its real entry point.

Everything else in the suite reaches the system through its Python API. This file drives `cua` the
way a person does -- through Typer -- so argument parsing, exit codes and the wording of every error
are tested, not assumed. The point of an operator-facing CLI is that a mistake produces a sentence
and a meaningful exit code, never a stack trace: a traceback reads as a defect in the system.

Exit-code contract being defended (see `RunResult.exit_code` and the command docstrings):
0 an answer (including a negative one), 1 a defect or an unreachable target, 2 a usage error, a
refusal by policy, or a session that needs a human.
"""

from __future__ import annotations

import ast
import json
import socket
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import cua
from cua.cli.main import _friendly_target_errors, _parse_inputs, app
from cua.cli.mock_login import TargetUnreachableError
from cua.runtime.dispatcher import EntrypointUnreachableError, NavigationBlockedError

ROOT = Path(__file__).resolve().parents[2]
CAPABILITY = ROOT / "evidence/capabilities/corebank.member.savings_balance@1.0.0.yaml"
REF = "corebank.member.savings_balance"

runner = CliRunner()


@pytest.fixture
def in_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    """The catalog and the policy file are found relative to the working directory."""
    monkeypatch.chdir(ROOT)


@pytest.fixture
def in_scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working directory of its own, so a replay's evidence never lands in the repository."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CUA_POLICY_FILE", str(ROOT / "config/policy.yaml"))
    return tmp_path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _no_traceback(output: str) -> None:
    assert "Traceback" not in output, output
    assert "most recent call last" not in output, output


# ------------------------------------------------------------------------- the surface


def test_version_prints_the_package_version_and_exits_zero() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.output.strip() == cua.__version__


def test_help_lists_every_command_a_reader_is_told_about() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in (
        "discover",
        "replay",
        "console",
        "demo",
        "eval",
        "codegen",
        "agent-demo",
        "catalog",
        "version",
    ):
        assert command in result.output, command


@pytest.mark.parametrize(
    "command",
    [
        ["discover"],
        ["replay"],
        ["console"],
        ["codegen"],
        ["agent-demo"],
        ["catalog", "show"],
        ["catalog", "invoke"],
        ["catalog", "approve"],
    ],
    ids=lambda c: " ".join(c),
)
def test_every_command_documents_itself(command: list[str]) -> None:
    """`--help` must work for each command, which also proves its signature survives the
    `_friendly_target_errors` wrapper (Typer reads the options off the wrapped function)."""
    result = runner.invoke(app, [*command, "--help"])

    assert result.exit_code == 0, result.output
    assert "Usage" in result.output
    _no_traceback(result.output)


def test_an_unknown_command_is_a_usage_error_not_a_crash() -> None:
    result = runner.invoke(app, ["definitely-not-a-command"])

    assert result.exit_code == 2
    _no_traceback(result.output)


# ------------------------------------------------------------------------- the catalog


@pytest.mark.usefixtures("in_repo")
def test_catalog_list_shows_the_reference_capability_and_its_integrity() -> None:
    result = runner.invoke(app, ["catalog", "list"])

    assert result.exit_code == 0, result.output
    assert f"{REF}@1.0.0" in result.output
    assert "sealed" in result.output
    _no_traceback(result.output)


@pytest.mark.usefixtures("in_repo")
def test_catalog_show_prints_the_artifact_and_resolves_a_bare_id() -> None:
    result = runner.invoke(app, ["catalog", "show", REF])

    assert result.exit_code == 0, result.output
    assert "member_id" in result.output
    assert "content_hash" in result.output


@pytest.mark.usefixtures("in_repo")
def test_catalog_show_tool_schema_is_json_an_agent_can_load() -> None:
    result = runner.invoke(app, ["catalog", "show", REF, "--tool-schema"])

    assert result.exit_code == 0, result.output
    schema = json.loads(result.output)
    assert schema["name"] == REF.replace(".", "_")
    assert "member_id" in schema["input_schema"]["properties"]
    assert schema["input_schema"]["required"] == ["member_id"]


@pytest.mark.usefixtures("in_repo")
@pytest.mark.parametrize(
    "ref",
    [
        pytest.param("no.such.capability", id="unknown"),
        pytest.param("no.such.capability@9.9.9", id="unknown-version"),
        pytest.param("../../etc/passwd", id="traversal"),
        pytest.param("..\\..\\windows\\system32", id="backslash-traversal"),
        pytest.param("", id="empty"),
        pytest.param("id with spaces", id="spaces"),
        pytest.param("x" * 5_000, id="long"),
    ],
)
def test_catalog_show_of_something_that_is_not_there_is_a_clean_refusal(ref: str) -> None:
    result = runner.invoke(app, ["catalog", "show", ref])

    assert result.exit_code == 2, result.output
    _no_traceback(result.output)


# ------------------------------------------------------------------------- codegen


@pytest.mark.usefixtures("in_repo")
def test_codegen_writes_a_test_that_is_valid_python(tmp_path: Path) -> None:
    out = tmp_path / "generated" / "test_member.py"

    result = runner.invoke(app, ["codegen", REF, "--input", "member_id=12345", "--out", str(out)])

    assert result.exit_code == 0, result.output
    source = out.read_text(encoding="utf-8")
    ast.parse(source)
    assert "12345" in source
    assert "playwright" in source.lower()


@pytest.mark.usefixtures("in_repo")
def test_codegen_for_an_unknown_capability_is_refused_without_writing_anything(
    tmp_path: Path,
) -> None:
    out = tmp_path / "test_nothing.py"

    result = runner.invoke(app, ["codegen", "no.such.capability", "--out", str(out)])

    assert result.exit_code == 2
    assert not out.exists()
    _no_traceback(result.output)


# ------------------------------------------------------------------------- input parsing


@pytest.mark.parametrize(
    ("pairs", "expected"),
    [
        pytest.param(None, {}, id="none"),
        pytest.param([], {}, id="empty-list"),
        pytest.param(["member_id=12345"], {"member_id": "12345"}, id="one"),
        pytest.param(["a=1", "b=2"], {"a": "1", "b": "2"}, id="two"),
        pytest.param(["note=a=b=c"], {"note": "a=b=c"}, id="equals-in-value"),
        pytest.param(["empty="], {"empty": ""}, id="empty-value"),
        pytest.param(["u=١٢٣"], {"u": "١٢٣"}, id="unicode"),
        pytest.param(["spaced= padded "], {"spaced": " padded "}, id="spaces"),
    ],
)
def test_input_pairs_split_on_the_first_equals_and_keep_the_value_verbatim(
    pairs: list[str] | None, expected: dict[str, str]
) -> None:
    assert _parse_inputs(pairs) == expected


@pytest.mark.parametrize(
    "pairs",
    [
        pytest.param(["oops"], id="no-equals"),
        pytest.param(["=x"], id="empty-name"),
        pytest.param(["  =x"], id="blank-name"),
        pytest.param(["a=1", "a=2"], id="duplicate"),
        pytest.param(["a=1", "b", "c=3"], id="one-bad-among-good"),
    ],
)
def test_a_malformed_input_pair_is_a_usage_error(pairs: list[str]) -> None:
    with pytest.raises(typer.Exit) as stopped:
        _parse_inputs(pairs)

    assert stopped.value.exit_code == 2


# ------------------------------------------------------------------------- replay: refusals


def test_replay_of_a_file_that_does_not_exist_is_a_usage_error() -> None:
    result = runner.invoke(app, ["replay", "no-such-file.yaml"])

    assert result.exit_code == 2
    _no_traceback(result.output)


def test_replay_of_a_directory_is_a_usage_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["replay", str(tmp_path)])

    assert result.exit_code == 2
    _no_traceback(result.output)


def test_sign_in_without_a_base_url_says_what_is_missing() -> None:
    result = runner.invoke(app, ["replay", str(CAPABILITY), "--sign-in"])

    assert result.exit_code == 2
    assert "--base-url" in result.output


def test_a_malformed_input_pair_stops_replay_before_anything_is_launched() -> None:
    result = runner.invoke(app, ["replay", str(CAPABILITY), "--input", "oops"])

    assert result.exit_code == 2
    assert "name=value" in result.output
    _no_traceback(result.output)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("a: [unclosed", id="malformed-yaml"),
        pytest.param("", id="empty-file"),
        pytest.param("- just\n- a\n- list\n", id="a-list"),
        pytest.param("foo: bar\n", id="not-a-capability"),
        pytest.param("\x01\x02\x03", id="binary-noise"),
    ],
)
def test_replay_of_a_document_that_is_not_a_capability_is_refused_cleanly(
    tmp_path: Path, body: str
) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text(body, encoding="utf-8")

    result = runner.invoke(app, ["replay", str(broken)])

    assert result.exit_code == 2, result.output
    assert "cannot run" in result.output
    _no_traceback(result.output)


def test_replay_of_an_artifact_edited_after_it_was_sealed_is_refused(tmp_path: Path) -> None:
    """The content hash is the whole point of sealing: a one-word edit must not run."""
    original = CAPABILITY.read_text(encoding="utf-8")
    assert "Look up a member" in original or "Given a member number" in original
    marker = "Look up a member" if "Look up a member" in original else "Given a member number"
    tampered = tmp_path / "tampered.yaml"
    tampered.write_text(original.replace(marker, marker + " (edited)", 1), encoding="utf-8")

    result = runner.invoke(app, ["replay", str(tampered), "--input", "member_id=12345"])

    assert result.exit_code == 2, result.output
    assert "content hash mismatch" in result.output
    _no_traceback(result.output)


@pytest.mark.usefixtures("in_repo")
def test_catalog_invoke_validates_its_arguments_before_acting() -> None:
    sign_in_alone = runner.invoke(app, ["catalog", "invoke", REF, "--sign-in"])
    unknown = runner.invoke(app, ["catalog", "invoke", "no.such.capability"])
    bad_pair = runner.invoke(app, ["catalog", "invoke", REF, "--input", "oops"])

    for result in (sign_in_alone, unknown, bad_pair):
        assert result.exit_code == 2, result.output
        _no_traceback(result.output)


def test_discover_without_a_model_key_says_so_instead_of_starting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery is the only command that needs a model; without a key it must stop at the door."""
    monkeypatch.setattr("cua.cli.main.load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    result = runner.invoke(
        app, ["discover", "--goal", "anything", "--target", "http://127.0.0.1:1"]
    )

    assert result.exit_code == 2, result.output
    assert "OMNIROUTE_API_KEY" in result.output
    _no_traceback(result.output)


# ------------------------------------------------------------------------- the error wrapper


def _wrapped(error: BaseException | None, *, exit_code: int | None = None) -> typer.Typer:
    mini = typer.Typer()

    @mini.command()
    @_friendly_target_errors
    def run(name: str = "x", flag: bool = False) -> None:
        if error is not None:
            raise error
        if exit_code is not None:
            raise typer.Exit(code=exit_code)
        typer.echo(f"ran {name} {flag}")

    @mini.command()
    def other() -> None:  # a second command so Typer keeps `run` as a named subcommand
        typer.echo("other")

    return mini


@pytest.mark.parametrize(
    ("error", "code", "words"),
    [
        pytest.param(
            TargetUnreachableError("http://x -- refused"), 1, "Could not reach the target", id="a"
        ),
        pytest.param(
            EntrypointUnreachableError("could not open http://x"),
            1,
            "Could not reach the target",
            id="b",
        ),
        pytest.param(
            NavigationBlockedError("http://evil is not on the allowlist"),
            2,
            "Refused by policy",
            id="c",
        ),
    ],
)
def test_a_target_problem_becomes_a_sentence_and_the_right_exit_code(
    error: BaseException, code: int, words: str
) -> None:
    result = runner.invoke(_wrapped(error), ["run"])

    assert result.exit_code == code, result.output
    assert words in result.output
    _no_traceback(result.output)


def test_the_wrapper_does_not_swallow_a_commands_own_exit_code() -> None:
    """Replay exits 0/1/2 by result status; the wrapper sits above that and must not reinterpret."""
    for code in (0, 1, 2, 7):
        assert runner.invoke(_wrapped(None, exit_code=code), ["run"]).exit_code == code


def test_the_wrapper_does_not_hide_a_genuine_defect() -> None:
    """Only the three target conditions are friendly; anything else is a bug and must surface."""
    result = runner.invoke(_wrapped(RuntimeError("a real bug")), ["run"])

    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)


def test_the_wrapper_keeps_the_commands_options_working() -> None:
    result = runner.invoke(_wrapped(None), ["run", "--name", "ada", "--flag"])

    assert result.exit_code == 0
    assert "ran ada True" in result.output


# ------------------------------------------------------------------------- a real browser


@pytest.mark.browser
@pytest.mark.slow
@pytest.mark.usefixtures("in_scratch")
def test_replay_against_a_target_that_is_not_running_is_a_clean_error() -> None:
    """The failure a presenter hits when the app was never started: a sentence, not a traceback."""
    port = _free_port()

    result = runner.invoke(
        app,
        [
            "replay",
            str(CAPABILITY),
            "--input",
            "member_id=12345",
            "--base-url",
            f"http://127.0.0.1:{port}",
            "--sign-in",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "Could not reach the target" in result.output
    assert f"127.0.0.1:{port}" in result.output
    assert "make app" in result.output
    _no_traceback(result.output)


@pytest.mark.browser
@pytest.mark.slow
@pytest.mark.usefixtures("in_scratch")
@pytest.mark.parametrize(
    "value",
    [
        pytest.param("not-a-member", id="text"),
        pytest.param("123", id="too-short"),
        pytest.param("12345678901", id="too-long"),
        pytest.param("", id="empty"),
        pytest.param(" 12345", id="leading-space"),
        pytest.param("12345\n", id="trailing-newline"),
        pytest.param("١٢٣٤٥", id="arabic-digits"),
    ],
)
def test_a_value_outside_the_declared_pattern_fails_before_the_browser_moves(value: str) -> None:
    """Exit 1, `input_validation_failed`, and the run record shows zero dispatches."""
    result = runner.invoke(
        app,
        [
            "replay",
            str(CAPABILITY),
            "--input",
            f"member_id={value}",
            "--base-url",
            "http://127.0.0.1:1",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "input_validation_failed" in result.output
    _no_traceback(result.output)

    runs = sorted((Path("evidence") / "replay").glob("rep-*"))
    assert len(runs) == 1
    trace = (runs[0] / "trace.jsonl").read_text(encoding="utf-8")
    assert '"event": "dispatch"' not in trace, "the application must not have been touched"
