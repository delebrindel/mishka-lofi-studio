"""Paso 8 — Pistas sueltas: guarda cada canción masterizada de la playlist, en orden y con su nombre.

    python steps/export.py playlists/christmas-lofi-with-mishka.json

Escribe en out/_playlists/<slug>/pistas/:
    01 - First Lights on the Tree.wav    master 24-bit (el mismo que entra al mix)
    01 - First Lights on the Tree.mp3    320k con título, número de pista, álbum, artista y portada
    …
    tracklist.txt                        orden, duración y toma de cada pista
Usa la misma toma de cada canción que el mix (la masterizada al último). Si alguna no tiene master,
masteriza la más reciente con los valores por defecto. Al volver a exportar se reemplazan las pistas anteriores.
"""
import argparse, json, re, subprocess, sys, time
from pathlib import Path

import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import master  # noqa: E402
import mix  # noqa: E402

OUT = mix.OUT
ARTIST = "Mishka LoFi"


def safe_name(s: str) -> str:
    """Nombre válido en Windows: sin <>:"/\\|?* ni punto final."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", s).strip().rstrip(".") or "pista"


def folder(pl) -> Path:
    return OUT / "_playlists" / pl["slug"] / "pistas"


def export_state(pl) -> dict:
    """¿Las pistas exportadas siguen al día? {"state": "missing"|"ok"|"stale", "reasons": [...], "n", "when"}"""
    info_p = folder(pl) / "export.json"
    if not info_p.exists():
        return {"state": "missing", "reasons": []}
    info = json.loads(info_p.read_text(encoding="utf-8"))
    when = info_p.stat().st_mtime
    reasons = []
    sts = mix.status(pl)
    used = [t["source"] for t in info.get("tracks", [])]
    if len(used) != len(sts):
        reasons.append(f"la playlist tiene {len(sts)} canciones y se exportaron {len(used)}")
    for i, st in enumerate(sts):
        src = used[i] if i < len(used) else None
        if st["chosen"] is None:
            reasons.append(f"{st['title']}: no tiene toma masterizada")
        elif src != st["chosen"].relative_to(OUT).as_posix():
            reasons.append(f"{st['title']}: cambió la toma en uso")
        elif st["chosen"].stat().st_mtime > when:
            reasons.append(f"{st['title']}: se volvió a masterizar")
    return {"state": "stale" if reasons else "ok", "reasons": reasons, "n": len(used), "when": when}


def _cover_jpg(pl, dest: Path):
    """Portada reducida a 1000 px para incrustarla en los MP3 (None si no hay portada)."""
    d = OUT / "_playlists" / pl["slug"]
    src = next((d / n for n in ("cover.png", "cover.jpg") if (d / n).exists()), None)
    if not src:
        return None
    from PIL import Image
    im = Image.open(src).convert("RGB")
    im.thumbnail((1000, 1000))
    out = dest / "_cover.jpg"
    im.save(out, quality=90)
    return out


def export(pl_path: Path, log=print, progress=None) -> dict:
    """progress(fracción 0–1, texto), si se da, recibe el avance."""
    pl = json.loads(Path(pl_path).read_text(encoding="utf-8"))
    say = progress or (lambda *_: None)
    sts = mix.status(pl)
    sin = [st["title"] for st in sts if not st["takes"]]
    if sin:
        raise RuntimeError(f"Faltan por generar: {', '.join(sin)}. Genéralas primero.")
    dest = folder(pl)
    dest.mkdir(parents=True, exist_ok=True)

    # 1. masterizar lo que falte (igual que el mix)
    todo = [st for st in sts if st["chosen"] is None]
    p1 = 0.5 if todo else 0.0
    for k, st in enumerate(todo):
        log(f"  masterizando {st['title']} ({st['takes'][0]}, la más reciente) con valores por defecto…")
        master.master(OUT / st["slug"] / "songs" / st["takes"][0], log=lambda *_: None,
                      progress=master.span(progress and (lambda f, t, _s=st: progress(f, f"{_s['title']}: {t}")),
                                           p1 * k / len(todo), p1 * (k + 1) / len(todo)))
    sts = mix.status(pl)

    # 2. quitar la exportación anterior (solo lo que escribe este paso)
    for f in [*dest.glob("[0-9][0-9] - *.wav"), *dest.glob("[0-9][0-9] - *.mp3"),
              *dest.glob("[0-9][0-9][0-9] - *.wav"), *dest.glob("[0-9][0-9][0-9] - *.mp3")]:
        f.unlink()

    # 3. copiar en orden con nombre y etiquetas
    cover = _cover_jpg(pl, dest)
    n = len(sts)
    width = max(2, len(str(n)))
    album, year = pl.get("title", pl["slug"]), time.strftime("%Y")
    tracks, lines = [], []
    for i, st in enumerate(sts, 1):
        name = f"{i:0{width}d} - {safe_name(st['title'])}"
        say(p1 + (1 - p1) * (i - 1) / n, f"{i}/{n} · {st['title']}")
        wav = st["chosen"]
        tags = ["-metadata", f"title={st['title']}", "-metadata", f"artist={ARTIST}",
                "-metadata", f"album_artist={ARTIST}", "-metadata", f"album={album}",
                "-metadata", f"track={i}/{n}", "-metadata", f"date={year}", "-metadata", "genre=Lo-Fi"]
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-map", "0:a", "-c", "copy", *tags,
                        str(dest / f"{name}.wav")], check=True)
        mp3 = wav.with_suffix(".mp3")
        src_a = ["-i", str(mp3)] if mp3.exists() else ["-i", str(wav)]
        codec = ["-c:a", "copy"] if mp3.exists() else ["-c:a", "libmp3lame", "-b:a", "320k"]
        art = (["-i", str(cover), "-map", "1:v", "-c:v", "copy", "-disposition:v", "attached_pic",
                "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"] if cover else [])
        subprocess.run(["ffmpeg", "-y", "-v", "error", *src_a, *art[:2], "-map", "0:a", *art[2:], *codec,
                        "-id3v2_version", "3", *tags, str(dest / f"{name}.mp3")], check=True)
        dur = sf.info(str(wav)).duration
        take = wav.stem.replace("_master", "")
        tracks.append({"n": i, "title": st["title"], "file": name, "source": wav.relative_to(OUT).as_posix(),
                       "take": take, "duration": round(dur, 2)})
        lines.append(f"{i:0{width}d}. {st['title']} — {mix.fmt_time(dur)} — {take}")
        log(f"  {name}  ({mix.fmt_time(dur)})")
    if cover:
        cover.unlink(missing_ok=True)
    total = sum(t["duration"] for t in tracks)
    (dest / "tracklist.txt").write_text(f"{album} — {ARTIST}\n{n} pistas · {mix.fmt_time(total)}\n\n"
                                        + "\n".join(lines) + "\n", encoding="utf-8")
    (dest / "export.json").write_text(json.dumps({"album": album, "tracks": tracks}, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
    say(1.0, "listo")
    log(f"✓ {n} pistas en {dest}")
    return {"dir": str(dest), "n": n, "duration": mix.fmt_time(total), "tracks": tracks}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("playlist")
    a = ap.parse_args()
    try:
        export(Path(a.playlist))
    except (RuntimeError, subprocess.CalledProcessError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
