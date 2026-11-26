"""Paso 6 — Playlist: une los masters en un solo mix con crossfade y genera los capítulos de YouTube.

    python steps/mix.py playlists/christmas-lofi-with-mishka.json
    python steps/mix.py playlists/<playlist>.json --status        # solo ver qué falta

playlists/<nombre>.json:
{
  "title": "Christmas LoFi with Mishka", "slug": "christmas-lofi-with-mishka",
  "gap": 1, "crossfade": 4, "fade_in": 5, "fade_out": 5, "lufs": -14, "ceiling": -1.0, "format": "horizontal",
  "tracks": ["first-lights-on-the-tree", "snowfall-by-the-fireplace", ...]   # carpetas de out/
}

Transición entre canciones: "gap" = segundos de silencio (por defecto 1). Con "gap": 0 se usa "crossfade"
(segundos que se enciman las canciones).

Qué toma usa de cada canción: la que masterizaste al último (el *_master.wav más reciente cuya toma original
siga existiendo: si borras o mandas a la papelera una toma, su master deja de contar).
Si una canción tiene tomas pero ninguna masterizada, masteriza la más reciente con los valores por defecto.
El mix completo abre con fade in y cierra con fade out (5 s por defecto).
Escribe en out/_playlists/<slug>/:  mix.wav, mix.mp3, chapters.txt, tracklist.json
"""
import argparse, json, sys
from pathlib import Path, PurePosixPath

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
sys.path.insert(0, str(Path(__file__).parent))
import master  # noqa: E402


def title_of(slug):
    for f in ("song.json", "brief.json"):
        p = OUT / slug / f
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")).get("title", slug)
    return slug.replace("-", " ").title()


def track_status(slug):
    """Tomas de la canción (la más nueva primero) y el master en uso.
    Un master solo cuenta si su toma original sigue en songs/ (si la borras, deja de contar)."""
    songs = OUT / slug / "songs"
    takes = [w for w in songs.glob("*.wav") if not w.stem.endswith("_master")
             and not w.stem.endswith("_mix_tmp")] if songs.exists() else []
    takes.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    names = {t.name for t in takes}
    masters = [m for m in songs.glob("*_master.wav") if m.name.replace("_master.wav", ".wav") in names] \
        if songs.exists() else []
    masters.sort(key=lambda p: p.stat().st_mtime)
    return {"slug": slug, "title": title_of(slug), "has_song_json": (OUT / slug / "song.json").exists(),
            "takes": [t.name for t in takes], "chosen": masters[-1] if masters else None}


def status(pl):
    return [track_status(s) for s in pl["tracks"]]


def mix_state(pl) -> dict:
    """¿El mix armado sigue al día? {"state": "missing"|"ok"|"stale", "reasons": [...], "duration", "built"}"""
    dest = OUT / "_playlists" / pl["slug"]
    mix_wav, tl = dest / "mix.wav", dest / "tracklist.json"
    if not mix_wav.exists() or not tl.exists():
        return {"state": "missing", "reasons": [], "changed": []}
    info = json.loads(tl.read_text(encoding="utf-8"))
    built = mix_wav.stat().st_mtime
    used = [PurePosixPath(t["file"].replace("\\", "/")) for t in info.get("tracks", [])]
    sts = status(pl)
    reasons, changed = [], []
    if len(used) != len(sts):
        reasons.append(f"la playlist tiene {len(sts)} canciones y el mix {len(used)}")
    for i, st in enumerate(sts):
        u = used[i] if i < len(used) else None
        why = None
        gone = u is not None and (not (OUT / u).exists() or
                                  not (OUT / u).with_name(u.name.replace("_master.wav", ".wav")).exists())
        if gone:
            why = f"la toma que está en el mix ({u.stem.replace('_master', '')}) se eliminó"
        elif st["chosen"] is None:
            why = "no tiene toma masterizada"
        elif u is None or u.as_posix() != st["chosen"].relative_to(OUT).as_posix():
            why = f"cambió la toma en uso → {st['chosen'].stem.replace('_master', '')}"
        elif st["chosen"].stat().st_mtime > built:
            why = "se volvió a masterizar después de armar el mix"
        if why:
            reasons.append(f"{st['title']}: {why}")
            changed.append(st["slug"])
    gap = float(pl.get("gap", 1))
    if "gap" in info and (float(info["gap"]) != gap or (gap == 0 and float(info.get("crossfade", 0)) != float(pl.get("crossfade", 4)))):
        reasons.append("cambiaste la transición entre canciones")
    return {"state": "stale" if reasons else "ok", "reasons": reasons, "changed": changed,
            "duration": info.get("duration"), "built": built}


def fmt_time(sec):
    sec = int(round(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build(pl_path: Path, log=print, progress=None) -> dict:
    """progress(fracción 0–1, texto), si se da, recibe el avance."""
    pl = json.loads(Path(pl_path).read_text(encoding="utf-8"))
    say = progress or (lambda *_: None)
    gap = float(pl.get("gap", 1))                      # segundos de silencio entre canciones (por defecto 1)
    xf = 0.0 if gap > 0 else float(pl.get("crossfade", 4))
    dest = OUT / "_playlists" / pl["slug"]
    dest.mkdir(parents=True, exist_ok=True)
    for leftover in dest.glob("mix_raw*"):  # restos de un armado interrumpido
        leftover.unlink(missing_ok=True)

    # 1. elegir (o crear) el master de cada canción
    sts = status(pl)
    missing = [st for st in sts if st["chosen"] is None]
    for st in missing:
        if not st["takes"]:
            raise RuntimeError(f"\"{st['title']}\" todavía no tiene tomas. Genérala primero.")
    p1 = 0.4 if missing else 0.0                       # tramo del avance para masterizar lo que falte
    chosen = []
    k = 0
    for st in sts:
        if st["chosen"] is None:
            log(f"  masterizando {st['title']} ({st['takes'][0]}, la más reciente) con valores por defecto…")
            master.master(OUT / st["slug"] / "songs" / st["takes"][0], log=lambda *_: None,
                          progress=master.span(progress and (lambda f, t, _s=st: progress(f, f"{_s['title']}: {t}")),
                                               p1 * k / len(missing), p1 * (k + 1) / len(missing)))
            k += 1
            st = track_status(st["slug"])
        chosen.append(st)

    # 2. unir con crossfade de igual potencia, escribiendo por partes (no carga 50 min en memoria)
    raw = dest / "mix_raw.wav"
    sr = sf.info(str(chosen[0]["chosen"])).samplerate
    n_xf = int(xf * sr)
    starts, written, tail = [], 0, None
    with sf.SoundFile(raw, "w", sr, 2, subtype="FLOAT") as out:
        for i, st in enumerate(chosen):
            say(p1 + 0.1 * i / len(chosen), f"uniendo canciones ({i + 1}/{len(chosen)}): {st['title']}")
            x, r = sf.read(st["chosen"], dtype="float32", always_2d=True)
            if r != sr:
                raise RuntimeError(f"{st['chosen'].name} está a {r} Hz y el resto a {sr} Hz")
            if x.shape[1] == 1:
                x = np.repeat(x, 2, axis=1)
            if tail is None:
                starts.append(0.0)
            elif xf == 0:                              # sin crossfade: cola completa + silencio
                out.write(tail)
                out.write(np.zeros((int(gap * sr), 2), np.float32))
                written += int(gap * sr)
                starts.append(written / sr)
            else:
                n = min(len(tail), len(x))
                t = np.linspace(0, np.pi / 2, n, dtype=np.float32)[:, None]
                starts.append((written - n) / sr)
                x = np.concatenate([tail[:n] * np.cos(t) + x[:n] * np.sin(t), x[n:]])
                written -= n
            keep = min(n_xf, len(x) // 2) if xf > 0 else min(sr, len(x) // 2)
            out.write(x[:-keep])
            written += len(x)
            tail = x[-keep:]
        out.write(tail)
    total = written / sr

    # 3. pasada final: mismo loudness y techo que las canciones, sin separar stems
    log(f"  normalizando el mix completo ({fmt_time(total)})…")
    rep = master.master(raw, drums_db=0, bass_db=0, lufs=pl.get("lufs", -14), ceiling=pl.get("ceiling", -1.0),
                        log=lambda *_: None, fade_in=pl.get("fade_in", 5), fade_out=pl.get("fade_out", 5),
                        progress=master.span(progress and (lambda f, t: progress(f, f"mix completo: {t}")),
                                             p1 + 0.1, 1.0))
    for ext in ("wav", "mp3", "json"):
        src = raw.with_name(f"mix_raw_master.{ext}")
        if src.exists():
            src.replace(dest / f"mix.{ext}" if ext != "json" else dest / "mix_report.json")
    raw.unlink(missing_ok=True)

    # 4. capítulos para YouTube
    chapters = "\n".join(f"{fmt_time(s)} {st['title']}" for s, st in zip(starts, chosen))
    (dest / "chapters.txt").write_text(chapters + "\n", encoding="utf-8")
    info = {"title": pl["title"], "duration_s": round(total, 1), "duration": fmt_time(total),
            "tracks": [{"title": st["title"], "start": fmt_time(s), "file": str(st["chosen"].relative_to(OUT))}
                       for s, st in zip(starts, chosen)],
            "after": rep["after"], "ok": rep["ok"], "gap": gap, "crossfade": xf}
    (dest / "tracklist.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"✓ {dest / 'mix.wav'}  —  {fmt_time(total)}, {rep['after']['lufs']} LUFS, "
        f"pico {rep['after']['true_peak_dbtp']} dBTP")
    log(chapters)
    info["dir"] = str(dest)
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("playlist")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    pl = json.loads(Path(a.playlist).read_text(encoding="utf-8"))
    if a.status:
        for i, st in enumerate(status(pl), 1):
            mark = "✓ master" if st["chosen"] else ("tomas: %d" % len(st["takes"]) if st["takes"] else "— sin generar")
            print(f"{i:2d}. {st['title']:<32} {mark}")
        return
    try:
        build(Path(a.playlist))
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
