"""Paso 5 — Portada animada en loop (10 s, 30 fps, primer frame == último) a partir de la imagen.

Automatiza lo que hoy se hace a mano en el chat con Claude:
  1. `plan`   Claude (API, con visión) mira la portada y escribe anim_spec.json: qué animar y dónde
              (nieve/lluvia en la ventana, fuego, lámparas, vapor, parpadeo, luces que titilan).
  2. `check`  Dibuja las zonas sobre la imagen (anim_check.png) y saca 4 frames de muestra para revisar.
  3. `render` Renderiza el MP4 con tools/mishka_loop_lib.py y verifica que el loop cierre perfecto.

Uso:
    python steps/animate.py all    out/<slug>/cover_vertical_1080x1920.png
    python steps/animate.py plan   <imagen>      # solo genera/actualiza el spec
    python steps/animate.py check  <imagen>      # tras editar anim_spec.json a mano
    python steps/animate.py render <imagen>

El spec usa coordenadas normalizadas 0–1000 (x de izquierda a derecha, y de arriba abajo),
así no depende de la resolución. Se puede corregir a mano y volver a correr `check`/`render`.
"""
import base64, io, json, os, re, sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as nd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import mishka_loop_lib as L  # noqa: E402
from mishka_loop_ext import Snow, EyeBlink  # noqa: E402

CLAUDE_MODEL = os.getenv("MISHKA_CLAUDE_MODEL", "claude-opus-5-5")

SPEC_PROMPT = """You are planning a subtle, seamless 10-second loop animation of this Mishka LoFi cover
(a cozy lo-fi illustration of Mishka, a spitz dog). Only small, calm motions — it must still look like the
same painting. Return ONLY a JSON object, no prose. All coordinates are normalized 0-1000 on this image
(x from left, y from top). Boxes are [x0, y0, x1, y1]. Be precise: boxes should hug the element tightly.

{
  "title_boxes": [[...]],          // every block of title / "MISHKA LOFI" text (never animate over these)
  "effects": [
    {"type": "snow",    "window": [...]},          // glass area of a window where snow falls outside
    {"type": "rain",    "window": [...]},          // glass area where rain falls outside
    {"type": "fire",    "box": [...]},             // fireplace flames only (not the bricks)
    {"type": "glow",    "box": [...], "center": [x, y]},  // a lamp shade, lantern or candle that should flicker softly
    {"type": "steam",   "box": [...], "source_y": y},     // painted steam above a mug/teapot; source_y = cup rim
    {"type": "twinkle", "box": [...]},             // area with small lights: stars, distant city windows, string lights
    {"type": "blink",   "eye_box": [...]}          // tight box around Mishka's clearly visible eye (iris + lids)
  ]
}
Include only effects that really exist in the image. One "blink" per visible eye (usually one in profile).
Use "snow" or "rain" according to what is painted outside the window, never both for the same window."""


# ---------- helpers ----------
def spec_path(img: Path) -> Path:
    return img.with_name("anim_spec.json")


def px(box, W, H):
    x0, y0, x1, y1 = box
    return (int(x0 * W / 1000), int(y0 * H / 1000), int(x1 * W / 1000), int(y1 * H / 1000))


def pt(p, W, H):
    return (int(p[0] * W / 1000), int(p[1] * H / 1000))


def box_mask(box, H, W):
    m = np.zeros((H, W), bool)
    x0, y0, x1, y1 = box
    m[y0:y1, x0:x1] = True
    return m


def text_mask(title_boxes, H, W):
    """Píxeles del título (crema/brillantes) dentro de sus cajas, dilatados — como en mishka_loop_ext."""
    im = L.G["im"]
    m = np.zeros((H, W), bool)
    for b in title_boxes:
        x0, y0, x1, y1 = b
        reg = im[y0:y1, x0:x1]
        t = (reg.mean(-1) > 0.62) & ((reg[..., 0] - reg[..., 2]) > -0.02)
        m[y0:y1, x0:x1] |= t
    return nd.binary_dilation(m, iterations=3)


def glass_mask(window, title_px, H, W):
    """Ventana menos madera del marco y menos el texto del título."""
    h, s, v = L.hsvsplit()
    wood = (h > 5) & (h < 50) & (s > 0.35) & (v < 0.62)
    g = box_mask(window, H, W) & ~wood & ~text_mask(title_px, H, W)
    return nd.binary_opening(g, iterations=1)


# ---------- plan (Claude API) ----------
def plan(img: Path):
    import anthropic

    im = Image.open(img).convert("RGB")
    im.thumbnail((1092, 1092 * 2))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.b64encode(buf.getvalue()).decode()}},
                {"type": "text", "text": SPEC_PROMPT},
            ],
        }],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    spec = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    spec_path(img).write_text(json.dumps(spec, indent=2), encoding="utf-8")
    print(f"✓ spec → {spec_path(img)}")
    return spec


# ---------- build effect objects ----------
def build(img: Path, spec: dict):
    L.init(str(img))
    H, W = L.G["H"], L.G["W"]
    titles = [px(b, W, H) for b in spec.get("title_boxes", [])]
    fx, seed = [], 1
    # orden de pintado: fondo → luces → primer plano → ojos
    order = {"twinkle": 0, "snow": 1, "rain": 1, "fire": 2, "glow": 3, "steam": 4, "blink": 9}
    blink_times = [[2.2, 7.1], [4.6], [8.3]]
    nblink = 0
    for e in sorted(spec.get("effects", []), key=lambda e: order.get(e["type"], 5)):
        seed += 1
        t = e["type"]
        try:
            if t == "snow":
                g = glass_mask(px(e["window"], W, H), titles, H, W)
                area = g.sum() / (W * H)
                fx.append(Snow(g, n=int(400 * area / 0.15) + 60, seed=seed))
            elif t == "rain":
                b = px(e["window"], W, H)
                g = glass_mask(b, titles, H, W).astype(np.float32)
                fx.append(L.Rain(g, b, n=int((b[2] - b[0]) * 0.35), seed=seed))
            elif t == "fire":
                b = px(e["box"], W, H)
                h, s, v = L.hsvsplit()
                m = box_mask(b, H, W) & (v > 0.55) & (s > 0.35) & ((h < 60) | (h > 340))
                m = L.clean(m, minsz=60, dil=3)
                fx.append(L.Fire(m, b, seed=seed))
            elif t == "glow":
                b = px(e["box"], W, H)
                c = pt(e["center"], W, H)
                r = max(b[2] - b[0], b[3] - b[1])
                pad = int(r * 0.8)
                big = (max(b[0] - pad, 0), max(b[1] - pad, 0), min(b[2] + pad, W), min(b[3] + pad, H))
                fx.append(L.Glow(big, b, c, r, seed=seed))
            elif t == "steam":
                b = px(e["box"], W, H)
                fx.append(L.SteamWarp(b, int(e["source_y"] * H / 1000)))
            elif t == "twinkle":
                reg = box_mask(px(e["box"], W, H), H, W) & ~text_mask(titles, H, W)
                fx.append(L.Twinkle(reg, thr=0.08, size=7, seed=seed))
            elif t == "blink":
                fx.append(EyeBlink(px(e["eye_box"], W, H), blink_times[nblink % 3]))
                nblink += 1
        except Exception as ex:  # un efecto mal ubicado no debe tumbar todo el render
            print(f"⚠ efecto {t} omitido: {ex}")
    return fx


def frame_fn(fx):
    base = L.G["im"]

    def frame(i):
        t = i * L.T / L.N
        out = base.copy()
        for f in fx:
            f.apply(out, t)
        return L.finish(out)

    return frame


# ---------- check / render ----------
def check(img: Path, spec: dict):
    fx = build(img, spec)
    H, W = L.G["H"], L.G["W"]
    vis = Image.open(img).convert("RGB")
    d = ImageDraw.Draw(vis)
    col = {"snow": "cyan", "rain": "deepskyblue", "fire": "orange", "glow": "yellow",
           "steam": "white", "twinkle": "violet", "blink": "lime"}
    for b in spec.get("title_boxes", []):
        d.rectangle(px(b, W, H), outline="red", width=4)
    for e in spec.get("effects", []):
        b = e.get("box") or e.get("window") or e.get("eye_box")
        d.rectangle(px(b, W, H), outline=col.get(e["type"], "white"), width=4)
        d.text((px(b, W, H)[0] + 6, px(b, W, H)[1] + 4), e["type"], fill=col.get(e["type"], "white"))
    frame = frame_fn(fx)
    frames = [Image.fromarray(frame(i)) for i in (0, 66, 140, 220)]  # 66 ≈ 2.2 s → parpadeo
    sheet = Image.new("RGB", (W * 5, H))
    for k, f in enumerate([vis] + frames):
        sheet.paste(f, (k * W, 0))
    sheet.thumbnail((W * 5 // 2, H // 2))
    outp = img.with_name("anim_check.png")
    sheet.save(outp)
    print(f"✓ revisión → {outp}  ({len(fx)} efectos)")


def render(img: Path, spec: dict):
    fx = build(img, spec)
    out = img.with_name("cover_loop.mp4")
    L.run(frame_fn(fx), ["", "render", str(out)], L.G["W"], L.G["H"])
    L.verify(str(out))
    print(f"✓ loop → {out}")


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, img = sys.argv[1], Path(sys.argv[2])
    sp = spec_path(img)
    if cmd in ("plan", "all") or not sp.exists():
        spec = plan(img)
    else:
        spec = json.loads(sp.read_text(encoding="utf-8"))
    if cmd in ("check", "all"):
        check(img, spec)
    if cmd in ("render", "all"):
        render(img, spec)


if __name__ == "__main__":
    main()
