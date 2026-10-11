"""Errors a front end can show as they are (REQUIREMENTS, Goals): the engine's name no CLI
flag or command, and where the remedy is a front end's control, the error names it."""

import ast
import re
from pathlib import Path

import pytest

import lecture_forge
from lecture_forge.cli import ADVICE, _worded, main
from lecture_forge.errors import advised
from lecture_forge.plan import write_plan

ENGINE = sorted(
    p for p in Path(lecture_forge.__file__).parent.glob("*.py") if p.name != "cli.py"
)
CLI_WORDS = re.compile(r"--[a-z]|lecture-forge|lforge")


def called(node: ast.AST) -> str:
    """The name a call is made by: `f` in f(...) and in x.f(...)."""
    if not isinstance(node, ast.Call):
        return ""
    return getattr(node.func, "id", "") or getattr(node.func, "attr", "")


def error_strings(tree: ast.AST) -> set[tuple[int, str]]:
    """(line, text) of every string in a raise, an exception's constructor, advised() or
    log.warning()."""
    return {
        (part.lineno, part.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Raise)
        or called(node).endswith("Error")
        or called(node) in ("advised", "warning")
        for part in ast.walk(node)
        if isinstance(part, ast.Constant) and isinstance(part.value, str)
    }


def test_the_scan_reads_errors_built_outside_a_raise_and_warnings():
    code = (
        "def f(n):\n"
        '    log.warning("w --all")\n'
        '    return ProviderError("p", f"too big {n}: use --range")\n'
    )
    assert {text for _, text in error_strings(ast.parse(code))} == {
        "w --all", "p", "too big ", ": use --range"
    }  # fmt: skip
    assert not error_strings(ast.parse('cmd = ["claude", "--model", "x"]'))


def test_engine_errors_name_no_cli_flag_or_command():
    found = sorted(
        f"{path.name}:{line}: {text}"
        for path in ENGINE
        for line, text in error_strings(ast.parse(path.read_text(encoding="utf-8")))
        if CLI_WORDS.search(text)
    )
    assert found == []


def test_advised_adds_the_advice_and_names_the_fix():
    e = advised(FileExistsError("x exists"), "; replace {what}", "replace", what="it")
    assert type(e) is FileExistsError and str(e) == "x exists; replace it"
    assert (e.problem, e.fix, e.details) == ("x exists", "replace", {"what": "it"})


def test_the_cli_has_words_for_every_fix_the_engine_names():
    fixes = {
        node.args[2].value
        for path in ENGINE
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if called(node) == "advised"
    }
    assert fixes == set(ADVICE)


def test_an_error_without_a_fix_is_shown_as_it_is():
    assert (
        _worded(ValueError("the plan has no episode 7")) == "the plan has no episode 7"
    )


def test_the_cli_names_its_flag_for_a_plan_that_exists(tmp_path):
    out = tmp_path / "plan.yaml"
    out.write_text("title: mine\n")
    with pytest.raises(FileExistsError) as e:
        write_plan({}, None, out)
    assert str(e.value) == (
        f"{out} exists and may hold your edits; replacing it has to be asked for"
    )
    assert _worded(e.value) == (
        f"{out} exists and may hold your edits; pass --force to replace it"
    )


def test_the_cli_calls_a_bad_series_name_by_its_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["write", "Bad", "--all"]) == 1
    assert capsys.readouterr().err == (
        "error: --series must be lowercase letters, digits, - or _, got 'Bad'\n"
    )
