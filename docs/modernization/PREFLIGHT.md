# Mishka preflight

## Answers

1. Scope: “es solo este repo, procede”
2. Build and test duration: open; smoke tests below, no CI supplied.
3. Bespoke infrastructure: open; none visible in the snapshot.
4. Prior attempts: open.
5. Off limits: open; do not edit the legacy source.

## Check 6 — Scope boundary

Standalone Studio source snapshot. No sibling source required or known inbound consumers. ACE-Step and optional ComfyUI are runtime services, not source modules. Creative work is moving to Pi; audio and video processing stay in Python.

| Check | Status | Evidence / next action |
|---|---|---|
| 1. Stack | Ready | 14 Python modules, five JSON files, four Markdown files, four PNGs, and three setup/ignore files. Gradio, Python scripts, JSON and filesystem artifacts; no DB. |
| 2. Analysis | Gaps | Python 3.14 and project Python 3.11 available. No `scc`, `cloc` or `lizard`; use file/line counts or temporarily try `nxctl run scc -- legacy/mishka`. |
| 3. Toolchain | Gaps | All 14 Python modules syntax-compile. Python 3.11 + Gradio 6.32 launched; playlist status and export commands ran. FFmpeg 9.0.2 rendered a two-second H.264/AAC file. No CI or build manifest beyond `requirements.txt`: CUDA 12.8 PyTorch and torchaudio 2.11.0 come from the PyTorch index. Full requirements were not restored. See verification for Pi target smoke. |
| 4. Completeness | Gaps | Local Python imports have corresponding modules. ACE-Step REST service and optional ComfyUI workflow export are external; no deployment descriptors or binary-only executables. JSON examples document the file contracts. |
| 5. Context | Gaps | OpenSrc snapshot has no git history; no production telemetry supplied. |
| 6. Boundary | Ready | Standalone repo per user. |
| 7. Write protection | Gaps | Legacy symlink and its real OpenSrc target lack a matching Claude edit deny in the inspected settings. Claude-only suggested deny rules: `Edit(/legacy/**)` and `Edit(//home/pones/.opensrc/repos/github.com/delebrindel/mishka-lofi-studio/main/**)`. Tool rules cannot prevent arbitrary scripts from opening files; only OS-level read-only access provides that guarantee. |

Discovery ready with gaps; first transform and verification ready for local characterization. Full media parity requires ACE-Step and the optional render dependencies. Defensive review is source-only.
