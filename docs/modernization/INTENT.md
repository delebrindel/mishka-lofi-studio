# Modernization intent: Mishka LoFi Studio

- Date: 2026-11-26
- Goal: transform incrementally.
- From: a Python media pipeline with a Gradio UI and optional direct provider API calls for creative planning.
- To: a TypeScript Pi extension that uses the active session model and its configured provider authentication for creative planning, without requiring a separate API key. Keep the Python media pipeline and local ACE-Step generation; leave Gradio available during the transition.
- First slice: draft the release brief using the Pi session, then hand off to the existing local pipeline. Confirm parity requirements during discovery.
- Source: `legacy/mishka` points to the OpenSrc snapshot of `delebrindel/mishka-lofi-studio`.

## User direction (verbatim)

> la idea es poder usar la subs de pi

> go

## Target versions

No target version selected yet.
