"""Paso 0 — De una idea a todos los prompts.

Usa Claude Code instalado en tu compu (tu suscripción de Claude, sin API key). Si no está instalado
y hay ANTHROPIC_API_KEY en .env, usa la API. Forzar uno: MISHKA_BRIEF_BACKEND=claude-code | api

    python steps/brief.py "mishka dormida junto a la ventana mientras llueve, domingo en la tarde"
    python steps/brief.py "christmas eve" --variants 3      # 3 propuestas distintas para elegir

Lee config/brief_instructions.md (tu guía de estilo, editable) y los títulos ya usados en out/ para no repetir.
Escribe en out/<slug>/:
    brief.json   título, descripción de YouTube, tags, notas de escena y los datos de la canción
    song.json    listo para steps/music.py y la pestaña 🎵 Canción
    desc.txt     la descripción, que también usa steps/image.py como prompt de la portada
"""
import argparse, json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
OUT = ROOT / "out"
MODEL = os.getenv("MISHKA_CLAUDE_MODEL", "claude-opus-5-5")

SCHEMA = """Return ONLY a JSON array with {n} object(s), no prose, each with exactly these keys:
{{
  "title": "...",
  "description": "full YouTube description following the required format, with real line breaks (\\n)",
  "tags": ["10-15 YouTube tags, lowercase"],
  "scene": "1-2 sentences in English describing the cover scene: where Mishka is, pose, light, what is outside the window, 2-3 props",
  "song": {{
    "caption": "...",
    "lyrics": "[Intro - ...]\\n[Main Theme - ...]\\n...",
    "bpm": 66, "key": "F", "time_signature": 4, "duration": 210, "batch_size": 2
  }}
}}
If several objects are requested, make them clearly different from each other (scene, key, tempo, instrumentation)."""


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def used_titles():
    titles = []
    if OUT.exists():
        for f in OUT.glob("*/brief.json"):
            try:
                titles.append(json.loads(f.read_text(encoding="utf-8"))["title"])
            except Exception:
                pass
    return titles


def parse(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text, re.S) or re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0))
    return data if isinstance(data, list) else [data]


def backend() -> str:
    import claude_local
    want = os.getenv("MISHKA_BRIEF_BACKEND", "auto")
    if want == "api" or (want == "auto" and not claude_local.available() and os.getenv("ANTHROPIC_API_KEY")):
        return "api"
    return "claude-code"


def ask_claude(idea: str, n=1) -> list[dict]:
    guide = (ROOT / "config" / "brief_instructions.md").read_text(encoding="utf-8")
    used = used_titles()
    user = f"Idea for the next Mishka LoFi release:\n{idea}\n\n"
    if used:
        user += "Titles already used (do not repeat or closely imitate): " + "; ".join(used) + "\n\n"
    user += SCHEMA.format(n=n)
    if backend() == "api":
        import anthropic
        msg = anthropic.Anthropic().messages.create(
            model=MODEL, max_tokens=4000, system=guide,
            messages=[{"role": "user", "content": user}],
        )
        return parse("".join(b.text for b in msg.content if b.type == "text"))
    import claude_local
    prompt = ("You write song briefs for the Mishka LoFi channel. Follow this style guide exactly. "
              "Do not use any tools; answer only with the JSON requested.\n\n"
              f"=== STYLE GUIDE ===\n{guide}\n=== END OF GUIDE ===\n\n{user}")
    return parse(claude_local.ask(prompt))


def save(brief: dict, idea: str = "") -> Path:
    d = OUT / slugify(brief["title"])
    d.mkdir(parents=True, exist_ok=True)
    brief = {**brief, "idea": idea}
    song = {"title": brief["title"], **brief["song"], "seed": None}
    (d / "brief.json").write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
    (d / "song.json").write_text(json.dumps(song, indent=2, ensure_ascii=False), encoding="utf-8")
    (d / "desc.txt").write_text(brief["description"].strip() + "\n", encoding="utf-8")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("idea")
    ap.add_argument("--variants", type=int, default=1)
    a = ap.parse_args()
    briefs = ask_claude(a.idea, a.variants)
    for i, b in enumerate(briefs, 1):
        d = save(b, a.idea)
        s = b["song"]
        print(f"\n[{i}] {b['title']}  —  {s['bpm']} BPM, {s['key']}, {s['duration']} s   →  {d}")
        print(f"    {s['caption']}")


if __name__ == "__main__":
    main()
