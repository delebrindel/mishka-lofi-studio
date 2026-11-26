# Mishka modernization: first slice

## Assessment and boundary

Mishka is a standalone Python/Gradio Studio. Its media steps already have CLI boundaries; a rewrite would risk changing audio and video behavior without improving provider access. The direct Anthropic path in `steps/brief.py` is the first migration seam. Keep the old source unmodified and ship a separate package in `extensions/mishka`.

| Flow | Source | Target in this slice |
|---|---|---|
| Plan a release | `config/brief_instructions.md` + `steps/brief.py` | `/mishka brief` calls Pi's active model registry and writes the existing release artifacts |
| Inspect a release | Gradio and playlist status | `/mishka status [slug]` lists song-level artifacts and WAV counts |
| Generate takes | `steps/music.py` → ACE-Step → `songs/` | `/mishka generate <slug>` passes the existing song JSON to the same CLI |
| Select takes, master, mix, make cover, animate, render | Gradio and `steps/` | Unchanged, out of this slice |

## Rules and intentional differences

- **R1 — Artifact contract:** preserve the title-based slug, saved idea, flattened song with a null seed, and trimmed description plus trailing LF. Require a valid single release before writing anything.
- **R2 — Provider:** only the active Pi model resolves authentication; do not read provider keys or invoke the legacy Anthropic client.
- **R3 — Media handoff:** pass the song's path as a subprocess argument to the existing generator; do not claim success if it exits with an error.
- **R4 — Preserve existing data:** reject duplicate slugs, invalid paths, symlinked output directories and writes from the OpenSrc snapshot. Use a trusted working copy.
- One release per call, no variants or historical-title prompt. Collisions are rejected instead of silently overwriting files. Status is song-level, not the original playlist dashboard. OMP and other creative-planning flows were not migrated.
