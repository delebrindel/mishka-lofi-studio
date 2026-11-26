"""Prompt listo para pegar en ChatGPT (portada de Mishka), a partir de título + escena.
ChatGPT ya conoce a Mishka y el estilo del canal, así que el prompt solo lleva escena, título y formato.

    python steps/chatgpt_prompt.py out/<slug>                    # usa brief.json / cover.json
    python steps/chatgpt_prompt.py playlists/<playlist>.json     # usa cover_title + scene de la playlist
Escribe chatgpt_prompt.txt junto a la canción o en out/_playlists/<slug>/ y lo imprime.
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"

ICONS = [("christmas", "una pequeña estrella"), ("snow", "un copo de nieve"), ("rain", "una gota"),
         ("tree", "un pequeño pino")]


def build(title: str, scene: str, fmt: str = "vertical", icon: str = "una pata") -> str:
    shape = "HORIZONTAL 16:9 (1920x1080)" if fmt == "horizontal" else "VERTICAL 9:16 (1080x1920)"
    place = ("en el tercio superior izquierdo, sobre una zona oscura y limpia" if fmt == "horizontal"
             else "en el tercio superior, centrado")
    return f"""Nueva portada de Mishka LoFi, formato {shape}.

Escena: {scene}

Título en la imagen: "{title}" en serif color crema con cursivas elegantes, en una o dos líneas {place}; debajo un separador fino con {icon} al centro; debajo "MISHKA LOFI" en mayúsculas espaciadas sans-serif crema. El texto bien escrito y sin tapar la cara de Mishka."""


def for_target(target: Path) -> tuple[str, Path]:
    target = Path(target)
    if target.suffix == ".json" and target.parent.name == "playlists":
        pl = json.loads(target.read_text(encoding="utf-8"))
        dest = OUT / "_playlists" / pl["slug"]
        title, scene, fmt = pl.get("cover_title", pl["title"]), pl.get("scene", ""), pl.get("format", "horizontal")
    else:
        dest = target
        info = {}
        for n in ("cover.json", "brief.json", "song.json"):
            p = target / n
            if p.exists():
                info = {**json.loads(p.read_text(encoding="utf-8")), **info}
        title, scene, fmt = info.get("title", target.name), info.get("scene", ""), info.get("format", "vertical")
    if not scene:
        raise RuntimeError("Falta la escena (campo \"scene\"). Pídesela a Claude o escríbela en cover.json.")
    low = (title + " " + scene).lower()
    icon = next((i for k, i in ICONS if k in low), "una pequeña pata")
    text = build(title, scene, fmt, icon)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "chatgpt_prompt.txt").write_text(text, encoding="utf-8")
    return text, dest / "chatgpt_prompt.txt"


if __name__ == "__main__":
    try:
        text, path = for_target(Path(sys.argv[1]))
    except (RuntimeError, IndexError) as e:
        sys.exit(str(e) or __doc__)
    print(text)
    print(f"\n→ guardado en {path}")
