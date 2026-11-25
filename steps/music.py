"""Paso 2 — Canción con ACE-Step 1.5 vía su API REST local.

Antes: en D:\\ACE-Step-1.5 corre  `uv run acestep-api`  (queda en http://localhost:8001).

Uso:
    python steps/music.py out/<slug>/song.json            # genera (batch) con los datos del json
    python steps/music.py out/<slug>/song.json --seed 1234 # regenera con seed fijo
    python steps/music.py out/<slug>/song.json --no-crackle  # quita el crepitar/fuego del caption

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
import argparse, json, os, re, sys, time, urllib.parse
from pathlib import Path

import requests

API = os.getenv("ACESTEP_API_URL", "http://localhost:8001").rstrip("/")
API_KEY = os.getenv("ACESTEP_API_KEY", "")
NOTES = r"[A-G](?:#|b)?"

# Mismos ajustes que usa tu interfaz de Gradio (sacados de Scripts/pilot.json).
# La API por defecto trae thinking=false; Gradio lo tiene en true y cambia mucho el resultado.
DEFAULTS = {
    "inference_steps": 8,
    "guidance_scale": 7.0,
    "shift": 3.0,
    "infer_method": "ode",
    "thinking": True,
    "lm_temperature": 0.85,
    "lm_cfg_scale": 2.0,
    "lm_top_p": 0.9,
    "use_cot_caption": False,
    "use_cot_language": True,
    "constrained_decoding": True,
}


def key_scale(k: str) -> str:
    """'F' → 'F major', 'Am' → 'A minor', 'F# minor' se respeta."""
    k = (k or "").strip()
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
        "key_scale": key_scale(song.get("key", "")),
        "time_signature": str(song.get("time_signature", 4)),
        "audio_duration": song.get("duration", 210),
        "batch_size": song.get("batch_size", 2),
        "vocal_language": "en",
        "audio_format": fmt,
    }
    body.update(DEFAULTS)
    for k in list(DEFAULTS) + ["model"]:  # song.json puede sobrescribir cualquiera
        if k in song:
            body[k] = song[k]
    if seed is not None:
        body.update(seed=int(seed), use_random_seed=False)
    return unwrap(requests.post(f"{API}/release_task", json=body, headers=headers(), timeout=30))["task_id"]


def wait(task_id: str, every=3, timeout=1800):
    t0 = time.time()
    while time.time() - t0 < timeout:
        task = unwrap(requests.post(f"{API}/query_result", json={"task_id_list": [task_id]},
                                    headers=headers(), timeout=30))[0]
        if task["status"] == 1:
            return json.loads(task["result"]) if isinstance(task["result"], str) else task["result"]
        if task["status"] == 2:
            raise RuntimeError(f"ACE-Step falló: {task.get('result')}")
        print(f"  … generando ({int(time.time() - t0)} s)", end="\r")
        time.sleep(every)
    raise TimeoutError("ACE-Step tardó demasiado")


def download(file_field: str, dst: Path):
    url = file_field if file_field.startswith("http") else f"{API}{file_field}"
    r = requests.get(url, headers=headers(), timeout=120)
    r.raise_for_status()
    dst.write_bytes(r.content)


def generate(song: dict, out_dir: Path, seed=None, fmt="wav", log=print) -> list[Path]:
    """Genera las tomas de una canción y las guarda en out_dir/songs/. Devuelve las rutas."""
    try:
        requests.get(f"{API}/health", timeout=5).raise_for_status()
    except Exception:
        raise RuntimeError(f"No encuentro ACE-Step en {API}. En D:\\ACE-Step-1.5 corre start_api_server.bat")
    log(f"♪ {song.get('title', out_dir.name)} — {song.get('bpm')} BPM, {key_scale(song.get('key', ''))}, "
        f"{song.get('duration')} s, batch {song.get('batch_size', 2)}, seed {seed if seed is not None else 'aleatorio'}")
    task = submit(song, seed, fmt)
    items = wait(task)

    out = out_dir / "songs"
    out.mkdir(parents=True, exist_ok=True)
    log_p = out / "takes.json"
    hist = json.loads(log_p.read_text(encoding="utf-8")) if log_p.exists() else []
    seeds = str(items[0].get("seed_value", "")).split(",") if items else []
    files = []
    for i, it in enumerate(items):
        if it.get("status", 1) != 1 or not it.get("file"):
            continue
        s = (seeds[i] if i < len(seeds) else "").strip() or f"{int(time.time())}_{i}"
        dst = out / f"take_{s}.{fmt}"
        download(it["file"], dst)
        hist.append({"file": dst.name, "seed": s, "caption": song["caption"], "metas": it.get("metas"),
                     "dit_model": it.get("dit_model"), "task_id": task, "created": time.strftime("%Y-%m-%d %H:%M")})
        log(f"✓ {dst}  ({it.get('dit_model') or 'modelo por defecto'})")
        files.append(dst)
    log_p.write_text(json.dumps(hist, indent=2, ensure_ascii=False), encoding="utf-8")
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song_json")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--no-crackle", action="store_true")
    ap.add_argument("--format", default="wav", choices=["wav", "flac", "mp3"])
    a = ap.parse_args()

    sp = Path(a.song_json)
    song = json.loads(sp.read_text(encoding="utf-8"))
    if a.no_crackle:
        song["caption"] = strip_crackle(song["caption"])
    seed = a.seed if a.seed is not None else song.get("seed")
    try:
        generate(song, sp.parent, seed, a.format)
    except RuntimeError as e:
        sys.exit(str(e))
    print(f"Elige la mejor toma y anótala en {sp.name} como \"seed\" si quieres regenerarla igual.")


if __name__ == "__main__":
    main()
