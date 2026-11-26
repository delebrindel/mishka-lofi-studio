# Mishka verification

| Rule | Evidence | Result |
|---|---|---|
| R1 | Node tests verify the three files and reject malformed model data. Live authenticated response saved a release with integer time signature and null seed. | Passed for tested inputs |
| R2 | Pi 1.6.0 RPC used active model `openai-codex/gpt-5.6-luna` and produced all three artifacts. The first live response did not supply a valid integer time signature; the explicit JSON example corrected it. | Passed for this provider/model |
| R3 | Tests check the executable arguments and propagation of a nonzero legacy exit; the live run below produced a WAV via Pi. | Passed with stubbed and live execution |
| R4 | Tests exercise collision, invalid slug and symlinked output. Source inspection: no shell, provider credentials handled by Pi, user/model text never used as a raw file path. | Passed for tested paths; use only a trusted Studio checkout |

Pi loaded the package manifest and registered the command in isolated RPC mode. Three automated tests passed. The legacy Studio and a two-second static-cover render were previously smoke-tested locally. No formal independent audit or sign-off was performed.

## Live validation: Layered Rain

- The supplied Spanish music description was passed verbatim to `/mishka brief` using authenticated OpenAI Codex. Pi saved the release in `modernized/mishka-studio/out/layered-rain`, and `/mishka status layered-rain` read it. The existing guide requires instrumental tracks; the brief translated expressive vocals into expressive felt piano and kept the distinct riff, rhythmic movement and layered arrangement. Cover prompt and YouTube/Instagram exports were produced.
- The demo used a copy of that brief's song settings with a duration of 30 seconds and batch size 1; the original 215-second request remains unchanged. On NixOS, the CUDA wheels needed `/run/opengl-driver/lib` in `LD_LIBRARY_PATH`. The first generation exceeded the test client's window during cold start; after the models warmed up, `/mishka generate layered-rain-smoke` completed in about 50 seconds with local ACE-Step 1.5 and wrote a new WAV. FFprobe verified the same-seed companion take as 30 seconds, stereo PCM at 48 kHz.
- Demucs separated that companion take on the RTX 3070 Laptop GPU; mastering reported -14.1 LUFS, -1.3 dBTP and zero clipped samples. A playlist test joined the same mastered take twice with a 4-second crossfade and emitted a 56-second mix and chapters at 0:00 and 0:26.
- FFprobe confirmed the animation fixture produced a 300-frame, 10-second loop. The playlist renderer muxed that fixture and the mix to a 56-second H.264/AAC MP4. A separate 30-second render used the source reference image and real master. The static renderer rejected the reference image's odd width (941 px); padding the fixture to 942 px fixed the render without changing Studio.
- Not tested: sung vocal generation, the original full-duration two-take batch, generated covers with ComfyUI, AI-generated animation planning, repaint, other providers, and OMP. The playlist animation and static cover are test fixtures. Audio fidelity was not judged by listening.
