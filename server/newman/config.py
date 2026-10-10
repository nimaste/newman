"""Environment-driven configuration. Every setting has a sane default except
the secrets/hosts that are genuinely deployment-specific."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Config:
    port: int = field(default_factory=lambda: _int("PORT", 8093))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./data")))
    app_version: str = field(default_factory=lambda: _env("APP_VERSION", "dev"))
    web_dir: str = field(default_factory=lambda: _env("WEB_DIR", "/app/web"))
    # Base URL phones use to reach the hub (snapshot + action links embedded in
    # pushes). PUBLIC_TOKEN_URL optionally points the token-only endpoints
    # (/api/public/*) at a separate no-mTLS host, the way Optiq uses snap.
    public_url: str = field(default_factory=lambda: _env("PUBLIC_URL").rstrip("/"))
    public_token_url: str = field(default_factory=lambda: _env("PUBLIC_TOKEN_URL").rstrip("/"))
    token_secret: str = field(default_factory=lambda: _env("TOKEN_SECRET"))
    # Announcing the hub on the home network (_newman._tcp, for the apps' "Connect to your Newman").
    # MDNS_INTERFACE: comma-separated interface names to announce (default: every non-Docker/VPN one).
    mdns: bool = field(default_factory=lambda: _bool("NEWMAN_MDNS", True))
    mdns_interface: str = field(default_factory=lambda: _env("MDNS_INTERFACE"))

    # MQTT (ring-mqtt)
    mqtt_host: str = field(default_factory=lambda: _env("MQTT_HOST", "10.1.1.2"))
    mqtt_port: int = field(default_factory=lambda: _int("MQTT_PORT", 1883))
    mqtt_username: str = field(default_factory=lambda: _env("MQTT_USERNAME"))
    mqtt_password: str = field(default_factory=lambda: _env("MQTT_PASSWORD"))
    # ring-mqtt only (re)publishes discovery + current states when it sees
    # "online" on its HA status topic. Home Assistant does this on startup;
    # Newman has to do the same or it would never learn the initial states.
    mqtt_birth: bool = field(default_factory=lambda: _bool("MQTT_BIRTH", True))
    mqtt_birth_topic: str = field(default_factory=lambda: _env("MQTT_BIRTH_TOPIC", "homeassistant/status"))
    mqtt_discovery_prefix: str = field(default_factory=lambda: _env("MQTT_DISCOVERY_PREFIX", "homeassistant"))

    # Everything below only seeds the integrations on first boot; after that
    # they're configured in the dashboard (Integrations page).
    # Reolink doorbell
    reolink_host: str = field(default_factory=lambda: _env("REOLINK_HOST"))
    reolink_port: int = field(default_factory=lambda: _int("REOLINK_PORT", 80))
    reolink_https: bool = field(default_factory=lambda: _bool("REOLINK_HTTPS", False))
    reolink_username: str = field(default_factory=lambda: _env("REOLINK_USERNAME", "admin"))
    reolink_password: str = field(default_factory=lambda: _env("REOLINK_PASSWORD"))
    reolink_channel: int = field(default_factory=lambda: _int("REOLINK_CHANNEL", 0))
    doorbell_name: str = field(default_factory=lambda: _env("DOORBELL_NAME", "Front Door"))
    doorbell_cooldown_s: int = field(default_factory=lambda: _int("DOORBELL_COOLDOWN_SECONDS", 15))

    # Push
    apns_key_path: str = field(default_factory=lambda: _env("APNS_KEY_PATH"))
    apns_key_id: str = field(default_factory=lambda: _env("APNS_KEY_ID"))
    apns_team_id: str = field(default_factory=lambda: _env("APNS_TEAM_ID"))
    apns_bundle_id: str = field(default_factory=lambda: _env("APNS_BUNDLE_ID", "ca.nimaste.newman"))
    apns_production: bool = field(default_factory=lambda: _bool("APNS_PRODUCTION", True))
    fcm_service_account_path: str = field(default_factory=lambda: _env("FCM_SERVICE_ACCOUNT_PATH"))

    # Apple Maps (MapKit JS) for the web and wall maps; without a key they use OpenFreeMap.
    # The team id defaults to APNS_TEAM_ID (same Apple Developer account); the key file to APNS_KEY_PATH
    # when MAPKIT_KEY_ID is the APNs key's id (one key with both services), else /app/keys/mapkit.p8.
    mapkit_key_id: str = field(default_factory=lambda: _env("MAPKIT_KEY_ID"))
    mapkit_team_id: str = field(default_factory=lambda: _env("MAPKIT_TEAM_ID"))
    mapkit_key_path: str = field(default_factory=lambda: _env("MAPKIT_KEY_PATH"))

    # Voice (wall tablets): Wyoming speech-to-text (wyoming-whisper) and text-to-speech (wyoming-piper)
    # as host:port; either unset = that half is off. The wake-word model lives in DATA_DIR/voice/.
    voice_stt: str = field(default_factory=lambda: _env("VOICE_STT"))
    voice_tts: str = field(default_factory=lambda: _env("VOICE_TTS"))
    # Text to speech: "wyoming" (Piper at VOICE_TTS) or "openai" (an OpenAI-compatible HTTP service such
    # as Kokoro-FastAPI at VOICE_TTS_URL, with Piper at VOICE_TTS as the fallback when it fails).
    voice_tts_kind: str = field(default_factory=lambda: _env("VOICE_TTS_KIND", "wyoming").lower())
    voice_tts_url: str = field(default_factory=lambda: _env("VOICE_TTS_URL"))
    voice_name: str = field(default_factory=lambda: _env(
        "VOICE_NAME", "af_heart" if _env("VOICE_TTS_KIND").lower() == "openai" else "en_US-lessac-medium"))
    voice_language: str = field(default_factory=lambda: _env("VOICE_LANGUAGE", "en"))
    voice_wakeword: str = field(default_factory=lambda: _env("VOICE_WAKEWORD", "hey_newman"))
    # The speech-to-text container, restarted through the Docker connection when the Whisper hint changes
    # (voice/whisper_restart.py) so it reads the new names; VOICE_STT_RESTART=0 turns that off.
    voice_stt_container: str = field(default_factory=lambda: _env("VOICE_STT_CONTAINER", "newman-whisper"))
    voice_stt_restart: bool = field(default_factory=lambda: _bool("VOICE_STT_RESTART", True))

    @property
    def token_base_url(self) -> str:
        return self.public_token_url or self.public_url


config = Config()
