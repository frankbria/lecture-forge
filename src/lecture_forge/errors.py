"""Advice an error carries apart from its message, so each front end can say the remedy
with its own controls: a flag in the CLI, a button in a GUI (REQUIREMENTS, Goals)."""


def advised[E: Exception](error: E, advice: str, fix: str, **details: object) -> E:
    """`error` with `advice` (a `str.format` template, filled from `details`) added to its
    message, in words any front end can show as they are. A front end with its own words
    for the remedy `fix` names shows `error.problem`, the message without the advice, and
    then its own."""
    error.problem = str(error)
    error.args = (error.problem + advice.format(**details),)
    error.fix, error.details = fix, details
    return error
