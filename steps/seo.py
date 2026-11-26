"""Textos para publicar (YouTube + Instagram), listos para copiar y pegar. Sin API.

    python steps/seo.py out/<slug>                                  # una canción (usa brief.json)
    python steps/seo.py playlists/christmas-lofi-with-mishka.json   # playlist (usa desc.txt + chapters.txt)

Escribe en la carpeta: publish_youtube.txt y publish_instagram.txt
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"

BASE_TAGS = ["lofi", "lofi hip hop", "cozy lofi", "lofi piano", "chill music", "study music", "relaxing music",
             "music to study", "calm piano", "mishka lofi"]
THEMES = {  # palabra clave → (tags de YouTube, hashtags)
    "christmas": (["christmas lofi", "christmas music", "christmas piano", "holiday lofi", "cozy christmas"],
                  ["#christmaslofi", "#christmasmusic", "#holidayvibes", "#cozychristmas"]),
    "snow": (["winter lofi", "snowy night", "winter ambience"], ["#winterlofi", "#snowyday"]),
    "winter": (["winter lofi", "winter music"], ["#winterlofi", "#wintervibes"]),
    "rain": (["rainy lofi", "rain sounds", "rainy day music"], ["#rainylofi", "#rainyday"]),
    "fireplace": (["fireplace ambience", "cozy fireplace"], ["#fireplace"]),
    "sleep": (["sleep music", "music for sleeping"], ["#sleepmusic"]),
}
BASE_HASH = ["#lofi", "#lofimusic", "#cozylofi", "#lofipiano", "#studymusic", "#chillvibes", "#relaxingmusic",
             "#cozyvibes", "#mishkalofi", "#dogsofinstagram", "#huskylove"]


def themed(text):
    low = text.lower()
    tags, hashes = [], []
    for k, (t, h) in THEMES.items():
        if k in low:
            tags += t
            hashes += h
    return tags, hashes


def uniq(xs):
    seen, out = set(), []
    for x in xs:
        if x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def yt_tags(tags, limit=480):
    """YouTube acepta ~500 caracteres de tags en total."""
    out, n = [], 0
    for t in uniq(tags):
        if n + len(t) + 1 > limit:
            break
        out.append(t)
        n += len(t) + 1
    return ", ".join(out)


def bullet_lines(desc):
    return [l for l in desc.splitlines() if l and not l[0].isascii() and not l.startswith('"')]


def for_song(folder: Path) -> dict:
    b = json.loads((folder / "brief.json").read_text(encoding="utf-8")) if (folder / "brief.json").exists() else {}
    s = json.loads((folder / "song.json").read_text(encoding="utf-8")) if (folder / "song.json").exists() else {}
    title = b.get("title") or s.get("title") or folder.name
    desc = b.get("description", "")
    if not desc and (folder / "desc.txt").exists():
        desc = (folder / "desc.txt").read_text(encoding="utf-8")
    ctx = f"{title} {desc} {s.get('caption', '')}"
    t_tags, t_hash = themed(ctx)
    hook = desc.strip().splitlines()[0] if desc.strip() else ""
    yt_title = f"{title} 🐾 Cozy LoFi Piano with Mishka"
    yt_desc = desc.strip() + "\n\n" + " ".join(uniq(["#lofi", "#cozylofi"] + t_hash[:1] + ["#mishkalofi"]))
    ig = "\n".join([hook, "", f"🎹 “{title}” — new cozy lofi from Mishka", *bullet_lines(desc)[:3], "",
                    "🎧 Full song on YouTube — link in bio", "", " ".join(uniq(BASE_HASH + t_hash)[:20])])
    return {"yt_title": yt_title, "yt_description": yt_desc,
            "yt_tags": yt_tags((b.get("tags") or []) + t_tags + BASE_TAGS), "ig_caption": ig, "dir": folder}


def for_playlist(pl_path: Path) -> dict:
    pl = json.loads(pl_path.read_text(encoding="utf-8"))
    folder = OUT / "_playlists" / pl["slug"]
    raw = (folder / "desc.txt").read_text(encoding="utf-8") if (folder / "desc.txt").exists() else ""
    m = re.match(r"\s*T[ÍI]TULO:\s*(.+)\n", raw)
    yt_title = m.group(1).strip() if m else f"{pl['title']} 🐾 cozy lofi piano to study & relax"
    body = raw[m.end():].strip() if m else raw.strip()
    chapters = (folder / "chapters.txt").read_text(encoding="utf-8").strip() if (folder / "chapters.txt").exists() else ""
    if "[pega aquí" in body:
        body = re.sub(r"\[pega aquí[^\]]*\]", chapters or "(arma el mix para generar los capítulos)", body)
    elif chapters:
        body += "\n\nTracklist\n" + chapters
    tl = json.loads((folder / "tracklist.json").read_text(encoding="utf-8")) if (folder / "tracklist.json").exists() else {}
    dur = tl.get("duration", "")
    t_tags, t_hash = themed(f"{pl['title']} {body} {pl.get('scene', '')}")
    yt_desc = body + "\n\n" + " ".join(uniq(["#lofi", "#cozylofi"] + t_hash[:2] + ["#studymusic", "#mishkalofi"]))
    hook = body.splitlines()[0] if body else ""
    mins = f"{int(dur.split(':')[0])} min" if dur and dur.count(":") == 1 else "Full mix"
    ig = "\n".join([hook, "", f"🎄 {mins} of cozy lofi with Mishka is out now",
                    *bullet_lines(body)[:3], "", "🎧 Full mix on YouTube — link in bio", "",
                    " ".join(uniq(BASE_HASH + t_hash)[:20])])
    return {"yt_title": yt_title, "yt_description": yt_desc,
            "yt_tags": yt_tags(t_tags + BASE_TAGS + ["lofi mix", "1 hour lofi", "lofi playlist"]),
            "ig_caption": ig, "dir": folder}


def build(target) -> dict:
    target = Path(target)
    d = for_playlist(target) if target.suffix == ".json" else for_song(target)
    d["dir"].mkdir(parents=True, exist_ok=True)
    (d["dir"] / "publish_youtube.txt").write_text(
        f"TÍTULO\n{d['yt_title']}\n\nDESCRIPCIÓN\n{d['yt_description']}\n\nTAGS\n{d['yt_tags']}\n", encoding="utf-8")
    (d["dir"] / "publish_instagram.txt").write_text(d["ig_caption"] + "\n", encoding="utf-8")
    return d


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    d = build(sys.argv[1])
    print("=== YOUTUBE ===\n" + d["yt_title"] + "\n\n" + d["yt_description"] + "\n\nTags: " + d["yt_tags"])
    print("\n=== INSTAGRAM ===\n" + d["ig_caption"])
    print(f"\n→ {d['dir'] / 'publish_youtube.txt'}\n→ {d['dir'] / 'publish_instagram.txt'}")
