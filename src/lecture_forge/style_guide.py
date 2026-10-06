"""Split the user's style guide into its three prompts, by its "## Part N" headings."""

import re
from dataclasses import dataclass
from pathlib import Path

PART = re.compile(r"^## Part (\d+)\b.*$", re.MULTILINE)
FENCE = re.compile(r"```[^\n]*\n(.*?)\n```", re.DOTALL)


class StyleGuideError(Exception):
    pass


@dataclass(frozen=True)
class StyleGuide:
    script_prompt: str  # Part 1: system prompt for the writing pass
    critique_prompt: str  # Part 2: system prompt for the critique pass
    input_template: str  # Part 3: per-episode input, without its code fence


def load_style_guide(path: str | Path) -> StyleGuide:
    path = Path(path)
    if not path.is_file():
        raise StyleGuideError(
            f"Style guide not found at {path}. Copy it to prompts/style-guide.md "
            "or set STYLE_GUIDE_PATH in .env."
        )
    text = path.read_text(encoding="utf-8")
    heads = list(PART.finditer(text))
    parts = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        parts[m[1]] = text[m.end() : end].strip().removesuffix("---").strip()
    missing = [f"Part {n}" for n in "123" if not parts.get(n)]
    if missing:
        raise StyleGuideError(f"{path} is missing {', '.join(missing)}")
    fence = FENCE.search(parts["3"])
    return StyleGuide(parts["1"], parts["2"], fence[1] if fence else parts["3"])
