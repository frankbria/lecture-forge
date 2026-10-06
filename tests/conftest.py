"""Spending guard: tests never use your real API keys unless they are paid tests you opted into.

Per-use billed APIs (ElevenLabs, Anthropic API, OpenAI) cost money on every call. A test
marked `paid` runs only with LECTURE_FORGE_PAID_TESTS=1, and every other test is blocked
from building a client with one of your real keys (or with no key, since the SDKs then
read the environment). Fake keys still work, so free error-path tests (a 401 from an
invalid key) keep running. Claude Code runs on your subscription and isn't affected.
"""

import os
from pathlib import Path

import anthropic
import openai
import pytest

from lecture_forge import render
from lecture_forge.config import load_settings

PAID_OPT_IN = os.environ.get("LECTURE_FORGE_PAID_TESTS") == "1"
_s = load_settings(Path(__file__).resolve().parent.parent / ".env")
REAL_KEYS = {
    k for k in (_s.elevenlabs_api_key, _s.anthropic_api_key, _s.openai_api_key) if k
}


class BilledCallBlocked(AssertionError):
    pass


def pytest_collection_modifyitems(config, items):
    if PAID_OPT_IN:
        return
    skip = pytest.mark.skip(
        reason="bills a per-use API; run with LECTURE_FORGE_PAID_TESTS=1"
    )
    for item in items:
        if item.get_closest_marker("paid"):
            item.add_marker(skip)


def _guarded(cls, name):
    def make(*args, api_key=None, **kwargs):
        if api_key is None or api_key in REAL_KEYS:
            raise BilledCallBlocked(
                f"this test tried to build a {name} client with your real key (or none, so "
                "the SDK would read the environment). Use a fake key, or mark the test "
                "@pytest.mark.paid and run with LECTURE_FORGE_PAID_TESTS=1."
            )
        return cls(*args, api_key=api_key, **kwargs)

    return make


@pytest.fixture(autouse=True)
def block_billed_apis(request, monkeypatch):
    if PAID_OPT_IN and request.node.get_closest_marker("paid"):
        return  # an opted-in paid test: the real thing
    monkeypatch.setattr(render, "ElevenLabs", _guarded(render.ElevenLabs, "ElevenLabs"))
    monkeypatch.setattr(
        anthropic, "Anthropic", _guarded(anthropic.Anthropic, "Anthropic")
    )
    monkeypatch.setattr(openai, "OpenAI", _guarded(openai.OpenAI, "OpenAI"))
