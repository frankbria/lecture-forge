"""What the engine reports while it works, so a front end can show it.

Each event comes before the slow step it names (an LLM call, a TTS request), so a front end
can say "critiquing..." while it happens.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Progress:
    stage: str  # "plan", "draft", "critique", "piece", "join" or "done"
    done: int = 0  # "piece": its number, from 1
    total: int = 0  # "piece": how many pieces the episode has
    note: str = ""  # "repair round"; for a piece, "synthesizing" or "reused"


OnProgress = Callable[[Progress], None]


def ignore(event: Progress) -> None:
    """The default: nobody is watching."""
