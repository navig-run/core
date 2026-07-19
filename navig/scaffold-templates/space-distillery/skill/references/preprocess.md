# Preprocessing video & audio

Claude cannot watch a video or hear audio directly. Convert to something readable — image
frames (which Read *can* view) and/or a text transcript — then run the normal rubric.
**These commands only read the source and write derived files; they never modify or delete
the original.**

Run via the Bash tool. Check a tool exists first (`command -v ffmpeg`). **If it exists, run the
preprocess in this same run** — never hand the user a command you could execute yourself. Only
if it's genuinely missing: ledger the item `blocked` with the exact command as the `reason` and
move on (it's re-tested every run). Write derived files to the session scratchpad, not into the
repo.

## Video → keyframes

```bash
SRC="<path to video>"
OUT="$SCRATCH/frames"   # session scratchpad dir
mkdir -p "$OUT"

# One frame every 2s (good default for pacing/style):
ffmpeg -hide_banner -loglevel error -i "$SRC" -vf "fps=1/2,scale=960:-1" "$OUT/f_%03d.jpg"

# OR scene-change detection (fewer, more meaningful frames):
ffmpeg -hide_banner -loglevel error -i "$SRC" -vf "select='gt(scene,0.4)',scale=960:-1" -vsync vfr "$OUT/scene_%03d.jpg"
```

Then Read `f_001.jpg`, `f_003.jpg`… and extract with the **video** rubric. Note timecodes
(frame N ≈ N×interval seconds). List the frame paths under `Sources` in the note.

Clip metadata (duration, fps, resolution) for the note:
```bash
ffprobe -hide_banner -v error -show_entries format=duration -show_entries stream=width,height,r_frame_rate -of default=noprint_wrappers=1 "$SRC"
```

## Audio → transcript

Speech (talks, voice notes, meeting audio) → transcript, then run the **text** rubric:
```bash
# If whisper is installed (openai-whisper or whisper.cpp):
whisper "$SRC" --model small --output_format txt --output_dir "$SCRATCH/transcript"
```

If no transcriber is available, say so and give the user the command. For music / SFX / ambient
refs there's nothing to transcribe — describe from listening notes or the source page and run
the **audio** (sonic) rubric directly.

## Extract audio from a video (if you only need the sound)
```bash
ffmpeg -hide_banner -loglevel error -i "$SRC" -vn -acodec copy "$SCRATCH/audio.m4a"
```
