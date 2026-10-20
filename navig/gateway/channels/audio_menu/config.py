"""Seed data for the /audio deep menu: providers, models, speeds, formats."""

from __future__ import annotations

PROVIDERS: dict[str, dict] = {
    "openai": {
        "label": "OpenAI",
        "models": {
            "tts-1": {
                "label": "TTS-1 (fast, multilingual)",
                "voices": [
                    "alloy",
                    "echo",
                    "fable",
                    "onyx",
                    "nova",
                    "shimmer",
                    "ash",
                    "coral",
                    "sage",
                ],
            },
            "tts-1-hd": {
                "label": "TTS-1 HD (quality, multilingual)",
                "voices": [
                    "alloy",
                    "echo",
                    "fable",
                    "onyx",
                    "nova",
                    "shimmer",
                    "ash",
                    "coral",
                    "sage",
                ],
            },
            "gpt-4o-mini-tts": {
                "label": "GPT-4o Mini TTS (expressive)",
                "voices": [
                    "alloy",
                    "echo",
                    "fable",
                    "onyx",
                    "nova",
                    "shimmer",
                    "ash",
                    "coral",
                    "sage",
                ],
            },
        },
    },
    "edge": {
        "label": "Edge TTS",
        "models": {
            "edge-neural": {
                "label": "Neural (free, multilingual)",
                "voices": [
                    "en-US-AriaNeural",
                    "en-US-GuyNeural",
                    "en-GB-SoniaNeural",
                    "en-AU-NatashaNeural",
                    "fr-FR-DeniseNeural",
                    "fr-FR-HenriNeural",
                    "de-DE-KatjaNeural",
                    "de-DE-ConradNeural",
                    "ru-RU-SvetlanaNeural",
                    "ru-RU-DmitryNeural",
                    "es-ES-ElviraNeural",
                    "es-ES-AlvaroNeural",
                    "it-IT-ElsaNeural",
                    "it-IT-DiegoNeural",
                    "ar-SA-ZariyahNeural",
                    "zh-CN-XiaoxiaoNeural",
                    "zh-CN-YunxiNeural",
                    "ja-JP-NanamiNeural",
                    "pt-BR-FranciscaNeural",
                ],
            },
        },
    },
    "deepgram": {
        "label": "Deepgram",
        "models": {
            "aura-asteria-en": {"label": "Asteria EN (F)", "voices": []},
            "aura-luna-en": {"label": "Luna EN (F)", "voices": []},
            "aura-stella-en": {"label": "Stella EN (F)", "voices": []},
            "aura-athena-en": {"label": "Athena EN (F)", "voices": []},
            "aura-hera-en": {"label": "Hera EN (F)", "voices": []},
            "aura-zeus-en": {"label": "Zeus EN (M)", "voices": []},
            "aura-orion-en": {"label": "Orion EN (M)", "voices": []},
            "aura-arcas-en": {"label": "Arcas EN (M)", "voices": []},
            "aura-perseus-en": {"label": "Perseus EN (M)", "voices": []},
            "aura-orpheus-en": {"label": "Orpheus EN (M)", "voices": []},
            "aura-helios-en": {"label": "Helios EN-GB (M)", "voices": []},
            "aura-angus-en": {"label": "Angus EN-IE (M)", "voices": []},
        },
    },
    "mimo": {
        "label": "Xiaomi MiMo (official)",
        "models": {
            "mimo-v2.5-tts": {
                "label": "MiMo V2.5 TTS (free in promo, 9 built-in voices)",
                "voices": ["mimo_default", "Mia", "Chloe", "Milo", "Dean"],
            },
            "mimo-v2.5-tts-voiceclone": {
                "label": "Voice Clone (base64 mp3/wav sample)",
                "voices": [],
            },
            "mimo-v2.5-tts-voicedesign": {
                "label": "Voice Design (describe the voice)",
                "voices": [],
            },
        },
    },
    "openrouter": {
        "label": "OpenRouter (Fish Audio)",
        "models": {
            # Measured 2026-10-12: the "-free" variant 404s ("No endpoints
            # found") — the pro id is the only routable one on OpenRouter.
            "fish-audio/s2.1-pro": {
                "label": "Fish Audio S2.1 Pro (multilingual, high quality)",
                "voices": [],
            },
        },
    },
    "google_cloud": {
        "label": "Google Cloud",
        "models": {
            "google-neural": {"label": "Neural2 (multilingual)", "voices": []},
        },
    },
}

SPEEDS: list[float] = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
FORMATS: list[str] = ["mp3", "opus", "aac", "flac", "wav", "pcm"]
VOICES_PER_PAGE: int = 8
