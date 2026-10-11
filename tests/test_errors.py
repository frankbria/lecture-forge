"""Text a front end can show as it is (REQUIREMENTS, Goals): the engine's names no CLI flag
or command, and where an error's remedy is a front end's control, the error names it."""

import ast
import re
import string
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


def engine_strings(tree: ast.AST) -> set[tuple[int, str]]:
    """(line, text) of every string in a module that can reach a front end: errors, notes,
    warnings, however they are put together. Docstrings can't. Nor can a bare flag (an
    argument for another program) or the app's name by itself."""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
        and node.body  # __init__.py is empty
        and isinstance(node.body[0], ast.Expr)
    }
    return {
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and not re.fullmatch(r"--[a-z-]+|lecture-forge", node.value)
    }


def test_the_scan_reads_every_string_but_docstrings_and_bare_flags():
    code = (
        "def f(errors):\n"
        '    """Docs may say --all."""\n'
        '    errors.append(f"{n}: use --range")\n'
        '    return ["claude", "--model", "lecture-forge"]\n'
    )
    assert {text for _, text in engine_strings(ast.parse(code))} == {
        ": use --range", "claude"
    }  # fmt: skip


def test_engine_text_names_no_cli_flag_or_command():
    found = sorted(
        f"{path.name}:{line}: {text}"
        for path in ENGINE
        for line, text in engine_strings(ast.parse(path.read_text(encoding="utf-8")))
        # the prompts name the app to the model
        if CLI_WORDS.search(text.replace("the lecture-forge app", ""))
    )
    assert found == []


def test_advised_adds_the_advice_and_names_the_fix():
    e = advised(FileExistsError("x exists"), "; replace {what}", "replace", what="it")
    assert type(e) is FileExistsError and str(e) == "x exists; replace it"
    assert (e.problem, e.fix, e.details) == ("x exists", "replace", {"what": "it"})


def test_the_cli_has_words_for_every_fix_and_the_details_they_use():
    given = [  # (fix, the details it comes with) of each advised() call
        (node.args[2].value, {keyword.arg for keyword in node.keywords})
        for path in ENGINE
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "advised"
    ]
    assert {fix for fix, _ in given} == set(ADVICE)
    for fix, details in given:
        used = {name for _, name, _, _ in string.Formatter().parse(ADVICE[fix]) if name}
        assert used <= details, fix


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
        f"{out} exists and may hold your edits; it is replaced only if you ask for that"
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
