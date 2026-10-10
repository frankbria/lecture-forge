"""Render scripts to MP3: split, synthesize with ElevenLabs (cached per piece), join, tag."""

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from elevenlabs.client import ElevenLabs
from elevenlabs.core.api_error import ApiError

from lecture_forge.config import Settings
from lecture_forge.plan import save
from lecture_forge.progress import OnProgress, Progress, ignore
from lecture_forge.providers import retryable_status
from lecture_forge.write import episode_dir, script_state, stale_reason

log = logging.getLogger(__name__)

CHAR_LIMITS = {  # characters per request (ElevenLabs model docs)
    "eleven_v3": 5000,
    "eleven_v4": 10000,
    "eleven_multilingual_v2": 10000,
    "eleven_flash_v2_5": 40000,
    "eleven_flash_v2": 30000,
}
NO_STITCHING = {"eleven_v3"}  # rejects previous_request_ids
MAX_PREVIOUS = 3  # ElevenLabs accepts at most 3 previous_request_ids
ID_TTL_S = 2 * 3600  # request ids can be stitched to for 2 hours
SEED = 1729  # one fixed seed for every piece: steadier delivery across joins
OUTPUT_FORMAT = "mp3_44100_128"
MEASURED = ("chars_sent", "credits_used")  # render.json keys: what a render cost
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}  # fmt: skip

TTS = Callable[
    [str, list[str]], tuple[bytes, str]
]  # (text, previous ids) -> (mp3, request id)


class RenderError(Exception):
    pass


@dataclass(frozen=True)
class Rendered:
    path: Path
    billed: int  # characters sent to ElevenLabs this run
    reused: int  # pieces taken from the cache instead of paid for again
    skipped: bool  # already rendered from this exact script, voice and model
    credits: int | None = None  # the plan balance's drop over this render, if any


def char_limit(model: str) -> int:
    return CHAR_LIMITS.get(model, 5000)  # unknown model: the strictest known limit


# --- splitting ------------------------------------------------------------------------


def _pack(parts: list[str], limit: int, sep: str) -> list[str]:
    out, cur = [], ""
    for part in parts:
        if cur and len(cur) + len(sep) + len(part) > limit:
            out.append(cur)
            cur = part
        else:
            cur = f"{cur}{sep}{part}" if cur else part
    return out + [cur] if cur else out


def _split_long(paragraph: str, limit: int) -> list[str]:
    """A paragraph over the limit: by sentences, then words, then (absurdly long words) chars."""
    units = []
    for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
        if len(sentence) <= limit:
            units.append(sentence)
            continue
        for word in sentence.split():
            units += [word[i : i + limit] for i in range(0, len(word), limit)]
    return _pack(units, limit, " ")


def split_text(text: str, limit: int) -> list[str]:
    """Pieces of at most `limit` characters, cut at paragraph breaks wherever possible."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        raise RenderError("the script is empty")
    pieces, run = [], []
    for p in paragraphs:
        if len(p) <= limit:
            run.append(p)
            continue
        pieces += _pack(run, limit, "\n\n") + _split_long(p, limit)
        run = []
    return pieces + _pack(run, limit, "\n\n")


# --- names and places ---------------------------------------------------------------------


def safe_filename(name: str) -> str:
    """A file or folder name valid on Windows (the Dropbox side) as well as Linux."""
    s = name.replace(":", " -").replace("/", "-").replace("\\", "-")
    s = re.sub(r'[<>"|?*\x00-\x1f]', "", s)
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
    s = s[:120].rstrip(". ")
    if not s:
        return "untitled"
    return "_" + s if s.split(".")[0].upper() in RESERVED else s


def _album_path(settings: Settings, plan: dict) -> Path:
    """The series' folder in the output dir. Creates nothing: the cost preview uses it too."""
    base = settings.output_dir
    # e.g. D: not mounted: don't invent a Linux-only folder
    if not base.parent.exists():
        raise RenderError(
            f"{base.parent} does not exist (is the drive mounted?); "
            "set LECTURE_FORGE_OUTPUT_DIR to a folder that does"
        )
    return base / safe_filename(plan["title"])


def _keyed(settings: Settings, pieces: list[str]) -> list[tuple[str, str]]:
    """(text, cache key) per piece: anything that changes a piece's audio changes its key.

    A stitched piece is spoken in continuity with the pieces before it, so their keys are
    part of its own: editing piece 1 re-renders the stitched pieces that follow it."""
    out: list[tuple[str, str]] = []
    stitch = settings.model_id not in NO_STITCHING
    for text in pieces:
        ident = [settings.model_id, settings.voice_id, SEED, OUTPUT_FORMAT, text]
        if stitch:  # (unstitched keys stay as they were, so existing caches still hit)
            ident.append([k for _, k in out[-MAX_PREVIOUS:]])
        out.append((text, hashlib.sha256(json.dumps(ident).encode()).hexdigest()[:20]))
    return out


# --- ElevenLabs ------------------------------------------------------------------------------


def elevenlabs_tts(
    settings: Settings,
    *,
    convert: Callable[..., tuple[bytes, str]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 4,
) -> TTS:
    """ElevenLabs text-to-speech with the same retry policy as the LLM providers."""
    voice = settings.require("voice_id")
    if convert is None:
        client = ElevenLabs(api_key=settings.require("elevenlabs_api_key"), timeout=300)

        def convert(**kw):
            with client.text_to_speech.with_raw_response.convert(**kw) as resp:
                return b"".join(resp.data), resp._response.headers.get("request-id", "")

    def tts(text: str, previous_ids: list[str]) -> tuple[bytes, str]:
        kw = {"voice_id": voice, "model_id": settings.model_id, "text": text,
              "output_format": OUTPUT_FORMAT, "seed": SEED}  # fmt: skip
        if previous_ids:
            kw["previous_request_ids"] = previous_ids
        for attempt in range(attempts):
            try:
                return convert(**kw)
            except ApiError as e:
                status, err, detail = e.status_code, e, f"{e.status_code} {e.body}"
            except httpx.HTTPError as e:
                status, err, detail = None, e, f"network error: {e}"
            if not retryable_status(status) or attempt == attempts - 1:
                raise RenderError(f"ElevenLabs: {detail}") from err
            delay = 10 * 2**attempt
            log.warning("ElevenLabs %s; retrying in %ss", detail, delay)
            sleep(delay)

    return tts


def characters_left(settings: Settings) -> int | None:
    """Characters left on the ElevenLabs plan this cycle, or None if it can't be read."""
    try:
        sub = ElevenLabs(
            api_key=settings.require("elevenlabs_api_key")
        ).user.subscription.get()
        return sub.character_limit - sub.character_count
    except (
        ApiError,
        httpx.HTTPError,
        AttributeError,
        TypeError,
    ) as e:  # informational only
        log.warning("couldn't read the ElevenLabs balance: %s", e)
        return None


# --- rendering ------------------------------------------------------------------------------


def _script(series_dir: Path, plan: dict, ep: dict) -> str:
    n, state = ep["n"], script_state(series_dir, plan, ep)
    if state == "missing":
        raise RenderError(f"write episode {n} first (lecture-forge write)")
    if state == "stale":
        raise RenderError(
            f"episode {n}'s script no longer matches the plan "
            f"({stale_reason(series_dir, plan, ep)}); rewrite it: "
            f"lecture-forge write {Path(series_dir).name} --episode {n}"
        )
    return (episode_dir(series_dir, n) / "script.txt").read_text(encoding="utf-8")


def _target(plan: dict, ep: dict, series_dir: Path, settings: Settings):
    """(script, output MP3, render state, previous state, up to date) for one episode."""
    script = _script(series_dir, plan, ep)
    out = (
        _album_path(settings, plan)
        / f"{ep['n']:02d} - {safe_filename(ep['title'])}.mp3"
    )
    state = {
        "script_sha256": hashlib.sha256(script.encode()).hexdigest(),
        "model": settings.model_id,
        "voice": settings.voice_id,
        "output": str(out),
    }
    marker = episode_dir(series_dir, ep["n"]) / "render.json"
    previous = {}
    if marker.exists():
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
        except (
            ValueError
        ):  # bad JSON or not UTF-8 (JSONDecodeError, UnicodeDecodeError)
            previous = None
        if not isinstance(previous, dict):  # torn by a crash, or hand-edited
            log.warning("%s is unreadable; rebuilding from cached pieces", marker)
            previous = {}
    up_to_date = out.exists() and {k: previous.get(k) for k in state} == state
    return script, out, state, previous, up_to_date


def _measured(marker) -> tuple[int, int]:
    """(characters sent, credits used) recorded in a render.json, or (0, 0)."""
    vals = [marker.get(k) for k in MEASURED] if isinstance(marker, dict) else []
    ok = len(vals) == 2 and all(type(v) is int and v > 0 for v in vals)
    return (vals[0], vals[1]) if ok else (0, 0)


def credit_rate(series_dir: Path, model: str) -> float | None:
    """Credits per character sent, measured over this series' earlier renders of `model`."""
    chars = credits = 0
    for marker in Path(series_dir).glob("episodes/*/render.json"):
        try:
            m = json.loads(marker.read_text(encoding="utf-8"))
        except ValueError:  # torn: _target warns when that episode renders
            continue
        if isinstance(m, dict) and m.get("model") == model:
            c, k = _measured(m)
            chars, credits = chars + c, credits + k
    return credits / chars if chars > 0 else None


def cost(
    plan: dict, ep: dict, series_dir: Path, settings: Settings, *, force: bool = False
) -> int:
    """Characters this episode would send to ElevenLabs now: 0 if it is already rendered
    (and not forced), else only the pieces not yet cached."""
    script, _, _, _, up_to_date = _target(plan, ep, series_dir, settings)
    if up_to_date and not force:
        return 0
    audio = episode_dir(series_dir, ep["n"]) / "audio"
    pieces = _keyed(settings, split_text(script, char_limit(settings.model_id)))
    return sum(len(t) for t, k in pieces if not (audio / f"{k}.mp3").exists())


def require_ffmpeg() -> None:
    """A machine without ffmpeg can't finish an episode, so nothing may be spent on one."""
    if shutil.which("ffmpeg") is None:
        raise RenderError("ffmpeg is not installed; it joins the pieces into one MP3")


@dataclass(frozen=True)
class Preflight:
    """What rendering would spend, decided before anything is. A front end shows it and
    gets an explicit yes before calling render_episode."""

    episodes: list[
        tuple[dict, int | None, str]
    ]  # (episode, characters to send, or None and why it's skipped)
    total: int  # characters to send
    rate: (
        float | None
    )  # measured credits per character; None until a render of the model
    estimate: int  # credits: total at the measured rate, or one per character
    left: int | None  # credits left on the plan; None if it can't be read

    @property
    def ready(self) -> list[dict]:
        return [ep for ep, chars, _ in self.episodes if chars is not None]

    @property
    def over_balance(self) -> bool:
        return self.left is not None and self.estimate > self.left

    def check(self) -> None:
        """Raise if this render mustn't start: it needs more credits than the plan has, or
        there is something to spend on and no ffmpeg to finish it with."""
        if self.over_balance:
            raise RenderError(
                f"this needs about {self.estimate:,} credits but your plan has only "
                f"{self.left:,} characters left (ElevenLabs counts credits as characters)"
            )
        if self.total:
            require_ffmpeg()


def preflight(
    plan: dict,
    series_dir: Path,
    settings: Settings,
    *,
    episode: int | None,
    force: bool,
) -> Preflight:
    """The cost of rendering one episode or every written one. With --all, unwritten and
    stale episodes are skipped; one requested episode raises why it can't render instead."""
    if episode is not None:
        todo = [e for e in plan["episodes"] if e["n"] == episode]
        if not todo:
            raise ValueError(f"the plan has no episode {episode}")
    else:
        todo = plan["episodes"]
    rows, total = [], 0
    rate = credit_rate(series_dir, settings.model_id)
    for ep in todo:
        state = script_state(series_dir, plan, ep)
        if state != "current" and episode is None:  # one episode: cost() says why
            why = (
                "not written yet"
                if state == "missing"
                else f"script no longer matches the plan ({stale_reason(series_dir, plan, ep)})"
            )
            rows.append((ep, None, why))
            continue
        chars = cost(plan, ep, series_dir, settings, force=force)
        rows.append((ep, chars, ""))
        total += chars
    left = characters_left(settings)
    # Nothing measured yet: assume the worst, one credit per character.
    return Preflight(rows, total, rate, round(total * (rate or 1.0)), left)


def _join(files: list[Path], out: Path, tags: dict[str, str]) -> None:
    """Concatenate the pieces and tag them; publish atomically so Dropbox never syncs a half file."""
    tmp = out.with_name(f".{out.name}.part")
    with tempfile.TemporaryDirectory() as td:
        listing = Path(td) / "pieces.txt"
        quoted = (str(f.resolve()).replace("'", "'\\''") for f in files)
        listing.write_text("".join(f"file '{q}'\n" for q in quoted), encoding="utf-8")
        # -xerror: without it a corrupt piece after a good one exits 0 with a truncated file.
        cmd = ["ffmpeg", "-loglevel", "error", "-xerror", "-y", "-f", "concat", "-safe", "0",
               "-i", str(listing), "-c", "copy", "-map_metadata", "-1", "-id3v2_version", "3"]  # fmt: skip
        for k, v in tags.items():
            cmd += ["-metadata", f"{k}={v}"]
        proc = subprocess.run(
            [*cmd, "-f", "mp3", str(tmp)], capture_output=True, text=True, check=False
        )
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RenderError(f"ffmpeg failed: {proc.stderr.strip()[-500:]}")
    os.replace(tmp, out)


def render_episode(
    plan: dict,
    ep: dict,
    series_dir: Path,
    settings: Settings,
    tts: TTS,
    *,
    force: bool = False,
    balance: Callable[[], int | None] | None = None,
    on_progress: OnProgress = ignore,
) -> Rendered:
    """`balance` reads the plan's credit balance; read before and after synthesis, the drop
    is recorded in render.json as what this episode cost."""
    script, out, state, previous, up_to_date = _target(plan, ep, series_dir, settings)
    if up_to_date and not force:
        return Rendered(out, 0, 0, True)
    require_ffmpeg()  # before any folder or paid request
    # Folders only now, after the skip check: a cost preview or a skip creates nothing.
    out.parent.parent.mkdir(exist_ok=True)  # the output dir; its parent was checked
    out.parent.mkdir(exist_ok=True)
    d = episode_dir(series_dir, ep["n"])
    marker = d / "render.json"
    audio_dir = d / "audio"
    audio_dir.mkdir(exist_ok=True)
    stitch = settings.model_id not in NO_STITCHING
    files, ids, billed, reused, before = [], [], 0, 0, None
    pieces = _keyed(settings, split_text(script, char_limit(settings.model_id)))
    for k, (text, key) in enumerate(pieces, 1):
        mp3, rid = audio_dir / f"{key}.mp3", audio_dir / f"{key}.rid"
        on_progress(
            Progress(
                "piece", k, len(pieces), "reused" if mp3.exists() else "synthesizing"
            )
        )
        if mp3.exists():
            reused += 1
        else:
            if balance and not billed:  # just before the first piece paid for
                before = balance()
            context = [i for i in ids[-MAX_PREVIOUS:] if i] if stitch else []
            audio, request_id = tts(text, context)
            part = mp3.with_suffix(".part")
            part.write_bytes(audio)
            save(rid, request_id)
            os.replace(part, mp3)  # a piece is cached only once it is fully on disk
            billed += len(text)
        fresh = rid.exists() and time.time() - rid.stat().st_mtime < ID_TTL_S
        ids.append(rid.read_text(encoding="utf-8").strip() if fresh else "")  # "": skip
        files.append(mp3)
    after = balance() if balance and billed else None
    # Counts can lag, or be topped up mid-render: only a drop is a cost.
    credits = before - after if before is not None and after is not None else 0
    # Add to the episode's earlier measurement of this model: a small edit's sample
    # mustn't replace a whole episode's, and a rebuild that sends nothing keeps it.
    sent, used = (
        _measured(previous) if previous.get("model") == state["model"] else (0, 0)
    )
    if credits > 0:
        sent, used = sent + billed, used + credits
    if used:
        state |= dict(zip(MEASURED, (sent, used)))
    artist = plan["author"] or "lecture-forge"
    on_progress(Progress("join"))
    _join(files, out, {
        "title": ep["title"], "album": plan["title"], "artist": artist, "album_artist": artist,
        "track": f"{ep['n']}/{max(e['n'] for e in plan['episodes'])}", "genre": "Speech",
    })  # fmt: skip
    save(marker, json.dumps(state, indent=2))
    _remove_replaced(previous.get("output"), out, settings, ep["n"])
    on_progress(Progress("done"))
    return Rendered(out, billed, reused, False, credits if credits > 0 else None)


def _remove_replaced(old: str | None, new: Path, settings: Settings, n: int) -> None:
    """After a rename, delete this episode's previous MP3 so the playlist has no duplicate.

    Only a file this app wrote: an .mp3 for this episode number inside the output folder."""
    if not old:
        return
    old_path = Path(old)
    # On a case-insensitive drive (Dropbox on D:) a case-only rename is the same file.
    if old_path.exists() and new.exists() and os.path.samefile(old_path, new):
        return
    base = settings.output_dir.resolve()
    if (
        old_path != new
        and old_path.suffix == ".mp3"
        and old_path.name.startswith(f"{n:02d} - ")
        and old_path.resolve().is_relative_to(base)
        and old_path.is_file()
    ):
        old_path.unlink()
