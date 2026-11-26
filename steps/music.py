"""Paso 2 — Canción con ACE-Step 1.5 vía su API REST local.

Antes: en D:\\ACE-Step-1.5 abre  start_api_1_7.bat  (queda en http://localhost:8001).

Uso:
    python steps/music.py out/<slug>/song.json            # genera (batch) con los datos del json
    python steps/music.py out/<slug>/song.json --seed 1234 # regenera con seed fijo
    python steps/music.py out/<slug>/song.json --no-crackle  # quita el crepitar/fuego del caption
    python steps/music.py out/<slug>/song.json --repaint take_123.wav --start 90 --end 120   # rehace solo ese tramo

song.json (lo escribe el paso de brief, o a mano):
{
  "title": "Snowfall by the Fireplace",
  "caption": "warm cozy winter lofi piano, soft felt piano lead, ...",
  "lyrics": "[Intro - piano]\\n[Main Theme - piano and Rhodes]\\n[Main Theme - piano]\\n[Outro - fade out]",
  "bpm": 66, "key": "F", "time_signature": 4, "duration": 210,
  "batch_size": 2, "seed": null
}
Escribe en out/<slug>/songs/:  take_<seed>.wav  (una por variante) + takes.json con seeds y metadatos.
"""
import argparse, json, os, re, sys, time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import keycheck  # noqa: E402

API = os.getenv("ACESTEP_API_URL", "http://localhost:8001").rstrip("/")
API_KEY = os.getenv("ACESTEP_API_KEY", "")
NOTES = r"[A-G](?:#|b)?"

# Base: los ajustes de tu Gradio (Scripts/pilot.json), con cambios para que sea más estable
# (ver docs de ACE-Step 1.5, "Tutorial"): LM menos aleatorio, prompt negativo contra voces/ruido,
# y sin pistas de idioma de voz porque todo es instrumental.
DEFAULTS = {
    "inference_steps": 8,
    "guidance_scale": 7.0,          # en el modelo turbo no tiene efecto; se deja por si usas sft/base
    "shift": 3.0,
    "infer_method": "ode",          # determinista (sde agrega aleatoriedad)
    "thinking": True,
    "lm_temperature": 0.7,          # antes 0.85: menos variación, resultados más conservadores y repetibles
    "lm_cfg_scale": 2.5,            # antes 2.0: se apega más al caption
    "lm_top_p": 0.9,
    "lm_negative_prompt": ("vocals, singing, voice, choir, humming, whistling, rap, spoken words, lyrics, "
                           "harsh distortion, glitch noise, heavy drums, loud kick, bright cymbals, "
                           "aggressive compression, muddy low end, heavy bass, abrupt ending, "
                           "dissonant chords, clashing notes, out of tune, detuned piano, atonal, sudden key change"),
    "use_cot_caption": False,       # no reescribir tu caption
    "use_cot_metas": False,         # BPM y tonalidad los das tú
    "use_cot_language": False,      # instrumental: no hay idioma que detectar
    "constrained_decoding": True,
    "use_constrained_decoding": True,
}
SETTINGS = Path(__file__).resolve().parent.parent / "config" / "acestep.json"
# Tonalidades que ACE-Step sí respeta (medido en 72 tomas, nov 2026: con tonalidad natural respetó 44 de 46;
# con bemol, 1 de 26, y esas tomas suenan fuera de tono). Las de bemol se convierten un semitono abajo.
COMMON_KEYS = {"C major", "G major", "D major", "F major", "A major", "E major",
               "A minor", "E minor", "D minor", "B minor", "G minor", "C minor"}
FLAT_MAP = {"Eb major": "D major", "Bb major": "A major", "Ab major": "G major", "Db major": "C major",
            "Gb major": "F major", "Cb major": "B major", "Eb minor": "D minor", "Bb minor": "A minor",
            "Ab minor": "G minor", "Db minor": "C minor", "Gb minor": "E minor", "F minor": "E minor"}


def settings() -> dict:
    """DEFAULTS + lo que pongas en config/acestep.json (incluye reference_audio para el sonido del canal)."""
    d = dict(DEFAULTS)
    if SETTINGS.exists():
        d.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
    return d


def lint(song: dict) -> list[str]:
    """Avisos de cosas que, según la guía de ACE-Step, vuelven inestable la generación."""
    w = []
    cap = song.get("caption", "").lower()
    if re.search(r"\b\d{2,3}\s*bpm\b|\bbpm\b|\bin [a-g](#|b)? (major|minor)\b|\bkey of\b", cap):
        w.append("el caption menciona BPM o tonalidad: van en sus campos, no en el caption")
    if any(v in cap for v in ("vocal", "singing", "voice", "choir")) and "no vocal" not in cap:
        w.append("el caption menciona voces y la canción es instrumental")
    if any(w in cap for w in ("half-diminished", "jazzy", "dissonan", "chromatic", "tension")):
        w.append("el caption pide armonía con tensión (half-diminished / jazzy): suele dar acordes discordantes; "
                 "mejor «simple warm chords that stay in key»")
    n_words = len(cap.split())
    if n_words > 125:
        w.append(f"caption largo ({n_words} palabras): los ejemplos oficiales van de ~85 a ~110")
    if "instrumental" not in cap:
        w.append("el caption no dice «instrumental»: el modelo asume voces si no se lo dices")
    if len(re.findall(r"far in the background", cap)) > 2:
        w.append("más de 2 sonidos ambientales: el modelo puede mezclarlos raro")
    for tag in re.findall(r"\[([^\]]+)\]", song.get("lyrics", "")):
        if tag.count(",") + tag.count(" and ") > 1:
            w.append(f"etiqueta cargada [{tag}]: con muchas cosas en un corchete el modelo puede confundirse")
    bpm = song.get("bpm") or 0
    if bpm and not 60 <= bpm <= 180:
        w.append(f"{bpm} BPM está fuera del rango más estable (60–180)")
    k = key_scale(song.get("key", ""))
    if k in FLAT_MAP:
        w.append(f"{k}: ACE-Step casi nunca respeta tonalidades con bemol y esas tomas suenan fuera de tono; "
                 f"se generará en {FLAT_MAP[k]} (cámbiala en la canción para que quede así)")
    elif k and k not in COMMON_KEYS:
        w.append(f"{k} es una tonalidad poco común: C, D, F, G, A, E, Am, Em, Dm, Bm son las más confiables")
    if (song.get("duration") or 0) > 240:
        w.append("más de 4 min puede repetirse o perder estructura")
    return w


def safe_key(k: str) -> str:
    """Tonalidad que se manda a ACE-Step: las de bemol se cambian por la natural de un semitono abajo."""
    ks = key_scale(k)
    return FLAT_MAP.get(ks, ks)


def key_scale(k: str) -> str:
    """'F' → 'F major', 'Am' → 'A minor', 'F# minor' se respeta."""
    k = (k or "").strip().replace("♭", "b").replace("♯", "#")
    if not k or re.search(r"major|minor", k, re.I):
        return k
    m = re.fullmatch(rf"({NOTES})(m|min)?", k)
    if not m:
        return k
    return f"{m.group(1)} {'minor' if m.group(2) else 'major'}"


def strip_crackle(caption: str) -> str:
    parts = [p.strip() for p in caption.split(",")]
    return ", ".join(p for p in parts if not re.search(r"crackl|fire ?place crackle|vinyl crackle", p, re.I))


def headers():
    return {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}


def unwrap(r: requests.Response):
    r.raise_for_status()
    j = r.json()
    if j.get("error"):
        raise RuntimeError(j["error"])
    return j["data"]


def submit(song: dict, seed, fmt="wav") -> str:
    body = {
        "prompt": song["caption"],
        "lyrics": song.get("lyrics", "[Instrumental]"),
        "bpm": song.get("bpm"),
        "key_scale": safe_key(song.get("key", "")),
        "time_signature": str(song.get("time_signature", 4)),
        "audio_duration": song.get("duration", 210),
        "batch_size": song.get("batch_size", 2),
        "vocal_language": "unknown",   # instrumental: no empujar al modelo hacia voces en inglés
        "audio_format": fmt,
    }
    conf = settings()
    body.update({k: v for k, v in conf.items() if k != "reference_audio"})
    for k in list(conf) + ["model"]:  # song.json puede sobrescribir cualquiera
        if k in song and k != "reference_audio":
            body[k] = song[k]
    ref = song.get("reference_audio", conf.get("reference_audio"))
    if ref:  # timbre y mezcla de una canción tuya que te encante (no copia la melodía)
        rp = Path(ref) if Path(ref).is_absolute() else SETTINGS.parent.parent / ref
        if rp.exists():
            body["reference_audio_path"] = str(rp.resolve())
    if seed is not None:
        body.update(seed=int(seed), use_random_seed=False)
    return unwrap(requests.post(f"{API}/release_task", json=body, headers=headers(), timeout=30))["task_id"]


STAGES = [  # etapas que reporta ACE-Step → texto para la UI
    ("queued", "en la fila de ACE-Step"),
    ("Phase 1", "el LM está pensando la canción"),
    ("Phase 2", "el LM está escribiendo la música"),
    ("Preparing inputs", "preparando la generación"),
    ("Generating music", "generando el audio"),
    ("Decoding audio", "decodificando el audio"),
    ("Preparing audio", "guardando el audio"),
    ("succeeded", "listo"),
    ("running", "arrancando"),
]


def stage_es(stage: str) -> str:
    for k, v in STAGES:
        if stage and k.lower() in stage.lower():
            return v
    return stage or "trabajando"


def task_progress(task: dict):
    """(fracción 0–1 o None, etapa) de un task en curso, según lo que reporta /query_result."""
    try:
        r = json.loads(task["result"]) if isinstance(task.get("result"), str) else task.get("result")
        first = r[0] if isinstance(r, list) and r and isinstance(r[0], dict) else {}
    except (TypeError, ValueError):
        first = {}
    frac = first.get("progress")
    return (float(frac) if frac is not None else None), stage_es(first.get("stage") or "")


def wait(task_id: str, every=2, timeout=1800, progress=None):
    """Espera el resultado. progress(fracción, texto) recibe el avance que reporta ACE-Step."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        task = unwrap(requests.post(f"{API}/query_result", json={"task_id_list": [task_id]},
                                    headers=headers(), timeout=30))[0]
        if task["status"] == 1:
            return json.loads(task["result"]) if isinstance(task["result"], str) else task["result"]
        if task["status"] == 2:
            raise RuntimeError(f"ACE-Step falló: {task.get('result')}")
        frac, stage = task_progress(task)
        if progress:
            progress(frac, stage)
        else:
            pct = f"{frac * 100:.0f}% · " if frac else ""
            print(f"  … {pct}{stage} ({int(time.time() - t0)} s)      ", end="\r")
        time.sleep(every)
    raise TimeoutError("ACE-Step tardó demasiado")


def download(file_field: str, dst: Path):
    url = file_field if file_field.startswith("http") else f"{API}{file_field}"
    r = requests.get(url, headers=headers(), timeout=120)
    r.raise_for_status()
    dst.write_bytes(r.content)


def server_info(timeout=2) -> dict | None:
    """Lo que reporta /health (modelos cargados, LM) o None si ACE-Step no responde."""
    try:
        r = requests.get(f"{API}/health", timeout=timeout)
        r.raise_for_status()
        j = r.json()
        return j.get("data", j)
    except Exception:
        return None


def stats() -> dict:
    """/v1/stats: trabajos en fila y segundos promedio por trabajo ({} si no responde)."""
    try:
        r = requests.get(f"{API}/v1/stats", headers=headers(), timeout=2)
        r.raise_for_status()
        j = r.json()
        return j.get("data", j) or {}
    except Exception:
        return {}


def check_server():
    if server_info(timeout=5) is None:
        raise RuntimeError(f"ACE-Step no responde en {API}. Ábrelo con start_api_1_7.bat (en D:\\ACE-Step-1.5), "
                           "espera a que diga que el servidor está listo y vuelve a intentar.")


def save_takes(items, out: Path, song: dict, task: str, fmt: str, log, extra=None, progress=None) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    log_p = out / "takes.json"
    hist = json.loads(log_p.read_text(encoding="utf-8")) if log_p.exists() else []
    seeds = str(items[0].get("seed_value", "")).split(",") if items else []
    files = []
    for i, it in enumerate(items):
        if it.get("status", 1) != 1 or not it.get("file"):
            continue
        if progress:
            progress(0.99, f"descargando toma {i + 1} de {len(items)}")
        s = (seeds[i] if i < len(seeds) else "").strip() or f"{int(time.time())}_{i}"
        dst = out / f"take_{s}.{fmt}"
        if dst.exists():  # mismo seed que una toma anterior: no la pises
            dst = out / f"take_{s}_{int(time.time())}.{fmt}"
        download(it["file"], dst)
        entry = {"file": dst.name, "seed": s, "caption": song["caption"], "metas": it.get("metas"),
                 "dit_model": it.get("dit_model"), "task_id": task, "created": time.strftime("%Y-%m-%d %H:%M"),
                 **(extra or {})}
        try:
            if progress:
                progress(0.995, f"midiendo el tono de la toma {i + 1}")
            entry["tono"] = keycheck.analyze(dst)
        except Exception:  # noqa: BLE001 — medir es un extra: si falla, la toma se guarda igual
            pass
        hist.append(entry)
        log(f"✓ {dst}  ({it.get('dit_model') or 'modelo por defecto'})  {keycheck.label(entry.get('tono'))}")
        files.append(dst)
    log_p.write_text(json.dumps(hist, indent=2, ensure_ascii=False), encoding="utf-8")
    return files


def source_key(src: Path) -> str | None:
    """Tonalidad en la que realmente suena una toma ('F# major'), medida o leída de takes.json."""
    tj = src.parent / "takes.json"
    t = None
    if tj.exists():
        t = next((h.get("tono") for h in reversed(json.loads(tj.read_text(encoding="utf-8")))
                  if h.get("file") == src.name and not h.get("deleted")), None)
    try:
        t = t or keycheck.analyze(src)
    except Exception:  # noqa: BLE001
        return None
    real = (t or {}).get("real") or "?"
    return None if real == "?" else key_scale(real)


def repaint(song: dict, src: Path, start: float, end: float, seed=None, variants=2, fmt="wav", log=print,
            progress=None) -> list[Path]:
    """Regenera solo el tramo [start, end] (segundos) de una toma; el resto queda igual."""
    check_server()
    src = Path(src)
    log(f"🎨 repaint de {src.name}: {start:.0f}s → {end:.0f}s, {variants} variantes")
    ks = source_key(src) or safe_key(song.get("key", ""))
    log(f"🎹 el tramo se pide en {ks}, la tonalidad en la que suena la toma")
    data = {
        "task_type": "repaint", "prompt": song["caption"], "lyrics": song.get("lyrics", "[Instrumental]"),
        "repainting_start": f"{start:.2f}", "repainting_end": f"{end:.2f}", "chunk_mask_mode": "explicit",
        "bpm": str(song.get("bpm", "")), "key_scale": ks,
        "time_signature": str(song.get("time_signature", 4)), "batch_size": str(variants),
        "audio_format": fmt, "vocal_language": "unknown",
    }
    for k in ("inference_steps", "guidance_scale", "shift", "infer_method"):
        data[k] = str(song.get(k, DEFAULTS[k]))
    if seed is not None:
        data.update(seed=str(int(seed)), use_random_seed="false")
    with open(src, "rb") as f:
        r = requests.post(f"{API}/release_task", data=data, files={"src_audio": (src.name, f, "audio/wav")},
                          headers=headers(), timeout=120)
    task = unwrap(r)["task_id"]
    items = wait(task, progress=progress)
    return save_takes(items, src.parent, song, task, fmt, log, progress=progress,
                      extra={"repaint_of": src.name, "repaint_range": [round(start, 2), round(end, 2)]})


def generate(song: dict, out_dir: Path, seed=None, fmt="wav", log=print, progress=None) -> list[Path]:
    """Genera las tomas de una canción y las guarda en out_dir/songs/. Devuelve las rutas."""
    check_server()
    for w in lint(song):
        log(f"⚠ {w}")
    log(f"♪ {song.get('title', out_dir.name)} — {song.get('bpm')} BPM, {safe_key(song.get('key', ''))}, "
        f"{song.get('duration')} s, batch {song.get('batch_size', 2)}, seed {seed if seed is not None else 'aleatorio'}")
    task = submit(song, seed, fmt)
    items = wait(task, progress=progress)
    return save_takes(items, out_dir / "songs", song, task, fmt, log, progress=progress)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song_json")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--no-crackle", action="store_true")
    ap.add_argument("--format", default="wav", choices=["wav", "flac", "mp3"])
    ap.add_argument("--repaint", help="toma (en songs/) de la que se rehace un tramo")
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--end", type=float, default=-1)
    a = ap.parse_args()

    sp = Path(a.song_json)
    song = json.loads(sp.read_text(encoding="utf-8"))
    if a.no_crackle:
        song["caption"] = strip_crackle(song["caption"])
    seed = a.seed if a.seed is not None else song.get("seed")
    try:
        if a.repaint:
            repaint(song, sp.parent / "songs" / a.repaint, a.start, a.end, a.seed, fmt=a.format)
            return
        generate(song, sp.parent, seed, a.format)
    except RuntimeError as e:
        sys.exit(str(e))
    print(f"Elige la mejor toma y anótala en {sp.name} como \"seed\" si quieres regenerarla igual.")


if __name__ == "__main__":
    main()
