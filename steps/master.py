"""Paso 3 — Masterizado: bajar batería y bajo, normalizar y evitar clips (reemplaza UVR + Audacity).

1. Separa la canción en batería / bajo / resto con Demucs (htdemucs, el mismo tipo de modelo que usa
   Ultimate Vocal Remover). Los stems se guardan y se reutilizan: cambiar los dB es instantáneo.
2. Remezcla con la batería y el bajo X dB más bajos (en float, sin recortar nada).
3. Normaliza a un loudness objetivo (LUFS) y limita el pico a un techo seguro (dBTP) → sin clips.
4. Exporta WAV 24-bit + MP3 320 y un reporte con LUFS, true peak y muestras clipeadas.

Uso:
    python steps/master.py out/<slug>/songs/take_1234.wav
    python steps/master.py <wav> --drums-db -8 --bass-db -4 --lufs -14 --ceiling -1.0
    python steps/master.py <wav> --drums-db 0 --bass-db 0   # solo normalizar, sin separar
Escribe junto al wav:  <nombre>_master.wav / .mp3 / _master.json  y  stems/<nombre>/{drums,bass,other,vocals}.wav
"""
import argparse, json, re, shutil, subprocess, sys
from pathlib import Path

import numpy as np
import soundfile as sf

DEFAULTS = dict(drums_db=-11.0, bass_db=-6.0, lufs=-14.0, ceiling=-1.0)
STEMS = ("drums", "bass", "other", "vocals")


# ---------- separación ----------
def model_cached() -> bool:
    """¿Ya está descargado algún modelo de Demucs en la caché de PyTorch?"""
    import os
    hub = Path(os.environ.get("TORCH_HOME", Path.home() / ".cache" / "torch")) / "hub" / "checkpoints"
    return any(hub.glob("*.th"))


def demucs_device() -> tuple[str, str]:
    """('cuda', 'NVIDIA GeForce …') si PyTorch ve la gráfica; si no ('cpu', motivo)."""
    try:
        import torch
    except ImportError:
        return "cpu", "PyTorch no está instalado"
    if torch.cuda.is_available():
        return "cuda", torch.cuda.get_device_name(0)
    why = "PyTorch es versión CPU" if "+cpu" in torch.__version__ or torch.version.cuda is None else "CUDA no disponible"
    return "cpu", f"{why} ({torch.__version__})"


def separate(wav: Path, model="htdemucs") -> dict:
    """Devuelve {stem: ruta} para drums, bass, other, vocals. Usa caché en stems/<nombre>/."""
    stem_dir = wav.parent / "stems" / wav.stem
    paths = {k: stem_dir / f"{k}.wav" for k in STEMS}
    if all(p.exists() and p.stat().st_mtime > wav.stat().st_mtime for p in paths.values()):
        return paths
    tmp = wav.parent / "stems" / "_demucs"
    dev, name = demucs_device()
    print(f"  separando stems de {wav.name} con Demucs ({model}) en {'GPU' if dev == 'cuda' else 'CPU'}: {name}"
          + ("" if model_cached() else " · primera vez: descargando el modelo (~80 MB, solo esta vez)"))
    r = subprocess.run([sys.executable, "-m", "demucs", "-n", model, "-d", dev, "--float32", "-o", str(tmp), str(wav)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Demucs falló:\n{r.stderr[-2000:]}")
    src = tmp / model / wav.stem
    stem_dir.mkdir(parents=True, exist_ok=True)
    for k, p in paths.items():
        shutil.move(str(src / f"{k}.wav"), p)
    shutil.rmtree(tmp, ignore_errors=True)
    return paths


# ---------- medición ----------
def measure(path: Path) -> dict:
    """LUFS integrado y true peak (dBTP) con el filtro ebur128 de ffmpeg."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                        "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    summ = r.stderr[r.stderr.rfind("Summary:"):]
    lufs = float(re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summ).group(1).replace("-inf", "-70"))
    tp = float(re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summ).group(1).replace("-inf", "-70"))
    lra = re.search(r"LRA:\s+(-?[\d.]+) LU", summ)
    return {"lufs": round(lufs, 1), "true_peak_dbtp": round(tp, 2), "lra": float(lra.group(1)) if lra else None}


def clipped_samples(path: Path, thr=0.999) -> int:
    """Cuenta muestras en el límite, leyendo por bloques (sirve para mixes de una hora)."""
    return int(sum((np.abs(b) >= thr).sum() for b in sf.blocks(str(path), blocksize=48000 * 30, dtype="float32")))


# ---------- master ----------
def master(wav: Path, drums_db=DEFAULTS["drums_db"], lufs=DEFAULTS["lufs"], ceiling=DEFAULTS["ceiling"],
           mp3=True, log=print, bass_db=DEFAULTS["bass_db"], fade_in=0.0, fade_out=0.0) -> dict:
    wav = Path(wav)
    base = wav.with_name(f"{wav.stem}_master")
    mix_tmp = wav.with_name(f"{wav.stem}_mix_tmp.wav")

    before = measure(wav)
    # 1–2. remezcla en float (nada se recorta aunque pase de 0 dBFS)
    if abs(drums_db) > 0.05 or abs(bass_db) > 0.05:
        gains = {"drums": drums_db, "bass": bass_db, "other": 0.0, "vocals": 0.0}
        stems = {k: sf.read(p, dtype="float32", always_2d=True) for k, p in separate(wav).items()}
        sr = stems["other"][1]
        n = min(len(x) for x, _ in stems.values())
        mix = sum(x[:n] * (10 ** (gains[k] / 20)) for k, (x, _) in stems.items())
        sf.write(mix_tmp, mix, sr, subtype="FLOAT")
        del stems, mix
        src = mix_tmp
    else:  # solo normalizar: se trabaja directo sobre el archivo, sin cargarlo en memoria
        sr = sf.info(str(wav)).samplerate
        src = wav

    # 3. ganancia hacia el LUFS objetivo + limitador con sobremuestreo 4x (atrapa picos entre muestras)
    m = before if src == wav else measure(src)
    gain = lufs - m["lufs"]
    lim = 10 ** ((ceiling - 0.3) / 20)  # margen extra por la conversión a MP3
    fades = ""
    if fade_in > 0:
        fades += f"afade=t=in:st=0:d={fade_in},"
    if fade_out > 0:
        dur = sf.info(str(src)).duration
        fades += f"afade=t=out:st={max(dur - fade_out, 0):.3f}:d={fade_out},"
    af = (f"{fades}volume={gain:.2f}dB,aresample={sr * 4},"
          f"alimiter=limit={lim:.4f}:attack=3:release=80:level=disabled:asc=1,"
          f"aresample={sr}")
    out_wav = base.with_suffix(".wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", af,
                    "-c:a", "pcm_s24le", str(out_wav)], check=True)
    mix_tmp.unlink(missing_ok=True)

    if mp3:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out_wav), "-c:a", "libmp3lame",
                        "-b:a", "320k", str(base.with_suffix(".mp3"))], check=True)

    after = measure(out_wav)
    rep = {
        "source": wav.name, "drums_db": drums_db, "bass_db": bass_db, "target_lufs": lufs, "ceiling_dbtp": ceiling,
        "before": {**before, "clipped_samples": clipped_samples(wav)},
        "after": {**after, "clipped_samples": clipped_samples(out_wav)},
        "gain_db": round(gain, 2),
    }
    ok = after["true_peak_dbtp"] <= ceiling + 0.05 and rep["after"]["clipped_samples"] == 0
    rep["ok"] = ok
    base.with_suffix(".json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    log(f"  antes:   {before['lufs']} LUFS, pico {before['true_peak_dbtp']} dBTP, "
        f"{rep['before']['clipped_samples']} muestras clipeadas")
    log(f"  después: {after['lufs']} LUFS, pico {after['true_peak_dbtp']} dBTP, "
        f"{rep['after']['clipped_samples']} muestras clipeadas  {'✓' if ok else '⚠ revisar'}")
    rep["files"] = [str(out_wav)] + ([str(base.with_suffix(".mp3"))] if mp3 else [])
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav", nargs="+")
    ap.add_argument("--drums-db", type=float, default=DEFAULTS["drums_db"], help="dB para la batería (negativo = más baja)")
    ap.add_argument("--bass-db", type=float, default=DEFAULTS["bass_db"], help="dB para el bajo (negativo = más bajo)")
    ap.add_argument("--lufs", type=float, default=DEFAULTS["lufs"], help="loudness objetivo (YouTube ≈ -14)")
    ap.add_argument("--ceiling", type=float, default=DEFAULTS["ceiling"], help="techo de true peak en dBTP")
    ap.add_argument("--no-mp3", action="store_true")
    a = ap.parse_args()
    for w in a.wav:
        print(f"🎚 {w}")
        rep = master(Path(w), a.drums_db, a.lufs, a.ceiling, not a.no_mp3, bass_db=a.bass_db)
        print("  → " + "\n  → ".join(rep["files"]))


if __name__ == "__main__":
    main()
