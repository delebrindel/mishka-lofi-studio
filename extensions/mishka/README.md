# Mishka for Pi

Draft a Mishka LoFi release with the model selected in Pi. The extension uses Pi's configured provider authentication, including a subscription-backed login when supported by that provider. It does not request or manage separate API keys. Audio generation still uses the local ACE-Step service, not the Pi subscription.

## Try it locally

From the root of a writable checkout of Mishka LoFi Studio, run:

```sh
pi --no-extensions -e ./extensions/mishka
```

Do not use the OpenSrc cache as a working copy.

`--no-extensions` isolates the smoke run from other installed extensions; it does not disable provider authentication. Drop it to load your other extensions.

In Pi, select your provider and model as usual, then run:

```text
/mishka brief Soft rain on a quiet Sunday, felt piano and warm Rhodes
/mishka status
/mishka generate <slug>
```

The brief creates `out/<slug>/brief.json`, `song.json`, and `desc.txt`. Existing releases are never replaced by the brief command. Only the idea and the Studio's brief guide are sent to the selected model. The original Studio can read the generated files.

For generation, start ACE-Step first (default `http://localhost:8001`). The extension invokes Studio's `steps/music.py` using the checkout's `.venv/bin/python`; set `MISHKA_PYTHON` to an absolute interpreter path to override it. For just that step on dome, Python 3.11 and `requests` suffice in the Studio environment:

```sh
uv venv --python 3.11
uv pip install requests
```

Set `ACESTEP_API_URL` in the environment that launches Pi if the server uses a different address. Start with one take on an 8 GB GPU (edit `batch_size` in the generated `song.json`). Use the existing Studio and tools for playback, mastering, cover creation, animation, and final video.

## Verify

Run `npm --prefix extensions/mishka test` from the repository root with a Node version that supports TypeScript stripping (Node 26 on dome). See [live verification](../../docs/modernization/VERIFICATION.md) and the [30-second song fixture](../../examples/layered_rain_smoke.json). No extension runtime dependencies need to be installed. Live model calls count toward the selected provider's normal usage. The extension has been tested on Pi; OMP is not a supported target for this version.
