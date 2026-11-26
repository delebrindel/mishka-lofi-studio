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
| 🎚️ **Master** | Browse songs by playlist (◀ ▶ to move between them, takes preview on select, 🗑 delete a take to `songs/_papelera`). **Repaint** regenerates just a time range of a take (ACE-Step repaint). Lowers drums and bass via stem separation, normalizes loudness and prevents clipping. Before/after player and level report. |
| 📀 **Playlist** | Generates missing tracks, regenerates one track or the whole playlist, joins the masters into one mix with crossfades, writes YouTube chapters, imports the cover, animates it, renders the long video and writes the publishing texts. |

## Pipeline

| # | Step | Script | Runs on |
|---|---|---|---|
| 0 | **Brief** — idea → title, description, tags, cover scene, `song.json` | `steps/brief.py` | Claude chat (free) or Claude API |
| 1 | **Cover** — Mishka cover from the style guide + reference images | `steps/cover_local.py` · `steps/image.py` | Local GPU (ComfyUI, Qwen-Image-Edit) · ChatGPT Plus · OpenAI API |
| 2 | **Music** — takes from ACE-Step 1.5 through its local REST API | `steps/music.py` | Local GPU |
| 3 | **Master** — Demucs splits drums/bass/other/vocals → drums −11 dB, bass −6 dB → −14 LUFS with a 4× oversampled true-peak limiter at −1 dBTP | `steps/master.py` | Local |
| 4 | **Animation** — seamless 10 s loop (snow, rain, fire, lamps, steam, twinkle, blink) from an `anim_spec.json` | `steps/animate.py` + `tools/` | Local (plan from Claude chat or API) |
| 5 | **Playlist mix** — equal-power crossfades or short silent gaps between tracks, 5 s fade in/out at the very start and end, final loudness pass, chapters | `steps/mix.py` | Local |
| 6 | **Video** — the loop repeated over the full audio, H.264 + AAC 320k | `steps/video.py` | Local |
| 7 | **Publish** — YouTube title, description (with chapters), tags and an Instagram caption, ready to copy | `steps/seo.py` | Local |

## Setup (Windows)

Requirements: Python 3.11–3.12 (64-bit), `ffmpeg` on PATH (`winget install Gyan.FFmpeg`), ACE-Step 1.5 in `D:\ACE-Step-1.5`.

```
cd /d "D:\Mishka LoFi\Automation"
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python gpu_check.py
```

`requirements.txt` pins PyTorch 2.11 built for CUDA 12.8 so Demucs runs on the NVIDIA GPU; `gpu_check.py` confirms it (on CPU, stem separation takes 1–2 min per song, once — stems are cached).

`.env` is only needed for the optional APIs:

```
ANTHROPIC_API_KEY=   # 💡 Idea "Escribir prompts (API)" and automatic animation planning
OPENAI_API_KEY=      # steps/image.py
ACESTEP_API_URL=http://localhost:8001
COMFY_URL=http://127.0.0.1:8188
```

## Local covers with ComfyUI

1. Open ComfyUI and load an image-edit workflow that takes reference images (recommended: the official **Qwen-Image-Edit-2511** template with the 4-step Lightning LoRA; GGUF quantizations fit a 12 GB GPU).
2. Put these markers in the workflow fields: `{{PROMPT}}` in the positive prompt text, `{{REF1}}`, `{{REF2}}`… in the *Load Image* nodes (REF1 = official Mishka reference), and `{{WIDTH}}` / `{{HEIGHT}}` in the empty-latent node if there is one. Seeds change automatically.
3. *Workflow → Export (API)* → save as `config/comfy_workflow_api.json`.
4. 📀 Playlist → **Generar con ComfyUI (local)**, or `python steps/cover_local.py <folder> --n 3`. The scene and title come from `cover.json`, `brief.json` or the playlist (`scene`, `cover_title`).

## ACE-Step on a 12 GB GPU (use the 1.7B LM)

ACE-Step classifies exactly 12.0 GB as *tier4* and silently falls back to the small `acestep-5Hz-lm-0.6B` planner. Reporting 12.5 GB moves it to *tier5*, which loads `acestep-5Hz-lm-1.7B` — a clearly better composition planner on an RTX 3060. Start the API with `D:\ACE-Step-1.5\start_api_1.7B.bat`:

```
@echo off
set MAX_CUDA_VRAM=12.5
call start_api_server.bat
```

Check the log for `Configuration Tier: tier5` and no "Falling back … 0.6B". If you ever hit `CUDA out of memory`, generate 1 take at a time or go back to the plain `start_api_server.bat`.

## Daily use

1. Start ACE-Step's API: `D:\ACE-Step-1.5\start_api_1.7B.bat` (keep it open).
2. Start Studio: `python ui.py`.
3. Ask Claude (skill **mishka-lofi-release**) for proposals → paste the brief in 💡 Idea, or let Claude write it straight into `out/`.
4. 🎵 Canción → **Generar** → listen to the takes.
5. 🎚️ Master → pick the best take → **Masterizar**.
6. **Prompt para ChatGPT** button (🎵 Canción or 📀 Playlist) → paste it in ChatGPT with the Mishka references → upload the cover → share it with Claude for the animation plan → render the loop.
7. For playlists, 📀 Playlist does steps 4–6 for every track and builds the mix and video.

### Command line

```
python steps/music.py out/<slug>/song.json [--seed 1234] [--no-crackle]
python steps/music.py out/<slug>/song.json --repaint take_1234.wav --start 90 --end 120   # redo only that range
python steps/master.py out/<slug>/songs/take_1234.wav [--drums-db -11 --bass-db -6 --lufs -14 --ceiling -1]
python steps/animate.py check  out/<slug>/cover.png      # draws the animated zones → anim_check.png
python steps/animate.py render out/<slug>/cover.png      # → cover_loop.mp4
python steps/mix.py playlists/christmas-lofi-with-mishka.json --status
python steps/mix.py playlists/christmas-lofi-with-mishka.json
python steps/video.py out/_playlists/christmas-lofi-with-mishka
python steps/seo.py playlists/christmas-lofi-with-mishka.json   # YouTube + Instagram texts
python steps/brief.py "first snow, Mishka by the window" --variants 3   # needs ANTHROPIC_API_KEY
python steps/chatgpt_prompt.py playlists/christmas-lofi-with-mishka.json  # cover prompt to paste in ChatGPT
python steps/cover_local.py out/_playlists/christmas-lofi-with-mishka --n 3   # local, ComfyUI
python steps/image.py --brief out/<slug>/brief.json --n 3              # needs OPENAI_API_KEY
```

## Folder layout

```
config/
  mishka_style.md            official Mishka character & art direction (used in every cover prompt)
  comfy_workflow_api.json    your ComfyUI workflow exported in API format (with {{PROMPT}}/{{REF1}}… markers)
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

**config/acestep.json** — overrides for every generation (ACE-Step stability settings):
```json
{
  "reference_audio": "out/snowfall-by-the-fireplace/songs/take_1480140451_master.wav",
  "lm_temperature": 0.7,
  "lm_cfg_scale": 2.5
}
```
`reference_audio` (optional) passes a favorite track's timbre and mix to every new generation without copying its melody — the most effective way to keep the channel sound consistent. Each song can override any value in its `song.json`. Generation logs warn about settings the ACE-Step guide calls unstable (BPM/key in the caption, uncommon keys, vocal words, overloaded structure tags, >4 min).

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
