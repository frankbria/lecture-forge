"""What the engine reports while it works, so a front end can show it.

Each event comes before the slow step it names (an LLM call, a TTS request, the decode that
checks a cached piece), so a front end can say "critiquing..." while it happens.

Failures are not events: the engine raises its domain exception, as without a callback. A
callback that raises stops the work right there; every step is resumable (paid audio pieces
are cached first), so that is a safe way to cancel. Raising on "done" reports a render that
did finish as failed, so don't. Events arrive synchronously on the thread that called the
engine: a GUI running the engine on a worker thread hands them to its UI thread itself.

A "note" event is not a step: it is something to tell the user that isn't a failure, in
words any front end can show. The cost preview notes a cached piece that has gone bad and
what replacing it costs; the render notes why an episode file is rendered again. With
nobody listening a note is logged. A retry of a network call is not an event: the two
functions that retry (providers.complete, render.elevenlabs_tts) take `on_retry`.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Progress:
    # "plan", "draft", "critique", "check", "piece", "join", "done" or "note"
    stage: str
    done: int = 0  # "check" and "piece": the piece's number, from 1
    total: int = 0  # "check" and "piece": how many pieces the episode has
    # "repair round"; for a piece, "synthesizing" or "reused"; for a note, its text
    note: str = ""
    episode: int = 0  # write and render: the episode's number; 0 for the plan


OnProgress = Callable[[Progress], None]


def ignore(event: Progress) -> None:
    """The default: nobody is watching."""


def tell(on_progress: OnProgress, text: str, episode: int = 0) -> None:
    """A note for the user: to the front end that listens, else to the log, so a script
    that calls the engine with no callback still sees it. Only the default, ,
    counts as nobody listening: a callback of the front end's own is given the note, even
    if it does nothing with it."""
    if on_progress is ignore:
        logging.getLogger(__name__).warning(text)
    else:
        on_progress(Progress("note", note=text, episode=episode))
