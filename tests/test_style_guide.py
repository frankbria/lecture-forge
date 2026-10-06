from pathlib import Path

import pytest

from lecture_forge.style_guide import StyleGuideError, load_style_guide

GUIDE = """# Guide

Intro text.

---

## Part 1 — Script-Writing Prompt

You are converting material.

---

## Part 2 — Critique-Pass Prompt

You are reviewing a script.

---

## Part 3 — Per-Episode Input Template

```
SOURCE MATERIAL:
{paste section text here}
```
"""


def test_parts_are_split_on_headings(tmp_path):
    p = tmp_path / "g.md"
    p.write_text(GUIDE)
    g = load_style_guide(p)
    assert g.script_prompt == "You are converting material."
    assert g.critique_prompt == "You are reviewing a script."
    assert g.input_template == "SOURCE MATERIAL:\n{paste section text here}"


def test_missing_file_says_where_to_put_it(tmp_path):
    with pytest.raises(StyleGuideError, match="STYLE_GUIDE_PATH"):
        load_style_guide(tmp_path / "nope.md")


def test_missing_part_is_reported(tmp_path):
    p = tmp_path / "g.md"
    p.write_text(GUIDE.split("## Part 2")[0])
    with pytest.raises(StyleGuideError, match="Part 2"):
        load_style_guide(p)


REAL = Path("prompts/style-guide.md")


@pytest.mark.skipif(not REAL.exists(), reason="user's style guide is not in the repo")
def test_real_style_guide_parses():
    g = load_style_guide(REAL)
    assert "Feynman" in g.script_prompt
    assert "Revised script" in g.critique_prompt
    assert g.input_template.startswith("SOURCE MATERIAL:")
