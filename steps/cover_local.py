"""Paso 1 (local) — Portada de Mishka con ComfyUI, en tu propia GPU. Sin API de pago.

Antes:
  1. Abre ComfyUI (queda en http://127.0.0.1:8188).
  2. Arma o abre un workflow de edición con referencias (recomendado: plantilla oficial Qwen-Image-Edit-2511
     + LoRA Lightning 4 pasos) y pon estos marcadores en los campos:
        texto del prompt positivo ........ {{PROMPT}}
        imagen en cada nodo Load Image ... {{REF1}}, {{REF2}}, {{REF3}} …  (REF1 = referencia oficial)
        ancho / alto (si hay latente) .... {{WIDTH}} / {{HEIGHT}}
     El seed se cambia solo en cada variante.
  3. Menú Workflow → Export (API) → guárdalo como  config/comfy_workflow_api.json

Uso:
    python steps/cover_local.py out/_playlists/christmas-lofi-with-mishka --n 3
    python steps/cover_local.py out/quiet-christmas-night --format vertical
Lee de la carpeta: brief.json (title + scene) o cover.json  {"title": "...", "scene": "...", "format": "horizontal"}
Escribe: cover_v1.png, cover_v2.png …  (ajustadas a 1920x1080 o 1080x1920) y cover_prompt_local.txt
"""
import argparse, copy, json, os, random, sys, time, uuid
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
COMFY = os.getenv("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
WORKFLOW = ROOT / "config" / "comfy_workflow_api.json"
# tamaños nativos de Qwen-Image (múltiplos de 16, ~1.3 MP); luego se ajustan al formato final
SIZES = {"horizontal": ((1664, 928), (1920, 1080)), "vertical": ((928, 1664), (1080, 1920))}

CHARACTER = (
    "Mishka is the dog from the first reference image: a fluffy spitz-type dog (like a Siberian husky or Finnish "
    "lapphund) with upright triangular ears, a long canine muzzle, black nose and icy blue eyes; cream and white fur "
    "on muzzle, chest and legs, charcoal-grey and black saddle on the back and shoulders, warm apricot and golden fur "
    "on the head, ears and hindquarters, a huge fluffy tail. Calm, contemplative, slightly wistful expression. "
    "Keep her exactly as in the reference: same markings, colors, eye color and proportions. Never a cat or a fox."
)
STYLE = (
    "Cozy hand-painted lo-fi illustration, cinematic lighting, nostalgic atmosphere, delicate painterly details. "
    "Warm amber and wood tones indoors, cool blues and muted purples outside. Match the art style of the other "
    "reference images."
)


def title_block(title):
    return (f'Text in the image: the title "{title}" in an elegant cream serif with italic swashes, one or two lines, '
            "on a dark clean area in the upper part; below it a thin ornamental divider with a tiny paw icon in the "
            'middle; below that "MISHKA LOFI" in widely spaced cream uppercase sans-serif. Spell the text exactly. '
            "The text never covers Mishka's face.")


def build_prompt(title, scene, fmt, with_title=True):
    shape = "Wide horizontal 16:9 composition." if fmt == "horizontal" else "Tall vertical 9:16 composition."
    parts = [f"Create a new illustration. {CHARACTER}", STYLE, f"Scene: {scene}", shape]
    if with_title:
        parts.append(title_block(title))
    else:
        parts.append("No text anywhere in the image.")
    return "\n\n".join(parts)


def upload(path: Path) -> str:
    with open(path, "rb") as f:
        r = requests.post(f"{COMFY}/upload/image", files={"image": (path.name, f, "image/png")},
                          data={"overwrite": "true"}, timeout=60)
    r.raise_for_status()
    j = r.json()
    return f"{j['subfolder']}/{j['name']}" if j.get("subfolder") else j["name"]


def fill(wf, values: dict, seed: int):
    """Sustituye {{MARCADORES}} y cambia todos los seeds."""
    wf = copy.deepcopy(wf)
    found = set()
    for node in wf.values():
        for k, v in list(node.get("inputs", {}).items()):
            if isinstance(v, str) and "{{" in v:
                for key, val in values.items():
                    tag = "{{" + key + "}}"
                    if v.strip() == tag:          # campo completo → conserva el tipo (int para WIDTH/HEIGHT)
                        v = val
                        found.add(key)
                        break
                    if tag in v:
                        v = v.replace(tag, str(val))
                        found.add(key)
                node["inputs"][k] = v
            elif k in ("seed", "noise_seed") and isinstance(v, int):
                node["inputs"][k] = seed
    if "PROMPT" not in found:
        raise RuntimeError("El workflow no tiene el marcador {{PROMPT}} en el texto del prompt.")
    return wf, found


def run(wf, timeout=900) -> list[bytes]:
    r = requests.post(f"{COMFY}/prompt", json={"prompt": wf, "client_id": uuid.uuid4().hex}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"ComfyUI rechazó el workflow: {r.text[:800]}")
    pid = r.json()["prompt_id"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = requests.get(f"{COMFY}/history/{pid}", timeout=30).json()
        if pid in h:
            entry = h[pid]
            st = entry.get("status", {})
            if st.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI falló: {json.dumps(st.get('messages', ''))[:800]}")
            imgs = [im for out in entry.get("outputs", {}).values() for im in out.get("images", [])
                    if im.get("type") == "output"]
            if imgs or st.get("completed"):
                return [requests.get(f"{COMFY}/view", params=im, timeout=60).content for im in imgs]
        time.sleep(2)
    raise TimeoutError("ComfyUI tardó demasiado")


def fit(src: Image.Image, w, h) -> Image.Image:
    im = src.convert("RGB")
    r, tr = im.width / im.height, w / h
    if r > tr:
        nw = int(im.height * tr); x = (im.width - nw) // 2; im = im.crop((x, 0, x + nw, im.height))
    else:
        nh = int(im.width / tr); y = int((im.height - nh) * 0.35); im = im.crop((0, y, im.width, y + nh))
    return im.resize((w, h), Image.LANCZOS)


def info_for(folder: Path) -> dict:
    for name in ("cover.json", "brief.json"):
        p = folder / name
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("scene"):
                return d
    raise RuntimeError(f"No hay escena: crea {folder / 'cover.json'} con title y scene, o un brief.json con scene.")


def generate(folder: Path, n=2, fmt=None, with_title=True, log=print) -> list[Path]:
    folder = Path(folder)
    if not WORKFLOW.exists():
        raise RuntimeError(f"Falta {WORKFLOW}. Exporta tu workflow de ComfyUI en formato API (ver arriba).")
    try:
        requests.get(f"{COMFY}/system_stats", timeout=5).raise_for_status()
    except Exception:
        raise RuntimeError(f"No encuentro ComfyUI en {COMFY}. Ábrelo primero.")
    info = info_for(folder)
    fmt = fmt or info.get("format", "horizontal")
    (gw, gh), (fw, fh) = SIZES[fmt]
    prompt = build_prompt(info["title"], info["scene"], fmt, with_title)
    (folder / "cover_prompt_local.txt").write_text(prompt, encoding="utf-8")

    refs = sorted(p for p in (ROOT / "refs").iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})
    values = {"PROMPT": prompt, "WIDTH": gw, "HEIGHT": gh}
    for i, p in enumerate(refs, 1):
        values[f"REF{i}"] = upload(p)
    wf_tpl = json.loads(WORKFLOW.read_text(encoding="utf-8"))

    outs = []
    start = len(list(folder.glob("cover_v*.png")))
    for k in range(n):
        seed = random.randint(1, 2**31 - 1)
        wf, found = fill(wf_tpl, values, seed)
        log(f"🖼 variante {k + 1}/{n} (seed {seed}, {'con' if with_title else 'sin'} título, {fmt})…")
        imgs = run(wf)
        if not imgs:
            raise RuntimeError("ComfyUI terminó sin devolver imágenes (¿falta un nodo Save Image?)")
        from io import BytesIO
        dst = folder / f"cover_v{start + k + 1}.png"
        fit(Image.open(BytesIO(imgs[0])), fw, fh).save(dst)
        log(f"✓ {dst}")
        outs.append(dst)
    return outs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--format", choices=list(SIZES))
    ap.add_argument("--no-title", action="store_true", help="sin texto (para poner el título después)")
    a = ap.parse_args()
    try:
        generate(Path(a.folder), a.n, a.format, not a.no_title)
    except (RuntimeError, TimeoutError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
