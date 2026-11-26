"""Paso 7 — Video final: la portada animada (loop de 10 s) repetida durante todo el audio.

    python steps/video.py out/_playlists/christmas-lofi-with-mishka
    python steps/video.py <carpeta> --audio mix.wav --visual cover_loop.mp4
    python steps/video.py out/quiet-christmas-night --audio songs/take_123_master.wav   # una sola canción

Busca en la carpeta, en este orden:
    visual: cover_loop.mp4  →  cover.png / cover.jpg (imagen fija si aún no hay animación)
    audio:  mix.wav  →  el *_master.wav más reciente en songs/
Escribe <carpeta>/video.mp4 (H.264 + AAC 320k, listo para YouTube).

El loop se codifica una sola vez y luego se copia N veces (sin recodificar): 50 min salen en ~1 minuto.
"""
import argparse, json, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from master import run_ffmpeg, span  # noqa: E402


def duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    return float(json.loads(r.stdout)["format"]["duration"])


def find_inputs(folder: Path, audio=None, visual=None):
    if visual:
        v = folder / visual
    else:
        v = next((folder / n for n in ("cover_loop.mp4", "cover.png", "cover.jpg", "cover.webp")
                  if (folder / n).exists()), None)
    if audio:
        a = folder / audio
    elif (folder / "mix.wav").exists():
        a = folder / "mix.wav"
    else:
        ms = sorted((folder / "songs").glob("*_master.wav"), key=lambda p: p.stat().st_mtime)
        a = ms[-1] if ms else None
    if not v or not v.exists():
        raise RuntimeError(f"No hay portada en {folder} (cover_loop.mp4 o cover.png)")
    if not a or not a.exists():
        raise RuntimeError(f"No hay audio en {folder} (mix.wav o un master en songs/)")
    return v, a


def render(folder: Path, audio=None, visual=None, log=print, progress=None) -> Path:
    """progress(fracción 0–1, texto), si se da, recibe el avance de ffmpeg."""
    folder = Path(folder)
    say = progress or (lambda *_: None)
    v, a = find_inputs(folder, audio, visual)
    out = folder / "video.mp4"
    tmp = folder / "video_rendering.mp4"   # se renombra al final: un render interrumpido no deja un video.mp4 cortado
    tmp.unlink(missing_ok=True)
    total = duration(a)
    log(f"🎬 {v.name} + {a.name}  ({int(total // 60)}:{int(total % 60):02d})")

    if v.suffix == ".mp4":
        # 1) codificar el loop una vez, con un solo keyframe al inicio para que se pueda copiar en bucle
        seg = folder / "_loop_seg.mp4"
        say(0.01, "preparando el loop de la animación")
        run_ffmpeg(["ffmpeg", "-y", "-v", "error", "-i", str(v), "-an", "-c:v", "libx264", "-preset", "slow",
                    "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30", "-g", "600", "-keyint_min", "600",
                    "-sc_threshold", "0", "-bf", "0", str(seg)], duration(v), span(progress, 0.0, 0.15),
                   "preparando el loop de la animación")
        lo = 0.15
        # 2) repetirlo sin recodificar y ponerle el audio
        cmd = ["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", str(seg), "-i", str(a),
               "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "320k", "-ar", "48000",
               "-t", f"{total:.3f}", "-movflags", "+faststart", str(tmp)]
    else:
        seg, lo = None, 0.0
        cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", "1", "-i", str(v), "-i", str(a),
               "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-tune", "stillimage", "-crf", "18",
               "-pix_fmt", "yuv420p", "-r", "30", "-g", "300",
               "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-t", f"{total:.3f}", "-movflags", "+faststart", str(tmp)]
    text = f"armando el video ({int(total // 60)}:{int(total % 60):02d} de audio)"
    say(lo, text)
    run_ffmpeg(cmd, total, span(progress, lo, 0.98), text)
    tmp.replace(out)
    if seg:
        seg.unlink(missing_ok=True)
    got = duration(out)
    size = out.stat().st_size / 1e9
    log(f"✓ {out}  —  {int(got // 60)}:{int(got % 60):02d}, {size:.2f} GB")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--audio")
    ap.add_argument("--visual")
    a = ap.parse_args()
    try:
        render(Path(a.folder), a.audio, a.visual)
    except (RuntimeError, subprocess.CalledProcessError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
