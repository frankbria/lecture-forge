import subprocess
import sys
from pathlib import Path

import pytest
from test_source import scanned_pdf

from lecture_forge.cli import main


def test_outline_prints_headings_indented(tmp_path, capsys):
    p = tmp_path / "n.md"
    p.write_text("# A\n## B\n")
    assert main(["outline", str(p)]) == 0
    assert capsys.readouterr().out == "n.md: 2 lines\nA  [1]\n  B  [2]\n"


def test_outline_without_headings_says_so(tmp_path, capsys):
    p = tmp_path / "n.txt"
    p.write_text("plain\n")
    main(["outline", str(p)])
    assert "no bookmarks" in capsys.readouterr().out


def test_bad_source_returns_error_code(tmp_path, capsys):
    assert main(["outline", str(tmp_path / "x.pdf")]) == 1
    assert "error:" in capsys.readouterr().err


def test_os_errors_print_as_error_lines(monkeypatch, capsys):
    from lecture_forge import cli

    def locked(args):
        raise PermissionError("locked by Dropbox")

    monkeypatch.setattr(cli, "cmd_outline", locked)
    assert main(["outline", "x.pdf"]) == 1
    assert "error: locked by Dropbox" in capsys.readouterr().err


def test_outline_notes_scanned_pages(tmp_path, capsys):
    assert main(["outline", str(scanned_pdf(tmp_path / "scan.pdf", "tssb"))]) == 0
    out = capsys.readouterr().out
    assert "note: 2 of 4 pages are scans (page images)" in out
    assert "nothing is OCR'd" in out
    main(["outline", str(scanned_pdf(tmp_path / "text.pdf", "tb"))])
    assert "note:" not in capsys.readouterr().out


@pytest.mark.parametrize("name", ["lecture-forge", "lforge"])
def test_each_command_name_runs_and_shows_itself(name):
    exe = Path(sys.executable).parent / name
    usage = subprocess.run([exe, "--help"], capture_output=True, text=True, check=False)
    assert usage.returncode == 0
    assert usage.stdout.startswith(f"usage: {name} ")
    bad = subprocess.run([exe, "nonsense"], capture_output=True, text=True, check=False)
    assert bad.returncode == 2
    assert f"{name}: error:" in bad.stderr
