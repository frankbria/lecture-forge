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
from lecture_forge.providers import retryable_status
from lecture_forge.write import episode_dir

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


def _script(series_dir: Path, ep: dict) -> str:
    path = episode_dir(series_dir, ep["n"]) / "script.txt"
    if not path.exists():
        raise RenderError(f"write episode {ep['n']} first (lecture-forge write)")
    return path.read_text(encoding="utf-8")


def _target(plan: dict, ep: dict, series_dir: Path, settings: Settings):
    """(script, output MP3, render state, previous state, up to date) for one episode."""
    script = _script(series_dir, ep)
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
    previous = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
    return script, out, state, previous, out.exists() and previous == state


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


def _join(files: list[Path], out: Path, tags: dict[str, str]) -> None:
    """Concatenate the pieces and tag them; publish atomically so Dropbox never syncs a half file."""
    if shutil.which("ffmpeg") is None:
        raise RenderError("ffmpeg is not installed; it joins the pieces into one MP3")
    tmp = out.with_name(f".{out.name}.part")
    with tempfile.TemporaryDirectory() as td:
        listing = Path(td) / "pieces.txt"
        quoted = (str(f.resolve()).replace("'", "'\\''") for f in files)
        listing.write_text("".join(f"file '{q}'\n" for q in quoted), encoding="utf-8")
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
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
) -> Rendered:
    script, out, state, previous, up_to_date = _target(plan, ep, series_dir, settings)
    if up_to_date and not force:
        return Rendered(out, 0, 0, True)
    # Folders only now, after the skip check: a cost preview or a skip creates nothing.
    out.parent.parent.mkdir(exist_ok=True)  # the output dir; its parent was checked
    out.parent.mkdir(exist_ok=True)
    d = episode_dir(series_dir, ep["n"])
    marker = d / "render.json"
    audio_dir = d / "audio"
    audio_dir.mkdir(exist_ok=True)
    stitch = settings.model_id not in NO_STITCHING
    files, ids, billed, reused = [], [], 0, 0
    for text, key in _keyed(
        settings, split_text(script, char_limit(settings.model_id))
    ):
        mp3, rid = audio_dir / f"{key}.mp3", audio_dir / f"{key}.rid"
        if mp3.exists():
            reused += 1
        else:
            context = [i for i in ids[-MAX_PREVIOUS:] if i] if stitch else []
            audio, request_id = tts(text, context)
            part = mp3.with_suffix(".part")
            part.write_bytes(audio)
            rid.write_text(request_id, encoding="utf-8")
            os.replace(part, mp3)  # a piece is cached only once it is fully on disk
            billed += len(text)
        fresh = rid.exists() and time.time() - rid.stat().st_mtime < ID_TTL_S
        ids.append(rid.read_text(encoding="utf-8").strip() if fresh else "")  # "": skip
        files.append(mp3)
    artist = plan["author"] or "lecture-forge"
    _join(files, out, {
        "title": ep["title"], "album": plan["title"], "artist": artist, "album_artist": artist,
        "track": f"{ep['n']}/{max(e['n'] for e in plan['episodes'])}", "genre": "Speech",
    })  # fmt: skip
    marker.write_text(json.dumps(state, indent=2), encoding="utf-8")
    _remove_replaced(previous.get("output"), out, settings, ep["n"])
    return Rendered(out, billed, reused, False)


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
