# Mishka LoFi — Automation

From an idea to a finished release: song prompts, music, mastering, animated cover and the final video — for single tracks and long playlists.

The Studio UI is in Spanish. Everything runs locally except the parts you already pay for: prompts and animation plans are written in Claude (chat), covers are made in ChatGPT Plus. API keys are optional.

## Mishka LoFi Studio

```
python ui.py      # opens http://localhost:7870
```

| Tab | What it does |
|---|---|
| 💡 **Idea** | Turns an idea into title, YouTube description, tags, cover scene and the ACE-Step prompt. Paste a brief written by Claude in chat, or use the Claude API if a key is set ("Escribir prompts (API)"). |
| 🎵 **Canción** (song) | Generates takes with ACE-Step (caption, structure, BPM, key, duration, seed). |
| 🎚️ **Master** | Lowers drums and bass via stem separation, normalizes loudness and prevents clipping. Before/after player and level report. |
| 📀 **Playlist** | Generates missing tracks, joins the masters into one mix with crossfades, writes YouTube chapters, imports the cover, animates it and renders the long video. |

## Pipeline

| # | Step | Script | Runs on |
|---|---|---|---|
| 0 | **Brief** — idea → title, description, tags, cover scene, `song.json` | `steps/brief.py` | Claude chat (free) or Claude API |
| 1 | **Cover** — Mishka cover from the style guide + reference images | `steps/image.py` | ChatGPT Plus (manual) or OpenAI API |
| 2 | **Music** — takes from ACE-Step 1.5 through its local REST API | `steps/music.py` | Local GPU |
| 3 | **Master** — Demucs splits drums/bass/other/vocals → drums −11 dB, bass −6 dB → −14 LUFS with a 4× oversampled true-peak limiter at −1 dBTP | `steps/master.py` | Local |
| 4 | **Animation** — seamless 10 s loop (snow, rain, fire, lamps, steam, twinkle, blink) from an `anim_spec.json` | `steps/animate.py` + `tools/` | Local (plan from Claude chat or API) |
| 5 | **Playlist mix** — equal-power crossfades, final loudness pass, chapters | `steps/mix.py` | Local |
| 6 | **Video** — the loop repeated over the full audio, H.264 + AAC 320k | `steps/video.py` | Local |

## Setup (Windows)

Requirements: Python 3.11–3.12 (64-bit), `ffmpeg` on PATH (`winget install Gyan.FFmpeg`), ACE-Step 1.5 in `D:\ACE-Step-1.5`.

```
cd /d "D:\Mishka LoFi\Automation"
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
copy .env.example .env
```

The `torch` line lets Demucs use the NVIDIA GPU; without it, stem separation runs on CPU (1–2 min per song, once — stems are cached).

`.env` is only needed for the optional APIs:

```
ANTHROPIC_API_KEY=   # 💡 Idea "Escribir prompts (API)" and automatic animation planning
OPENAI_API_KEY=      # steps/image.py
ACESTEP_API_URL=http://localhost:8001
```

## Daily use

1. Start ACE-Step's API: `D:\ACE-Step-1.5\start_api_server.bat` (keep it open).
2. Start Studio: `python ui.py`.
3. Ask Claude (skill **mishka-lofi-release**) for proposals → paste the brief in 💡 Idea, or let Claude write it straight into `out/`.
4. 🎵 Canción → **Generar** → listen to the takes.
5. 🎚️ Master → pick the best take → **Masterizar**.
6. Cover in ChatGPT → share it with Claude for the animation plan → render the loop.
7. For playlists, 📀 Playlist does steps 4–6 for every track and builds the mix and video.

### Command line

```
python steps/music.py out/<slug>/song.json [--seed 1234] [--no-crackle]
python steps/master.py out/<slug>/songs/take_1234.wav [--drums-db -11 --bass-db -6 --lufs -14 --ceiling -1]
python steps/animate.py check  out/<slug>/cover.png      # draws the animated zones → anim_check.png
python steps/animate.py render out/<slug>/cover.png      # → cover_loop.mp4
python steps/mix.py playlists/christmas-lofi-with-mishka.json --status
python steps/mix.py playlists/christmas-lofi-with-mishka.json
python steps/video.py out/_playlists/christmas-lofi-with-mishka
python steps/brief.py "first snow, Mishka by the window" --variants 3   # needs ANTHROPIC_API_KEY
python steps/image.py --brief out/<slug>/brief.json --n 3              # needs OPENAI_API_KEY
```

## Folder layout

```
config/
  mishka_style.md            official Mishka character & art direction (used in every cover prompt)
  brief_instructions.md      channel style for prompts: caption rules, structure, BPM/keys, description format
  prompts_en_el_chat.md      how Claude should hand over briefs in chat
refs/                        Mishka reference images (official reference first)
steps/                       one script per pipeline step
tools/                       mishka_loop_lib.py + mishka_loop_ext.py (loop animation library)
playlists/<name>.json        playlist definition: title, slug, crossfade, loudness, format, track list
examples/                    sample song.json
out/                         generated material (git-ignored)
  <slug>/song.json, brief.json, desc.txt
  <slug>/songs/take_<seed>.wav, take_<seed>_master.wav/.mp3/.json, takes.json, stems/
  <slug>/cover.png, anim_spec.json, anim_check.png, cover_loop.mp4, video.mp4
  _playlists/<slug>/mix.wav, mix.mp3, chapters.txt, tracklist.json, cover.png, cover_loop.mp4, video.mp4
```

## File formats

**song.json**
```json
{
  "title": "Snowfall by the Fireplace",
  "caption": "warm cozy winter lofi piano, soft felt piano lead, ..., fade-out ending",
  "lyrics": "[Intro - piano]\n[Main Theme - piano and Rhodes]\n[Main Theme - piano]\n[Outro - fade out]",
  "bpm": 66, "key": "F", "time_signature": 4, "duration": 210, "batch_size": 2, "seed": null
}
```
Music defaults match the ACE-Step Gradio UI (turbo model, 8 steps, guidance 7, shift 3, thinking on, LM cfg 2). Any of them can be overridden per song.

**anim_spec.json** — coordinates normalized 0–1000 on the cover, boxes `[x0, y0, x1, y1]`:
```json
{
  "title_boxes": [[120, 65, 880, 240]],
  "effects": [
    {"type": "snow", "window": [700, 0, 1000, 425]},
    {"type": "fire", "box": [744, 520, 915, 660]},
    {"type": "glow", "box": [0, 326, 117, 430], "center": [60, 380]},
    {"type": "steam", "box": [85, 455, 205, 505], "source_y": 500},
    {"type": "blink", "eye_box": [572, 438, 618, 460]}
  ]
}
```

**Master track selection in playlists:** the most recently mastered take of each song is used. Songs with takes but no master get their first take mastered with the defaults.

## Notes

- Masters target −14 LUFS (YouTube reference) with a −1 dBTP ceiling; the report flags any remaining clipped samples. Clipping already baked into an ACE-Step render can't be repaired, only kept from getting worse.
- The animation loop is exactly 10 s / 300 frames; frame 300 equals frame 0, so it repeats without a seam.
- Long videos encode the loop once and copy it N times: a 50-min video takes a few minutes and is ~1.7 GB at 1080p.
- Costs if you use the APIs instead of the chat/ChatGPT workflow: roughly USD 0.70–1.00 per song, almost all of it the cover.
