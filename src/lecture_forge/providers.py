"""LLM providers behind one call: complete(provider, system, user, pdf) -> text.

Every provider sees the real PDF pages (no text extraction), so equations survive.
"""

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import anthropic
import openai
import pymupdf

from lecture_forge.config import Settings

log = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "claude-code": "",
    "anthropic": "claude-opus-5-5",
    "openai": "gpt-5.5",
}
TIMEOUT_S = 1800  # a full script plus critique can take many minutes
# Per-request PDF limits (provider docs): Claude API 600 pages (100 on 200k-context models)
# and 32 MB per request, base64 included; OpenAI 50 MB per file, no documented page limit.
CLAUDE_1M = re.compile(
    r"claude-[a-z]+-(4-[6-9]|[5-9]|\d{2})"
)  # 4.6 and later: 1M context
MAX_REQUEST_BYTES = {"anthropic": 32_000_000}
MAX_PDF_BYTES = {"openai": 50_000_000}


class ProviderError(Exception):
    def __init__(self, provider: str, message: str, retryable: bool = False):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable
        self.alternatives: list[str] = []  # filled in by complete() on final failure


def retryable_status(status: int | None) -> bool:
    """Timeouts (408), conflicts (409), rate limits (429), server errors and network failures
    (no status) are worth retrying; other 4xx are not. Mirrors the SDKs' own retry rule,
    which is switched off so there's one retry policy."""
    return status is None or status in (408, 409, 429) or status >= 500


def _api_error(provider: str, e: Exception) -> ProviderError:
    return ProviderError(
        provider, str(e), retryable_status(getattr(e, "status_code", None))
    )


def max_pdf_pages(model: str) -> int:
    """PDF pages per Claude API request: 600 on 1M-context models; 100 on older (200k)
    ones, and on unknown model names, the safe side."""
    return 600 if CLAUDE_1M.match(model) else 100


def check_pdf(name: str, pdf: Path, system: str, user: str, model: str = "") -> None:
    """Refuse a PDF over the provider's per-request limits before uploading it."""
    size = pdf.stat().st_size
    with pymupdf.open(pdf) as doc:
        pages = doc.page_count
    request = 4 * -(-size // 3) + len(system.encode()) + len(user.encode())  # base64
    found = f"{pages} page{'s' * (pages != 1)}, {size / 10**6:.1f} MB"
    if name == "anthropic" and pages > max_pdf_pages(model):
        why = f"{pages} pages; {model} takes at most {max_pdf_pages(model)}"
    elif request > MAX_REQUEST_BYTES.get(name, request):
        found += f", {request / 10**6:.1f} MB once base64-encoded"
        why = f"{name} takes at most {MAX_REQUEST_BYTES[name] // 10**6} MB per request"
    elif size > MAX_PDF_BYTES.get(name, size):
        why = f"{name} takes at most {MAX_PDF_BYTES[name] // 10**6} MB per PDF"
    else:
        return
    raise ProviderError(
        name,
        f"the PDF is too large ({found}): {why}. Use a smaller page range: plan one "
        "chapter at a time with --range (lecture-forge outline lists the chapter pages), "
        "or split the episode in plan.yaml. Or use the claude-code provider, which reads "
        "the pages as it goes",
    )


def _b64(pdf: Path) -> str:
    return base64.standard_b64encode(pdf.read_bytes()).decode()


def subscription_env() -> dict[str, str]:
    """Environment for `claude -p` with API credentials removed.

    Claude Code prefers ANTHROPIC_API_KEY over the subscription login when it sees one,
    which would bill every script to the API account.
    """
    return {
        k: v
        for k, v in os.environ.items()
        if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    }


def _claude_code(system: str, user: str, pdf: Path | None, settings: Settings) -> str:
    name = "claude-code"
    with tempfile.TemporaryDirectory() as tmp:
        if pdf:
            shutil.copy(pdf, Path(tmp) / "source.pdf")
            user += (
                "\n\nThe source material is source.pdf in the current directory. "
                "Read every page of it before writing."
            )
        cmd = [
            "claude", "-p",
            "--setting-sources", "",  # no user hooks, plugins, styles or CLAUDE.md
            "--system-prompt", system,
            "--tools", "Read",  # reads outside this temp dir are denied in -p mode
            "--strict-mcp-config",
            "--no-session-persistence",
            "--output-format", "json",
        ]  # fmt: skip
        if model := settings.llm_model or DEFAULT_MODELS[name]:
            cmd += ["--model", model]
        try:
            proc = subprocess.run(
                cmd, input=user, capture_output=True, text=True, encoding="utf-8",
                cwd=tmp, env=subscription_env(), timeout=TIMEOUT_S, check=False,
            )  # fmt: skip
        except FileNotFoundError as e:
            raise ProviderError(name, "`claude` CLI not found on PATH") from e
        except subprocess.TimeoutExpired as e:
            raise ProviderError(name, f"timed out after {TIMEOUT_S}s", True) from e
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        detail = (proc.stderr or proc.stdout).strip()[-500:]
        raise ProviderError(name, f"exit {proc.returncode}: {detail}", True) from None
    unexpected = ProviderError(name, f"unexpected reply: {proc.stdout.strip()[:200]}")
    if not isinstance(out, dict):
        raise unexpected
    if out.get("is_error"):
        status = out.get("api_error_status")
        raise ProviderError(
            name, out.get("result") or out.get("subtype"), retryable_status(status)
        )
    if not isinstance(out.get("result"), str):
        raise unexpected
    return out["result"]


def _anthropic(system: str, user: str, pdf: Path | None, settings: Settings) -> str:
    name = "anthropic"
    if not settings.anthropic_api_key:
        raise ProviderError(name, "ANTHROPIC_API_KEY is not set")
    model = settings.llm_model or DEFAULT_MODELS[name]
    if pdf:
        check_pdf(name, pdf, system, user, model)
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key, max_retries=0, timeout=TIMEOUT_S
    )
    content = []
    if pdf:
        content.append(
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": _b64(pdf),
                },
            }
        )
    content.append({"type": "text", "text": user})
    try:
        with client.beta.messages.stream(
            model=model,
            max_tokens=64000,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # a refused request is re-run on a fallback model
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.APIError as e:
        raise _api_error(name, e) from e
    if msg.stop_reason == "refusal":
        raise ProviderError(name, "the model declined this request")
    if msg.stop_reason == "max_tokens":
        raise ProviderError(name, "response was cut off at max_tokens")
    return "".join(b.text for b in msg.content if b.type == "text")


def _openai(system: str, user: str, pdf: Path | None, settings: Settings) -> str:
    name = "openai"
    if not settings.openai_api_key:
        raise ProviderError(name, "OPENAI_API_KEY is not set")
    if pdf:
        check_pdf(name, pdf, system, user)
    client = openai.OpenAI(
        api_key=settings.openai_api_key, max_retries=0, timeout=TIMEOUT_S
    )
    content = []
    if pdf:
        content.append(
            {
                "type": "input_file",
                "filename": "source.pdf",
                "file_data": f"data:application/pdf;base64,{_b64(pdf)}",
            }
        )
    content.append({"type": "input_text", "text": user})
    try:
        resp = client.responses.create(
            model=settings.llm_model or DEFAULT_MODELS[name],
            instructions=system,
            input=[{"role": "user", "content": content}],
        )
    except openai.APIError as e:
        raise _api_error(name, e) from e
    return _openai_text(resp)


def _openai_text(resp) -> str:
    """Text of a Responses API reply. `output_text` silently skips refusal blocks."""
    if resp.status == "incomplete":
        raise ProviderError("openai", f"response incomplete: {resp.incomplete_details}")
    for item in resp.output:
        for part in getattr(item, "content", None) or []:
            if part.type == "refusal":
                raise ProviderError(
                    "openai", f"the model declined this request: {part.refusal}"
                )
    return resp.output_text


PROVIDERS: dict[str, Callable[[str, str, Path | None, Settings], str]] = {
    "claude-code": _claude_code,
    "anthropic": _anthropic,
    "openai": _openai,
}


def available(settings: Settings) -> list[str]:
    """Providers that have what they need to run on this machine."""
    ready = {
        "claude-code": shutil.which("claude") is not None,
        "anthropic": bool(settings.anthropic_api_key),
        "openai": bool(settings.openai_api_key),
    }
    return [p for p, ok in ready.items() if ok]


def complete(
    provider: str,
    system: str,
    user: str,
    pdf: Path | None = None,
    *,
    settings: Settings,
    attempts: int = 4,
    base_delay: float = 10.0,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Call a provider, retrying transient failures with exponential backoff.

    On final failure the ProviderError lists the other usable providers, so the caller
    can offer to switch.
    """
    for attempt in range(attempts):
        try:
            text = PROVIDERS[provider](system, user, pdf, settings)
            if not text.strip():  # never hand an empty script to the next step
                raise ProviderError(provider, "returned an empty response")
            return text
        except ProviderError as e:
            if not e.retryable or attempt == attempts - 1:
                e.alternatives = [p for p in available(settings) if p != provider]
                raise
            delay = base_delay * 2**attempt
            log.warning(
                "%s; retrying in %.0fs (%d/%d)", e, delay, attempt + 1, attempts - 1
            )
            sleep(delay)
