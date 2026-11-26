"""Medidor de tono: ¿la toma suena dentro de su tonalidad?

Mide en el audio (sin modelos, solo FFT):
  · la tonalidad que realmente suena (perfiles de Krumhansl sobre un cromagrama)
  · qué % de la energía armónica cae FUERA de la escala de esa tonalidad  ← lo que se oye como "off"
  · la afinación respecto a La 440 (cents) y si se mueve durante la canción

Con las tomas de Mishka LoFi (nov 2026): las que gustaron dan 4–18 % fuera de escala; las que se
rechazaron por sonar desafinadas, 26–39 %. Por eso:  ✅ ≤ 18.5 %  ·  🟡 18.5–25 %  ·  ⚠️ > 25 %.

    python steps/keycheck.py out/<slug>/songs/take_123.wav [más tomas…]
    python steps/keycheck.py out/<slug>                       # mide y guarda todas las tomas de la canción
"""
import json, sys
from pathlib import Path

import numpy as np
import soundfile as sf

NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
OK, WARN = 18.5, 25.0


def scale(pc, minor):
    return {(pc + s) % 12 for s in ([0, 2, 3, 5, 7, 8, 10] if minor else [0, 2, 4, 5, 7, 9, 11])}


def verdict(out_pct):
    if out_pct is None:
        return ""
    return "✅" if out_pct <= OK else ("🟡" if out_pct <= WARN else "⚠️")


def label(t: dict | None) -> str:
    """'✅ 9% fuera · D' para mostrar junto a una toma."""
    if not t or t.get("fuera") is None:
        return ""
    return f"{verdict(t['fuera'])} {t['fuera']:.0f}% fuera de escala · suena en {t['real']}"


def analyze(path, seconds=200) -> dict:
    """{'real': 'D', 'fuera': 9.0, 'afinacion': 0.3, 'deriva': 1.7, 'corr': 0.94}"""
    info = sf.info(str(path))
    sr = info.samplerate
    x, _ = sf.read(str(path), dtype="float32", always_2d=True, frames=int(sr * min(info.duration, seconds)))
    x = x.mean(1)
    N, hop = 16384, 4096
    win = np.hanning(N).astype(np.float32)
    f = np.fft.rfftfreq(N, 1 / sr)
    lo, hi = np.searchsorted(f, 90), np.searchsorted(f, 2200)
    devs, wts, mids, times = [], [], [], []
    for i in range(int(5 * sr), len(x) - N, hop):
        X = np.abs(np.fft.rfft(x[i:i + N] * win)) + 1e-12
        L = 20 * np.log10(X)
        m = L[lo:hi].max()
        if m < -40:
            continue
        k = np.arange(lo + 1, hi - 1)
        pk = k[(L[k] > L[k - 1]) & (L[k] >= L[k + 1]) & (L[k] > m - 25)]
        if not len(pk):
            continue
        a, b, c = L[pk - 1], L[pk], L[pk + 1]
        fr = (pk + 0.5 * (a - c) / (a - 2 * b + c)) * sr / N   # pico interpolado
        midi = 69 + 12 * np.log2(fr / 440)
        devs.append(midi - np.round(midi)); wts.append(X[pk]); mids.append(midi); times.append(np.full(len(pk), i / sr))
    if not devs:
        return {"real": "?", "fuera": None, "afinacion": None, "deriva": None, "corr": None}
    dev, w, midi, t = (np.concatenate(v) for v in (devs, wts, mids, times))
    z = np.sum(w * np.exp(2j * np.pi * dev)) / w.sum()
    off = np.angle(z) / (2 * np.pi)
    drift = []
    for t0 in np.arange(5, t.max(), 15):
        s = (t >= t0) & (t < t0 + 15)
        if s.sum() > 50:
            zz = np.sum(w[s] * np.exp(2j * np.pi * dev[s])) / w[s].sum()
            drift.append(np.angle(zz) / (2 * np.pi) * 100)
    chroma = np.bincount(np.round(midi - off).astype(int) % 12, weights=w, minlength=12)
    chroma /= chroma.sum()
    corr, root, minor = max((float(np.corrcoef(chroma, np.roll(P, r))[0, 1]), r, mn)
                            for r in range(12) for P, mn in ((MAJ, False), (MIN, True)))
    out = 100 * float(sum(chroma[i] for i in range(12) if i not in scale(root, minor)))
    return {"real": NAMES[root] + ("m" if minor else ""), "fuera": round(out, 1), "afinacion": round(float(off) * 100, 1),
            "deriva": round(float(np.ptp(drift)), 1) if drift else None, "corr": round(corr, 2)}


def measure_song(song_dir: Path, force=False, log=print) -> int:
    """Mide las tomas de out/<slug>/songs/ que no tengan medición y la guarda en takes.json. Devuelve cuántas midió."""
    songs = Path(song_dir) / "songs"
    tj = songs / "takes.json"
    hist = json.loads(tj.read_text(encoding="utf-8")) if tj.exists() else []
    live = {h["file"]: h for h in hist if not h.get("deleted")}
    n = 0
    for wav in sorted(songs.glob("*.wav")):
        if wav.stem.endswith("_master") or wav.stem.endswith("_mix_tmp"):
            continue
        h = live.get(wav.name)
        if h is None:
            h = {"file": wav.name}
            hist.append(h)
            live[wav.name] = h
        if h.get("tono") and not force:
            continue
        try:
            h["tono"] = analyze(wav)
            n += 1
            log(f"  {wav.name}: {label(h['tono'])}")
        except Exception as e:  # noqa: BLE001 — un archivo raro no debe detener lo demás
            log(f"  {wav.name}: no se pudo medir ({e})")
    if n:
        tj.write_text(json.dumps(hist, indent=2, ensure_ascii=False), encoding="utf-8")
    return n


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for a in sys.argv[1:]:
        p = Path(a)
        if p.is_dir():
            print(f"♪ {p.name}")
            measure_song(p, force=True)
        else:
            r = analyze(p)
            print(f"{p.name}: {label(r)} · afinación {r['afinacion']:+.1f} cents")


if __name__ == "__main__":
    main()
