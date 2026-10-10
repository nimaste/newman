"""A small Wyoming protocol client (speech-to-text and text-to-speech services such as
rhasspy/wyoming-whisper and rhasspy/wyoming-piper).

Wyoming is a TCP stream of events. Each event is one JSON header line
    {"type": "audio-chunk", "data": {...}, "data_length": 31, "payload_length": 2048}\n
followed by `data_length` bytes of extra JSON (merged into "data") and `payload_length` bytes of
binary payload (raw PCM for audio chunks). See https://github.com/rhasspy/wyoming.

Speech to text: transcribe, audio-start, audio-chunk…, audio-stop  ->  transcript {"text"}
Text to speech: synthesize {"text", "voice": {"name"}}  ->  audio-start, audio-chunk…, audio-stop
"""
from __future__ import annotations

import asyncio
import io
import json
import wave
from dataclasses import dataclass, field
from typing import Any

VERSION = "1.5.2"
CHUNK_BYTES = 2048 * 2  # 2048 samples of 16-bit audio per chunk (what Home Assistant sends)


class WyomingError(Exception):
    pass


@dataclass
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    payload: bytes = b""


def parse_address(value: str | None, default_port: int) -> tuple[str, int] | None:
    """"whisper:10300" / "tcp://10.1.1.2:10300" / "10.1.1.2" -> (host, port); None when unset."""
    v = (value or "").strip()
    if not v:
        return None
    if "://" in v:
        v = v.split("://", 1)[1]
    v = v.strip("/")
    if v.startswith("["):  # [ipv6]:port
        host, _, rest = v[1:].partition("]")
        return host, int(rest.lstrip(":") or default_port)
    host, sep, port = v.rpartition(":")
    if not sep or not port.isdigit():
        return v, default_port
    return host, int(port)


async def write_event(writer: asyncio.StreamWriter, ev: Event) -> None:
    header: dict[str, Any] = {"type": ev.type, "version": VERSION}
    data = json.dumps(ev.data, ensure_ascii=False).encode() if ev.data else b""
    if data:
        header["data_length"] = len(data)
    if ev.payload:
        header["payload_length"] = len(ev.payload)
    writer.write(json.dumps(header).encode() + b"\n" + data + ev.payload)
    await writer.drain()


async def read_event(reader: asyncio.StreamReader) -> Event | None:
    """The next event, or None at end of stream."""
    line = await reader.readline()
    if not line:
        return None
    try:
        header = json.loads(line)
    except ValueError as e:
        raise WyomingError(f"bad event header: {line[:80]!r}") from e
    if not isinstance(header, dict) or "type" not in header:
        raise WyomingError("event without a type")
    data = header.get("data") or {}
    if header.get("data_length"):
        extra = json.loads(await reader.readexactly(int(header["data_length"])))
        if isinstance(extra, dict):
            data = {**data, **extra}
    payload = await reader.readexactly(int(header["payload_length"])) if header.get("payload_length") else b""
    return Event(str(header["type"]), data if isinstance(data, dict) else {}, payload)


class WyomingClient:
    """One request per connection (that's how the services are used by Home Assistant too)."""

    def __init__(self, host: str, port: int, timeout: float = 15.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    async def _open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            return await asyncio.wait_for(asyncio.open_connection(self.host, self.port), min(self.timeout, 5.0))
        except (OSError, asyncio.TimeoutError) as e:
            raise WyomingError(f"can't reach {self.host}:{self.port} ({e or 'timed out'})") from e

    @staticmethod
    async def _close(writer: asyncio.StreamWriter) -> None:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ConnectionError):
            pass

    async def describe(self) -> dict[str, Any]:
        """The service's `info` event (its models / voices)."""
        reader, writer = await self._open()
        try:
            await write_event(writer, Event("describe"))
            while True:
                ev = await asyncio.wait_for(read_event(reader), self.timeout)
                if ev is None:
                    raise WyomingError("closed before answering")
                if ev.type == "info":
                    return ev.data
        finally:
            await self._close(writer)

    async def transcribe(self, pcm: bytes, rate: int = 16000, width: int = 2, channels: int = 1,
                         language: str | None = "en") -> str:
        """Raw PCM (16-bit little-endian, mono, 16 kHz by default) -> the transcript text."""
        reader, writer = await self._open()
        fmt = {"rate": rate, "width": width, "channels": channels}
        try:
            await write_event(writer, Event("transcribe", {"language": language} if language else {}))
            await write_event(writer, Event("audio-start", fmt))
            for i in range(0, len(pcm), CHUNK_BYTES):
                await write_event(writer, Event("audio-chunk", fmt, pcm[i:i + CHUNK_BYTES]))
            await write_event(writer, Event("audio-stop"))
            while True:
                ev = await asyncio.wait_for(read_event(reader), self.timeout)
                if ev is None:
                    raise WyomingError("speech-to-text closed without a transcript")
                if ev.type == "transcript":
                    return str(ev.data.get("text") or "").strip()
                if ev.type == "error":
                    raise WyomingError(str(ev.data.get("text") or "speech-to-text error"))
        except asyncio.TimeoutError as e:
            raise WyomingError("speech-to-text timed out") from e
        finally:
            await self._close(writer)

    async def synthesize(self, text: str, voice: str | None = None, speaker: str | None = None) -> "Audio":
        """Text -> audio (whatever rate the voice speaks at)."""
        reader, writer = await self._open()
        data: dict[str, Any] = {"text": text}
        if voice or speaker:
            data["voice"] = {k: v for k, v in (("name", voice), ("speaker", speaker)) if v}
        chunks: list[bytes] = []
        fmt: dict[str, Any] = {}
        try:
            await write_event(writer, Event("synthesize", data))
            while True:
                ev = await asyncio.wait_for(read_event(reader), self.timeout)
                if ev is None:
                    if chunks:
                        break
                    raise WyomingError("text-to-speech closed without audio")
                if ev.type == "audio-start":
                    fmt = ev.data
                elif ev.type == "audio-chunk":
                    fmt = fmt or ev.data
                    chunks.append(ev.payload)
                elif ev.type == "audio-stop":
                    break
                elif ev.type == "error":
                    raise WyomingError(str(ev.data.get("text") or "text-to-speech error"))
        except asyncio.TimeoutError as e:
            raise WyomingError("text-to-speech timed out") from e
        finally:
            await self._close(writer)
        return Audio(b"".join(chunks), int(fmt.get("rate") or 22050), int(fmt.get("width") or 2),
                     int(fmt.get("channels") or 1))


@dataclass
class Audio:
    pcm: bytes
    rate: int = 22050
    width: int = 2
    channels: int = 1

    @property
    def seconds(self) -> float:
        return len(self.pcm) / max(1, self.rate * self.width * self.channels)

    def wav(self) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(self.channels)
            w.setsampwidth(self.width)
            w.setframerate(self.rate)
            w.writeframes(self.pcm)
        return buf.getvalue()


def pcm_from_wav(data: bytes) -> tuple[bytes, int, int, int]:
    """A WAV file's PCM and (rate, width, channels)."""
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.readframes(w.getnframes()), w.getframerate(), w.getsampwidth(), w.getnchannels()
