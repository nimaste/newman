"""Text to speech over an OpenAI-compatible HTTP API (Kokoro-FastAPI and the like).

    POST {url}/v1/audio/speech  {"model", "input", "voice", "response_format": "wav"}  -> a WAV file
    GET  {url}/v1/audio/voices  -> {"voices": ["af_heart", ...]}

Returns the same Audio the Wyoming client does, so the pipeline doesn't care which one speaks.
FallbackTTS wraps it with Piper over Wyoming for when the HTTP service is down.
"""
from __future__ import annotations

import io
import logging
import re
import time
import wave
from typing import Any

import httpx

from .wyoming import Audio, WyomingError

log = logging.getLogger(__name__)

ACCENTS = {"a": "American", "b": "British"}  # Kokoro's English voices: a…_ / b…_ prefixes
GENDERS = {"f": "woman", "m": "man"}
VOICES_TTL = 600.0


def voice_label(name: str) -> str | None:
    """"af_heart" -> "Heart (American, woman)"; None for a voice that isn't English Kokoro-style."""
    m = re.fullmatch(r"([a-z])([fm])_([a-z0-9_]+)", name or "")
    if not m or m.group(1) not in ACCENTS:
        return None
    nice = " ".join(w.capitalize() for w in m.group(3).split("_") if w)
    return f"{nice} ({ACCENTS[m.group(1)]}, {GENDERS[m.group(2)]})"


def audio_from_wav(data: bytes) -> Audio:
    """A WAV file -> Audio. Lenient about streamed WAVs whose header sizes are left unset."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            fmt = (w.getframerate(), w.getsampwidth(), w.getnchannels())
            pcm = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as e:
        raise WyomingError(f"text-to-speech sent audio that isn't WAV ({e})") from e
    if not pcm:  # header said 0 frames: take everything after the data chunk
        i = data.find(b"data")
        if i >= 0:
            pcm = data[i + 8:]
    width, channels = fmt[1], fmt[2]
    pcm = pcm[:len(pcm) - len(pcm) % max(1, width * channels)]
    return Audio(pcm, fmt[0], width, channels)


def wav_bytes(pcm: bytes, rate: int = 24000) -> bytes:
    """Test helper and fallback: 16-bit mono PCM as a WAV file."""
    return Audio(pcm, rate).wav()


class OpenAITTS:
    def __init__(self, url: str, model: str = "kokoro", timeout: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.url, self.model, self.timeout, self.transport = url.rstrip("/"), model, timeout, transport
        self._voices: tuple[float, list[dict[str, str]]] | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self.transport)

    async def synthesize(self, text: str, voice: str | None = None, speaker: str | None = None) -> Audio:
        body: dict[str, Any] = {"model": self.model, "input": text, "voice": voice or "af_heart",
                                "response_format": "wav", "stream": False}
        try:
            async with self._client() as c:
                r = await c.post(f"{self.url}/v1/audio/speech", json=body)
        except httpx.HTTPError as e:
            raise WyomingError(f"can't reach {self.url} ({e.__class__.__name__}: {e})") from e
        if r.status_code != 200:
            raise WyomingError(f"text-to-speech answered {r.status_code}: {r.text[:120]}")
        return audio_from_wav(r.content)

    async def voices(self) -> list[dict[str, str]]:
        """The English voices, labelled (cached for a while; the last good list if it's unreachable)."""
        now = time.monotonic()
        if self._voices and now - self._voices[0] < VOICES_TTL:
            return self._voices[1]
        try:
            async with self._client() as c:
                r = await c.get(f"{self.url}/v1/audio/voices")
            r.raise_for_status()
            raw = r.json()
            names = raw.get("voices") if isinstance(raw, dict) else raw
            out = []
            for v in names or []:
                name = v if isinstance(v, str) else str((v or {}).get("name") or (v or {}).get("id") or "")
                label = voice_label(name)
                if label:
                    out.append({"name": name, "label": label})
            out.sort(key=lambda x: (x["name"][0], x["name"][1], x["label"]))
        except (httpx.HTTPError, ValueError, AttributeError) as e:
            log.warning("Text-to-speech voices: %s", e)
            return self._voices[1] if self._voices else []
        self._voices = (now, out)
        return out

    def known_voices(self) -> set[str] | None:
        """The voice names from the last listing (None before one)."""
        return {v["name"] for v in self._voices[1]} if self._voices else None


class FallbackTTS:
    """The HTTP service first; on failure, Piper over Wyoming (its own default voice) when configured."""

    def __init__(self, primary: OpenAITTS, fallback: Any = None) -> None:
        self.primary, self.fallback, self._warned = primary, fallback, False

    async def synthesize(self, text: str, voice: str | None = None, speaker: str | None = None) -> Audio:
        try:
            audio = await self.primary.synthesize(text, voice, speaker)
        except Exception as e:  # noqa: BLE001
            if self.fallback is None:
                raise
            if not self._warned:
                log.warning("Text-to-speech (%s) failed, using Piper instead: %s", self.primary.url, e)
                self._warned = True
            return await self.fallback.synthesize(text, None)
        if self._warned:
            log.info("Text-to-speech (%s) is back", self.primary.url)
            self._warned = False
        return audio

    async def voices(self) -> list[dict[str, str]]:
        return await self.primary.voices()

    def known_voices(self) -> set[str] | None:
        return self.primary.known_voices()
