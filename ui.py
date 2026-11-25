"""Mishka LoFi Studio — interfaz local del pipeline.

    python ui.py        → abre http://localhost:7870

Pestañas:
  💡 Idea     de una idea a título, descripción y prompt musical (Claude API)
  🎵 Canción  generar tomas con ACE-Step (necesita start_api_server.bat corriendo)
  🎚️ Master   bajar batería + normalizar + evitar clips, con comparación antes / después
  📀 Playlist generar lo que falte, unir en un mix con capítulos, portada animada y video final
"""
import json, os, re, sys
from pathlib import Path

import gradio as gr

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "steps"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
import music  # noqa: E402
import brief as briefmod  # noqa: E402
import mix as mixmod  # noqa: E402
import video as videomod  # noqa: E402
import master  # noqa: E402

OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)
PLAYLISTS = ROOT / "playlists"
PLAYLISTS.mkdir(exist_ok=True)


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "sin-titulo"


def projects():
    return sorted(p.name for p in OUT.iterdir() if p.is_dir() and not p.name.startswith("_"))


def playlist_names():
    return sorted(p.stem for p in PLAYLISTS.glob("*.json"))


def takes(project=None):
    dirs = [OUT / project] if project else [p for p in OUT.iterdir() if p.is_dir()]
    found = []
    for d in dirs:
        found += [w for w in (d / "songs").glob("*.wav") if not w.stem.endswith("_master")]
    return sorted((str(w.relative_to(OUT)) for w in found if "_playlists" not in w.parts), reverse=True)


# ---------------- 💡 Idea ----------------
def brief_md(b):
    s = b["song"]
    return (f"### {b['title']}\n**{s['bpm']} BPM · {s['key']} · {s['duration']} s**\n\n"
            f"**Caption:** {s['caption']}\n\n**Estructura:** `{s['lyrics'].replace(chr(10), ' ')}`\n\n"
            f"**Portada:** {b.get('scene', '')}\n\n---\n\n{b['description'].replace(chr(10), '  ' + chr(10))}\n\n"
            f"*Tags:* {', '.join(b.get('tags', []))}")


def load_pasted(text):
    if not text.strip():
        raise gr.Error("Pega el JSON del brief")
    try:
        bs = briefmod.parse(text)
        for b in bs:
            b["title"], b["description"], b["song"]["caption"]  # valida que estén las llaves
    except Exception as e:
        raise gr.Error(f"No pude leer ese brief: {e}")
    titles = [b["title"] for b in bs]
    return bs, gr.update(choices=titles, value=titles[0], visible=True), brief_md(bs[0]), gr.update(visible=True)


def make_briefs(idea, n, progress=gr.Progress()):
    if not idea.strip():
        raise gr.Error("Escribe una idea")
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise gr.Error("No hay ANTHROPIC_API_KEY en .env. Pide los prompts en el chat de Claude y pégalos abajo.")
    progress(0, desc="Claude está escribiendo los prompts…")
    try:
        bs = briefmod.ask_claude(idea, int(n))
    except Exception as e:
        raise gr.Error(f"No se pudo generar: {e}")
    titles = [b["title"] for b in bs]
    return bs, gr.update(choices=titles, value=titles[0], visible=True), brief_md(bs[0]), gr.update(visible=True)


def show_brief(title, bs):
    b = next((b for b in bs or [] if b["title"] == title), None)
    return brief_md(b) if b else ""


def use_brief(title, bs, idea):
    b = next(b for b in bs if b["title"] == title)
    d = briefmod.save(b, idea)
    return gr.update(choices=projects(), value=d.name), gr.Tabs(selected="song")


# ---------------- 🎵 Canción ----------------
SONG_FIELDS = ["title", "caption", "lyrics", "bpm", "key", "time_signature", "duration", "batch_size", "seed"]


def load_song(project):
    if not project:
        return [gr.update()] * len(SONG_FIELDS)
    sp = OUT / project / "song.json"
    s = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {"title": project}
    return [s.get("title", ""), s.get("caption", ""), s.get("lyrics", ""), s.get("bpm", 70), s.get("key", ""),
            str(s.get("time_signature", 4)), s.get("duration", 210), s.get("batch_size", 2),
            "" if s.get("seed") is None else str(s.get("seed"))]


def gen_song(title, caption, lyrics, bpm, key, ts, duration, batch, seed, no_crackle, progress=gr.Progress()):
    if not title or not caption:
        raise gr.Error("Falta título o caption")
    song = {"title": title, "caption": caption, "lyrics": lyrics or "[Instrumental]", "bpm": int(bpm),
            "key": key, "time_signature": int(ts), "duration": int(duration), "batch_size": int(batch),
            "seed": int(seed) if str(seed).strip() else None}
    d = OUT / slugify(title)
    d.mkdir(parents=True, exist_ok=True)
    (d / "song.json").write_text(json.dumps(song, indent=2, ensure_ascii=False), encoding="utf-8")
    if no_crackle:
        song["caption"] = music.strip_crackle(song["caption"])
    lines = []
    progress(0, desc="Generando con ACE-Step…")
    try:
        files = music.generate(song, d, song["seed"], log=lines.append)
    except Exception as e:
        raise gr.Error(str(e))
    audios = [gr.update(value=str(f), label=f.name, visible=True) for f in files[:4]]
    audios += [gr.update(value=None, visible=False)] * (4 - len(audios))
    return ["\n".join(lines)] + audios + [gr.update(choices=projects(), value=d.name)]


# ---------------- 🎚️ Master ----------------
def do_master(take, drums_db, bass_db, lufs, ceiling, progress=gr.Progress()):
    if not take:
        raise gr.Error("Elige una toma")
    wav = OUT / take
    lines = []
    progress(0, desc="Separando batería (solo la primera vez) y masterizando…")
    try:
        rep = master.master(wav, drums_db, lufs, ceiling, log=lines.append, bass_db=bass_db)
    except Exception as e:
        raise gr.Error(str(e))
    b, a = rep["before"], rep["after"]
    md = (f"| | LUFS | True peak | Muestras clipeadas |\n|---|---|---|---|\n"
          f"| Antes | {b['lufs']} | {b['true_peak_dbtp']} dBTP | {b['clipped_samples']} |\n"
          f"| **Después** | **{a['lufs']}** | **{a['true_peak_dbtp']} dBTP** | **{a['clipped_samples']}** |\n\n"
          f"{'✅ Listo, sin clips' if rep['ok'] else '⚠️ Revisa: el pico quedó sobre el techo'} · "
          f"batería {drums_db:+.0f} dB · bajo {bass_db:+.0f} dB · ganancia {rep['gain_db']:+.1f} dB")
    return str(wav), rep["files"][0], md, rep["files"]


# ---------------- 📀 Playlist ----------------
def pl_load(name):
    return json.loads((PLAYLISTS / f"{name}.json").read_text(encoding="utf-8"))


def pl_dir(name):
    d = OUT / "_playlists" / pl_load(name)["slug"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def pl_status(name):
    if not name:
        return "", None, None, None
    pl = pl_load(name)
    rows = ["| # | Canción | Estado | Toma elegida |", "|---|---|---|---|"]
    for i, st in enumerate(mixmod.status(pl), 1):
        if st["chosen"]:
            state, take = "✅ masterizada", st["chosen"].name.replace("_master.wav", "")
        elif st["takes"]:
            state, take = f"🎵 {len(st['takes'])} tomas, sin master", "se masteriza la primera"
        else:
            state, take = "⬜ sin generar", ""
        rows.append(f"| {i} | {st['title']} | {state} | {take} |")
    d = pl_dir(name)
    cover = next((str(d / n) for n in ("cover.png", "cover.jpg") if (d / n).exists()), None)
    loop = str(d / "cover_loop.mp4") if (d / "cover_loop.mp4").exists() else None
    vid = str(d / "video.mp4") if (d / "video.mp4").exists() else None
    return "\n".join(rows), cover, loop, vid


def pl_generate(name, progress=gr.Progress()):
    pl = pl_load(name)
    todo = [st for st in mixmod.status(pl) if not st["takes"]]
    if not todo:
        yield "Todas las canciones ya tienen tomas."
        return
    log = []
    for i, st in enumerate(todo, 1):
        progress((i - 1) / len(todo), desc=f"{st['title']} ({i}/{len(todo)})")
        sp = OUT / st["slug"] / "song.json"
        if not sp.exists():
            log.append(f"⚠ {st['title']}: falta song.json, la salto")
            yield "\n".join(log)
            continue
        song = json.loads(sp.read_text(encoding="utf-8"))
        try:
            music.generate(song, sp.parent, song.get("seed"), log=log.append)
        except Exception as e:
            log.append(f"⚠ {st['title']}: {e}")
        yield "\n".join(log)
    log.append("Listo. Escucha las tomas en 🎚️ Master y masteriza la que más te guste de cada canción.")
    yield "\n".join(log)


def pl_mix(name, progress=gr.Progress()):
    progress(0, desc="Masterizando lo que falte y uniendo el mix…")
    lines = []
    try:
        info = mixmod.build(PLAYLISTS / f"{name}.json", log=lines.append)
    except Exception as e:
        raise gr.Error(str(e))
    d = Path(info["dir"])
    chapters = (d / "chapters.txt").read_text(encoding="utf-8")
    return str(d / "mix.mp3"), chapters, f"**{info['duration']}** · {info['after']['lufs']} LUFS · pico {info['after']['true_peak_dbtp']} dBTP"


def pl_cover(name, img):
    if not img:
        raise gr.Error("Sube la portada")
    from PIL import Image
    pl = pl_load(name)
    w, h = (1920, 1080) if pl.get("format", "horizontal") == "horizontal" else (1080, 1920)
    im = Image.open(img).convert("RGB")
    r, tr = im.width / im.height, w / h
    if r > tr:
        nw = int(im.height * tr); x = (im.width - nw) // 2; im = im.crop((x, 0, x + nw, im.height))
    else:
        nh = int(im.width / tr); y = int((im.height - nh) * 0.35); im = im.crop((0, y, im.width, y + nh))
    dst = pl_dir(name) / "cover.png"
    im.resize((w, h), Image.LANCZOS).save(dst)
    return str(dst)


def pl_anim(name, spec_text, progress=gr.Progress()):
    import animate
    d = pl_dir(name)
    img = d / "cover.png"
    if not img.exists():
        raise gr.Error("Primero sube la portada")
    sp = d / "anim_spec.json"
    if spec_text.strip():
        try:
            spec = json.loads(re.search(r"\{.*\}", spec_text, re.S).group(0))
        except Exception as e:
            raise gr.Error(f"No pude leer el plan de animación: {e}")
        sp.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    if not sp.exists():
        raise gr.Error("Pega el plan de animación (pídeselo a Claude con tu portada)")
    spec = json.loads(sp.read_text(encoding="utf-8"))
    progress(0.1, desc="Revisando zonas…")
    animate.check(img, spec)
    progress(0.3, desc="Renderizando loop de 10 s…")
    animate.render(img, spec)
    return str(d / "anim_check.png"), str(d / "cover_loop.mp4")


def pl_video(name, progress=gr.Progress()):
    progress(0, desc="Armando el video (unos minutos)…")
    lines = []
    try:
        out = videomod.render(pl_dir(name), log=lines.append)
    except Exception as e:
        raise gr.Error(str(e))
    return str(out), "\n".join(lines)


THEME = gr.themes.Soft(primary_hue="amber", neutral_hue="stone")

with gr.Blocks(title="Mishka LoFi Studio") as app:
    gr.Markdown("# 🐾 Mishka LoFi Studio")
    with gr.Tabs() as tabs:
        with gr.Tab("💡 Idea", id="idea"):
            idea = gr.Textbox(label="Idea", lines=2,
                              placeholder="ej. Mishka dormida junto a la ventana, primera nevada, domingo en la tarde")
            n_var = gr.Slider(1, 4, value=3, step=1, label="Propuestas")
            go_idea = gr.Button("Escribir prompts (API)", variant="primary")
            with gr.Accordion("Sin API key: pega aquí el brief que te dio Claude en el chat", open=True):
                pasted = gr.Textbox(label="Brief (JSON)", lines=6)
                load_btn = gr.Button("Cargar propuestas")
            briefs = gr.State([])
            pick = gr.Radio(label="Elige una", visible=False)
            preview = gr.Markdown()
            use = gr.Button("Usar esta → 🎵 Canción", visible=False)

        with gr.Tab("🎵 Canción", id="song"):
            with gr.Row():
                proj = gr.Dropdown(projects(), label="Proyecto", allow_custom_value=False, scale=3)
                reload_p = gr.Button("↻", scale=0)
            title = gr.Textbox(label="Título")
            caption = gr.Textbox(label="Caption (prompt de estilo)", lines=4)
            lyrics = gr.Textbox(label="Lyrics / estructura", lines=4,
                                value="[Intro - piano]\n[Main Theme - piano and Rhodes]\n[Main Theme - piano]\n[Outro - fade out]")
            with gr.Row():
                bpm = gr.Number(label="BPM", value=70, precision=0)
                key = gr.Textbox(label="Tonalidad", value="F", placeholder="F, Am, C# minor…")
                ts = gr.Dropdown(["2", "3", "4", "6"], value="4", label="Compás")
                duration = gr.Slider(30, 600, value=210, step=5, label="Duración (s)")
                batch = gr.Slider(1, 4, value=2, step=1, label="Tomas")
                seed = gr.Textbox(label="Seed (vacío = aleatorio)")
            no_crackle = gr.Checkbox(label="Quitar crepitar / fuego del caption")
            go = gr.Button("Generar", variant="primary")
            log = gr.Textbox(label="Registro", lines=3)
            outs = [gr.Audio(visible=False, type="filepath") for _ in range(4)]

        with gr.Tab("🎚️ Master", id="master"):
            with gr.Row():
                take = gr.Dropdown(takes(), label="Toma", scale=3)
                reload_t = gr.Button("↻", scale=0)
            with gr.Row():
                drums = gr.Slider(-24, 0, value=master.DEFAULTS["drums_db"], step=1, label="Batería (dB)")
                bass = gr.Slider(-24, 0, value=master.DEFAULTS["bass_db"], step=1, label="Bajo (dB)")
                lufs = gr.Slider(-20, -9, value=master.DEFAULTS["lufs"], step=0.5, label="Loudness objetivo (LUFS)")
                ceil = gr.Slider(-3, -0.1, value=master.DEFAULTS["ceiling"], step=0.1, label="Techo de pico (dBTP)")
            run = gr.Button("Masterizar", variant="primary")
            with gr.Row():
                a_before = gr.Audio(label="Antes", type="filepath")
                a_after = gr.Audio(label="Después", type="filepath")
            report = gr.Markdown()
            files = gr.Files(label="Archivos")

        with gr.Tab("📀 Playlist", id="playlist"):
            with gr.Row():
                pl_name = gr.Dropdown(playlist_names(), label="Playlist", scale=3)
                pl_reload = gr.Button("↻", scale=0)
            pl_table = gr.Markdown()
            gr.Markdown("**1 · Canciones** — genera las que falten. Luego, en 🎚️ Master, masteriza la toma que más te guste de cada una (si no, se usa la primera).")
            pl_gen = gr.Button("Generar canciones faltantes")
            pl_log = gr.Textbox(label="Registro", lines=4)
            gr.Markdown("**2 · Mix** — une los masters con crossfade, normaliza todo y escribe los capítulos.")
            pl_mix_btn = gr.Button("Armar mix", variant="primary")
            pl_info = gr.Markdown()
            with gr.Row():
                pl_audio = gr.Audio(label="Mix", type="filepath")
                pl_chapters = gr.Textbox(label="Capítulos para la descripción de YouTube", lines=8, buttons=["copy"])
            gr.Markdown("**3 · Portada** — sube la imagen de ChatGPT; se ajusta sola al formato de la playlist.")
            with gr.Row():
                pl_cover_in = gr.Image(label="Portada", type="filepath", sources=["upload", "clipboard"])
                pl_cover_save = gr.Button("Guardar portada", scale=0)
            gr.Markdown("**4 · Animación** — pega el plan de animación que te da Claude (skill mishka-lofi-release) con tu portada.")
            pl_spec = gr.Textbox(label="anim_spec.json", lines=5)
            pl_anim_btn = gr.Button("Animar portada")
            with gr.Row():
                pl_check = gr.Image(label="Zonas animadas", type="filepath")
                pl_loop = gr.Video(label="Loop de 10 s")
            gr.Markdown("**5 · Video final** — repite el loop durante todo el mix.")
            pl_vid_btn = gr.Button("Renderizar video", variant="primary")
            pl_vid_log = gr.Textbox(label="Resultado", lines=2)
            pl_video_out = gr.Video(label="Video")

    # eventos
    go_idea.click(make_briefs, [idea, n_var], [briefs, pick, preview, use])
    load_btn.click(load_pasted, pasted, [briefs, pick, preview, use])
    pick.change(show_brief, [pick, briefs], preview)
    use.click(use_brief, [pick, briefs, idea], [proj, tabs])
    tabs.select(lambda: (gr.update(choices=projects()), gr.update(choices=takes())), None, [proj, take])
    fields = [title, caption, lyrics, bpm, key, ts, duration, batch, seed]
    proj.change(load_song, proj, fields)
    reload_p.click(lambda: gr.update(choices=projects()), None, proj)
    go.click(gen_song, fields + [no_crackle], [log] + outs + [proj])
    reload_t.click(lambda: gr.update(choices=takes()), None, take)
    pl_name.change(pl_status, pl_name, [pl_table, pl_cover_in, pl_loop, pl_video_out])
    pl_reload.click(lambda: gr.update(choices=playlist_names()), None, pl_name)
    pl_gen.click(pl_generate, pl_name, pl_log).then(pl_status, pl_name, [pl_table, pl_cover_in, pl_loop, pl_video_out])
    pl_mix_btn.click(pl_mix, pl_name, [pl_audio, pl_chapters, pl_info]).then(
        pl_status, pl_name, [pl_table, pl_cover_in, pl_loop, pl_video_out])
    pl_cover_save.click(pl_cover, [pl_name, pl_cover_in], pl_cover_in)
    pl_anim_btn.click(pl_anim, [pl_name, pl_spec], [pl_check, pl_loop])
    pl_vid_btn.click(pl_video, pl_name, [pl_video_out, pl_vid_log])
    run.click(do_master, [take, drums, bass, lufs, ceil], [a_before, a_after, report, files])

if __name__ == "__main__":
    app.launch(server_port=7870, inbrowser=True, allowed_paths=[str(OUT)], theme=THEME)
