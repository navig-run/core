# NAVIG Voice Services

## Overview

The `navig.voice` module provides a unified interface for text-to-speech (TTS), speech-to-text (STT), and audio playback. All services support multiple providers with automatic fallback.

## Quick Start

```python
from navig.voice import speak, transcribe, play_notification

# Text-to-Speech
audio_path = await speak("Hello from NAVIG!")

# Speech-to-Text
text = await transcribe("recording.wav")

# Play notification sound
await play_notification("wake")
```

## Text-to-Speech (TTS)

### Providers

| Provider | Env Variable | Quality | Cost | Notes |
|----------|-------------|---------|------|-------|
| `edge` | — | Good | Free | Default, no API key needed |
| `openai` | `OPENAI_API_KEY` | Excellent | Paid | Models: tts-1, tts-1-hd, gpt-4o-mini-tts |
| `elevenlabs` | `ELEVENLABS_API_KEY` | Premium | Paid | Most natural voices |
| `google_cloud` | `GOOGLE_CLOUD_API_KEY` | Excellent | Paid | Neural2 voices |
| `deepgram` | `DEEPGRAM_API_KEY` | Good | Paid | Aura TTS models |

### Usage

```python
from navig.voice import TTS, TTSConfig, TTSProvider

# Default (Edge TTS, free)
tts = TTS()
result = await tts.synthesize("Hello!")

# OpenAI
tts = TTS(TTSConfig(provider=TTSProvider.OPENAI))
result = await tts.synthesize("Hello!", voice="nova")

# Google Cloud
tts = TTS(TTSConfig(
    provider=TTSProvider.GOOGLE_CLOUD,
    google_cloud_voice="en-US-Neural2-C"
))
result = await tts.synthesize("Hello!")

# Deepgram Aura
tts = TTS(TTSConfig(
    provider=TTSProvider.DEEPGRAM,
    deepgram_model="aura-asteria-en"
))
result = await tts.synthesize("Hello!")

# List available voices
voices = await tts.list_voices()
```

## Speech-to-Text (STT)

### Providers

| Provider | Env Variable | Speed | Quality | Notes |
|----------|-------------|-------|---------|-------|
| `whisper_api` | `OPENAI_API_KEY` | Fast | Excellent | Default |
| `deepgram` | `DEEPGRAM_API_KEY` | Very fast | Excellent | Nova-2 model |
| `whisper_local` | — | Slow | Good | Offline, needs `openai-whisper` |

### Usage

```python
from navig.voice import STT, STTConfig, STTProvider

# Default (Whisper API)
stt = STT()
result = await stt.transcribe("audio.wav")
print(result.text)

# Deepgram
stt = STT(STTConfig(provider=STTProvider.DEEPGRAM))
result = await stt.transcribe("audio.mp3")

# Local Whisper (offline)
stt = STT(STTConfig(
    provider=STTProvider.WHISPER_LOCAL,
    whisper_local_model="base"
))
result = await stt.transcribe("audio.wav")
```

## Audio Playback

### Built-in Sounds

| Sound ID | File | Description |
|----------|------|-------------|
| `alarm` | alarm-default.mp3 | Default alarm |
| `alarm_short` | alarm-di.wav | Short beep |
| `analyzing` | Analyzing.mp3 | "Analyzing..." prompt |
| `hello` | Hi.mp3 | Greeting |
| `network_error` | networkError.mp3 | Network error |
| `push_to_talk` | pushToTalk.mp3 | PTT indicator |
| `tts_failed` | tts_failed.mp3 | TTS failure |
| `wait` | waitPlease.mp3 | "Please wait" |
| `wake` | echo_en_wake.wav | Wake chime |
| `ok` | echo_en_ok.wav | Confirmation |
| `end` | echo_en_end.wav | Session end |

### Usage

```python
from navig.voice import play_notification, play_sound, list_sounds

# Play by name
await play_notification("wake")
await play_notification("alarm")

# Play arbitrary file
await play_sound("/path/to/audio.mp3")

# List available sounds
sounds = list_sounds()

# Synchronous
from navig.voice import play_notification_sync
play_notification_sync("ok")
```

### Platform Support

| Platform | WAV | MP3 |
|----------|-----|-----|
| Windows | winsound (built-in) | PowerShell MediaPlayer |
| macOS | afplay | afplay |
| Linux | paplay/aplay | mpv/ffplay |

## Environment Variables

```bash
# TTS Providers
OPENAI_API_KEY=sk-...           # OpenAI TTS + Whisper
ELEVENLABS_API_KEY=...          # ElevenLabs
GOOGLE_CLOUD_API_KEY=...        # Google Cloud TTS
DEEPGRAM_API_KEY=...            # Deepgram TTS + STT
```

Set these in your shell environment or in the NAVIG configuration.
