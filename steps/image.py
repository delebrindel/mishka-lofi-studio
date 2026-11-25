"""Paso 1 — Imagen de Mishka vía API de OpenAI (endpoint /images/edits con referencias).

Uso:
    python steps/image.py --brief out/<slug>/brief.json
    python steps/image.py --title "Snowfall by the Fireplace" --description-file desc.txt

Lee:
    config/mishka_style.md    guía oficial del personaje (va completa en el prompt)
    refs/*.png|jpg|webp        imágenes de referencia de Mishka (la primera = principal)
Escribe:
    out/<slug>/cover_<formato>_raw.png        imagen tal como sale de la API
    out/<slug>/cover_<formato>_<WxH>.png      ajustada a 1080x1920 (vertical) o 1920x1080
    out/<slug>/image_prompt.txt     el prompt exacto que se envió
"""
import argparse, base64, json, os, re, sys
from pathlib import Path

from openai import OpenAI
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MODEL = os.getenv("MISHKA_IMAGE_MODEL", "gpt-image-2")
FORMATS = {  # formato: (tamaño pedido a la API, tamaño final)
    "vertical": ("1024x1536", (1080, 1920)),   # Shorts / Reels / TikTok — formato actual de Mishka
    "horizontal": ("1536x1024", (1920, 1080)), # video largo de YouTube
}
QUALITY = os.getenv("MISHKA_IMAGE_QUALITY", "high")


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


TITLE_STYLE = (
    "Title typography (match the reference covers): the title in an elegant cream-colored "
    "serif with italic swashes, on one or two lines in the upper third; below it a thin "
    "ornamental divider with a small themed icon in the middle (e.g. a paw, a tree, a raindrop); "
    "below that \"MISHKA LOFI\" in widely spaced cream uppercase sans-serif. Text must be "
    "spelled exactly and must never cover Mishka's face or eyes."
)


def build_prompt(title: str, description: str, title_in_image: bool, fmt: str) -> str:
    style = (ROOT / "config" / "mishka_style.md").read_text(encoding="utf-8")
    refs_note = (
        "Reference images: the first image is the official Mishka reference (character only). "
        "The other images are previous Mishka LoFi covers: follow their character, art style, "
        "lighting and title layout, but create a NEW scene for this title."
    )
    title_line = (
        f'Crea la imagen con el título "{title}".\n{TITLE_STYLE}'
        if title_in_image
        else f'Crea la imagen para "{title}". No incluyas ningún texto en la imagen.'
    )
    shape = "vertical 9:16 (Shorts/Reels)" if fmt == "vertical" else "horizontal 16:9 (YouTube)"
    return f"{style}\n\n---\n\n{refs_note}\n\n{title_line}\n\n{description}\n\nFormato: {shape}."


def fit(src: Path, dst: Path, w: int, h: int):
    im = Image.open(src).convert("RGB")
    target = w / h
    r = im.width / im.height
    if r > target:  # demasiado ancha → recorta lados
        nw = int(im.height * target)
        x = (im.width - nw) // 2
        im = im.crop((x, 0, x + nw, im.height))
    else:  # demasiado alta → recorta arriba/abajo (sesgo hacia arriba para no cortar el título)
        nh = int(im.width / target)
        y = int((im.height - nh) * 0.35)
        im = im.crop((0, y, im.width, y + nh))
    im.resize((w, h), Image.LANCZOS).save(dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief")
    ap.add_argument("--title")
    ap.add_argument("--description-file")
    ap.add_argument("--no-title-in-image", action="store_true")
    ap.add_argument("--format", choices=FORMATS, default="vertical")
    ap.add_argument("--n", type=int, default=1, help="variantes a generar")
    a = ap.parse_args()

    if a.brief:
        b = json.loads(Path(a.brief).read_text(encoding="utf-8"))
        title, description = b["title"], b["description"]
        if b.get("scene"):
            description += f"\n\nCover scene: {b['scene']}"
    else:
        title = a.title
        description = Path(a.description_file).read_text(encoding="utf-8")
    if not title:
        sys.exit("Falta --title o --brief")

    refs = sorted(p for p in (ROOT / "refs").iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})
    if not refs:
        sys.exit("Pon al menos una imagen de referencia de Mishka en refs/")

    out = ROOT / "out" / slugify(title)
    out.mkdir(parents=True, exist_ok=True)
    size, (w, h) = FORMATS[a.format]
    prompt = build_prompt(title, description, not a.no_title_in_image, a.format)
    (out / "image_prompt.txt").write_text(prompt, encoding="utf-8")

    client = OpenAI()  # usa OPENAI_API_KEY del entorno / .env
    files = [open(p, "rb") for p in refs[:16]]
    try:
        res = client.images.edit(model=MODEL, image=files, prompt=prompt, size=size, quality=QUALITY, n=a.n)
    finally:
        for f in files:
            f.close()

    for i, d in enumerate(res.data):
        suffix = "" if a.n == 1 else f"_{i+1}"
        raw = out / f"cover_{a.format}_raw{suffix}.png"
        raw.write_bytes(base64.b64decode(d.b64_json))
        final = out / f"cover_{a.format}_{w}x{h}{suffix}.png"
        fit(raw, final, w, h)
        print(f"✓ {final}")


if __name__ == "__main__":
    main()
