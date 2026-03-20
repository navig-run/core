"""
NAVIG Voice Module

Provides text-to-speech, speech-to-text, and audio playback capabilities,
inspired by comprehensive voice pipeline patterns.

Features:
- Multi-provider TTS (OpenAI, ElevenLabs, Edge TTS, Google Cloud, Deepgram)
- Multi-provider STT (Deepgram, OpenAI Whisper, local Whisper)
- Cross-platform audio playback with built-in notification sounds
- Automatic provider fallback
- Caching for repeated phrases

Quick Start:
    from navig.voice import speak
    
    # Simple usage (async)
    audio_path = await speak("Hello world!")
    
    # Sync usage
    from navig.voice import speak_sync
    audio_path = speak_sync("Hello world!")

    # Speech-to-text
    from navig.voice import transcribe
    text = await transcribe("recording.wav")

    # Play notification sounds
    from navig.voice import play_notification
    await play_notification("wake")

Providers (TTS):
- edge: Free, no API key required (default)
- openai: High quality, requires OPENAI_API_KEY
- elevenlabs: Premium voices, requires ELEVENLABS_API_KEY
- google_cloud: Google Cloud TTS, requires GOOGLE_CLOUD_API_KEY
- deepgram: Deepgram Aura TTS, requires DEEPGRAM_API_KEY

Providers (STT):
- whisper_api: OpenAI Whisper API (default), requires OPENAI_API_KEY
- deepgram: Deepgram Nova, requires DEEPGRAM_API_KEY
- whisper_local: Local Whisper model, requires openai-whisper package
"""

from navig.voice.playback import (
    # Classes
    NotificationSound,
    list_sounds,
    play_notification,
    play_notification_sync,
    # Functions
    play_sound,
    play_sound_sync,
)
from navig.voice.stt import (
    # Classes
    STT,
    STTConfig,
    STTProvider,
    STTResult,
    get_stt,
    # Functions
    transcribe,
    transcribe_full,
    transcribe_sync,
)
from navig.voice.tts import (
    # Classes
    TTS,
    TTSConfig,
    TTSProvider,
    TTSResult,
    TTSVoice,
    get_tts,
    # Functions
    speak,
    speak_sync,
    synthesize,
)

__all__ = [
    # TTS
    "TTS",
    "TTSConfig",
    "TTSResult",
    "TTSProvider",
    "TTSVoice",
    "speak",
    "speak_sync",
    "synthesize",
    "get_tts",

    # STT
    "STT",
    "STTConfig",
    "STTResult",
    "STTProvider",
    "transcribe",
    "transcribe_full",
    "transcribe_sync",
    "get_stt",

    # Playback
    "NotificationSound",
    "play_sound",
    "play_sound_sync",
    "play_notification",
    "play_notification_sync",
    "list_sounds",
]
