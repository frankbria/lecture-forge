"""What the engine reports while it works, so a front end can show it.

Each event comes before the slow step it names (an LLM call, a TTS request), so a front end
can say "critiquing..." while it happens.

Failures are not events: the engine raises its domain exception, as without a callback. A
callback that raises stops the work right there; every step is resumable (paid audio pieces
are cached first), so that is a safe way to cancel. Raising on "done" reports a render that
did finish as failed, so don't.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Progress:
    stage: str  # "plan", "draft", "critique", "piece", "join" or "done"
    done: int = 0  # "piece": its number, from 1
    total: int = 0  # "piece": how many pieces the episode has
    note: str = ""  # "repair round"; for a piece, "synthesizing" or "reused"
    episode: int = 0  # write and render: the episode's number; 0 for the plan


OnProgress = Callable[[Progress], None]


def ignore(event: Progress) -> None:
    """The default: nobody is watching."""
