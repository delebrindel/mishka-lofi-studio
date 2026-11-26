"""Mishka LoFi Studio — interfaz local del pipeline.

    python ui.py        → abre http://localhost:7870

Pestañas:
  💡 Idea     de una idea a título, descripción y prompt musical (Claude API)
  🎵 Canción  generar tomas con ACE-Step (necesita start_api_server.bat corriendo)
  🎚️ Master   bajar batería + normalizar + evitar clips, con comparación antes / después
  📀 Playlist generar lo que falte, unir en un mix con capítulos, portada animada y video final
"""
import json, os, re, sys, time
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
import cover_local  # noqa: E402
import chatgpt_prompt  # noqa: E402
import seo  # noqa: E402
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
    import claude_local
    if not claude_local.available() and not os.getenv("ANTHROPIC_API_KEY"):
        raise gr.Error("No encuentro Claude Code en esta compu. Instálalo (https://code.claude.com), corre `claude` "
                       "una vez para entrar con tu cuenta, y reinicia Studio. O pega abajo un brief del chat.")
    via = "Claude Code (tu suscripción)" if briefmod.backend() == "claude-code" else "la API de Anthropic"
    progress(0, desc=f"Claude está escribiendo los prompts vía {via}… (1–2 min)")
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
ALL_SONGS = "Todas las canciones"


def m_song_list(pl):
    """[(etiqueta, slug)] de las canciones de una playlist (o de todas), con su estado."""
    if pl and pl != ALL_SONGS and (PLAYLISTS / f"{pl}.json").exists():
        slugs = pl_load(pl)["tracks"]
    else:
        slugs = projects()
    out = []
    for i, slug in enumerate(slugs, 1):
        st = mixmod.track_status(slug)
        mark = "✅" if st["chosen"] else ("🎵" if st["takes"] else "⬜")
        out.append((f"{i}. {mark} {st['title']}", slug))
    return out


def m_pick_playlist(pl):
    songs = m_song_list(pl)
    first = next((v for _, v in songs if mixmod.track_status(v)["takes"]), songs[0][1] if songs else None)
    return gr.update(choices=songs, value=first)


def m_pick_song(slug):
    """Tomas de la canción; la que está en uso marcada y preseleccionada."""
    if not slug:
        return gr.update(choices=[], value=None), "", None
    st = mixmod.track_status(slug)
    used = st["chosen"].name.replace("_master.wav", ".wav") if st["chosen"] else None
    tj = OUT / slug / "songs" / "takes.json"
    hist = {h["file"]: h for h in json.loads(tj.read_text(encoding="utf-8"))} if tj.exists() else {}

    def label(t):
        l = t[:-4]
        h = hist.get(t, {})
        if h.get("repaint_of"):
            a, b = h.get("repaint_range", ["?", "?"])
            l += f"  🎨 repaint {a:.0f}–{b:.0f}s de {h['repaint_of'][:-4]}"
        return l + ("  ✅ en uso" if t == used else "")
    choices = [(label(t), f"{slug}/songs/{t}") for t in st["takes"]]
    value = f"{slug}/songs/{used}" if used else (choices[0][1] if choices else None)
    sj = OUT / slug / "song.json"
    song = json.loads(sj.read_text(encoding="utf-8")) if sj.exists() else {}
    info = (f"**{st['title']}** · {song.get('bpm', '?')} BPM · {song.get('key', '?')} · "
            f"{len(st['takes'])} tomas · en uso: **{used[:-4] if used else 'ninguna'}**")
    if not st["takes"]:
        info += " · ⬜ sin generar (genérala en 📀 Playlist o 🎵 Canción)"
    return gr.update(choices=choices, value=value), info, (str(OUT / value) if value else None)


def m_step(pl, slug, delta):
    songs = [v for _, v in m_song_list(pl)]
    if not songs:
        return gr.update()
    i = songs.index(slug) if slug in songs else -1
    return gr.update(value=songs[(i + delta) % len(songs)])


def m_delete_ask(take):
    if not take:
        raise gr.Error("Elige la toma a eliminar")
    name = Path(take).stem
    slug = Path(take).parts[0]
    st = mixmod.track_status(slug)
    in_use = st["chosen"] is not None and st["chosen"].name == f"{name}_master.wav"
    warn = " **Es la toma en uso de la playlist**; se usará otra masterizada (o tendrás que masterizar otra)." if in_use else ""
    return (gr.update(value=f"¿Eliminar **{name}**?{warn} Se mueve a la papelera de la canción (`songs/_papelera`), "
                            "junto con su master y sus stems.", visible=True),
            gr.update(value=f"Sí, eliminar {name}", visible=True), gr.update(visible=True))


def m_delete(take):
    """Mueve la toma, su master y sus stems a songs/_papelera (recuperable)."""
    import shutil
    src = OUT / take
    songs = src.parent
    trash = songs / "_papelera"
    trash.mkdir(exist_ok=True)
    name = src.stem
    moved = []
    for f in [src, *songs.glob(f"{name}_master.*")]:
        if f.exists():
            dst = trash / f.name
            if dst.exists():
                dst.unlink()
            shutil.move(str(f), str(dst))
            moved.append(f.name)
    stems = songs / "stems" / name
    if stems.exists():
        dst = trash / f"stems_{name}"
        shutil.rmtree(dst, ignore_errors=True)
        shutil.move(str(stems), str(dst))
    tj = songs / "takes.json"
    if tj.exists():
        hist = json.loads(tj.read_text(encoding="utf-8"))
        for h in hist:
            if h.get("file") == src.name:
                h["deleted"] = time.strftime("%Y-%m-%d %H:%M")
        tj.write_text(json.dumps(hist, indent=2, ensure_ascii=False), encoding="utf-8")
    return f"🗑 {name} movida a la papelera ({len(moved)} archivos + stems). Para recuperarla, sácala de `{trash}`."


def m_preview(take):
    if not take:
        return None, gr.update(), gr.update(), ""
    import soundfile as sf
    dur = sf.info(str(OUT / take)).duration
    return (str(OUT / take), gr.update(maximum=round(dur), value=min(60, round(dur) - 30)),
            gr.update(maximum=round(dur), value=min(90, round(dur))), f"Duración de la toma: **{int(dur // 60)}:{int(dur % 60):02d}**")


def rp_caption(slug):
    sj = OUT / slug / "song.json" if slug else None
    return json.loads(sj.read_text(encoding="utf-8")).get("caption", "") if sj and sj.exists() else ""


def rp_run(slug, take, start, end, caption, progress=gr.Progress()):
    if not slug or not take:
        raise gr.Error("Elige canción y toma")
    if end - start < 5:
        raise gr.Error("El tramo debe durar al menos 5 segundos")
    song = json.loads((OUT / slug / "song.json").read_text(encoding="utf-8"))
    if caption.strip():
        song["caption"] = caption.strip()   # solo para este repaint; no cambia song.json
    lines = []
    progress(0, desc="Repintando el tramo con ACE-Step…")
    try:
        files = music.repaint(song, OUT / take, float(start), float(end), log=lines.append)
    except Exception as e:
        raise gr.Error(str(e))
    ups = [gr.update(value=str(f), label=f"{f.stem} (repaint {start:.0f}–{end:.0f}s)", visible=True) for f in files[:2]]
    ups += [gr.update(value=None, visible=False)] * (2 - len(ups))
    return ["\n".join(lines) + "\nLas variantes ya aparecen en «Toma»: elige la que te guste y masterízala."] + ups


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


def publish_texts(target):
    try:
        d = seo.build(target)
    except Exception as e:
        raise gr.Error(f"No pude armar los textos: {e}")
    return d["yt_title"], d["yt_description"], d["yt_tags"], d["ig_caption"]


def pl_publish(name):
    if not name:
        raise gr.Error("Elige la playlist")
    return publish_texts(PLAYLISTS / f"{name}.json")


def song_publish(project):
    if not project:
        raise gr.Error("Elige un proyecto")
    return publish_texts(OUT / project)


def publish_box():
    """Cuatro cajas con botón de copiar: título, descripción y tags de YouTube, caption de Instagram."""
    yt_t = gr.Textbox(label="YouTube · Título", buttons=["copy"])
    yt_d = gr.Textbox(label="YouTube · Descripción", lines=12, buttons=["copy"])
    yt_g = gr.Textbox(label="YouTube · Tags", lines=2, buttons=["copy"])
    ig = gr.Textbox(label="Instagram · Caption", lines=10, buttons=["copy"])
    return [yt_t, yt_d, yt_g, ig]


MAX_TRACKS = 24


def pl_players(name):
    """Un reproductor por canción con la toma que usa la playlist (mp3 del master si existe)."""
    ups = []
    sts = mixmod.status(pl_load(name)) if name else []
    for i, st in enumerate(sts[:MAX_TRACKS], 1):
        songs = OUT / st["slug"] / "songs"
        if st["chosen"]:
            mp3 = st["chosen"].with_suffix(".mp3")
            path = mp3 if mp3.exists() else st["chosen"]
            label = f"{i}. {st['title']}  ·  {st['chosen'].stem.replace('_master', '')}"
        elif st["takes"]:
            path, label = songs / st["takes"][0], f"{i}. {st['title']}  ·  {st['takes'][0][:-4]} (sin master)"
        else:
            ups.append(gr.update(value=None, label=f"{i}. {st['title']}  ·  sin generar", visible=True))
            continue
        ups.append(gr.update(value=str(path), label=label, visible=True))
    ups += [gr.update(value=None, visible=False)] * (MAX_TRACKS - len(ups))
    return ups


def rg_preview(slug, take):
    if not slug or not take:
        return gr.update(value=None, visible=False)
    return gr.update(value=str(OUT / slug / "songs" / take), label=f"Escuchar {take}", visible=True)


def pl_track_choices(name):
    if not name:
        return gr.update(choices=[])
    return gr.update(choices=[(f"{i}. {st['title']}", st["slug"]) for i, st in enumerate(mixmod.status(pl_load(name)), 1)],
                     value=None)


def rg_load(slug):
    if not slug:
        return "", "", gr.update(choices=[], value=None)
    sp = OUT / slug / "song.json"
    song = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    st = mixmod.track_status(slug)
    current = st["chosen"].name.replace("_master.wav", "") if st["chosen"] else "ninguna"
    info = f"Toma en uso: **{current}** · {song.get('bpm', '?')} BPM · {song.get('key', '?')} · {len(st['takes'])} tomas generadas"
    return song.get("caption", ""), info, gr.update(choices=[t for t in st["takes"]], value=None)


def rg_generate(slug, caption, seed, no_crackle, progress=gr.Progress()):
    if not slug:
        raise gr.Error("Elige la pista")
    sp = OUT / slug / "song.json"
    if not sp.exists():
        raise gr.Error("Esa pista no tiene song.json")
    song = json.loads(sp.read_text(encoding="utf-8"))
    if caption.strip() and caption.strip() != song.get("caption"):
        song["caption"] = caption.strip()
        sp.write_text(json.dumps(song, indent=2, ensure_ascii=False), encoding="utf-8")  # se guarda el cambio
    if no_crackle:
        song["caption"] = music.strip_crackle(song["caption"])
    lines = []
    progress(0, desc=f"Regenerando {song.get('title', slug)}…")
    try:
        files = music.generate(song, sp.parent, int(seed) if str(seed).strip() else None, log=lines.append)
    except Exception as e:
        raise gr.Error(str(e))
    names = [f.name for f in files]
    audios = [gr.update(value=str(f), label=f.name, visible=True) for f in files[:2]]
    audios += [gr.update(value=None, visible=False)] * (2 - len(audios))
    all_takes = mixmod.track_status(slug)["takes"]
    return ["\n".join(lines), gr.update(choices=all_takes, value=names[0] if names else None)] + audios


def rg_master(slug, take, progress=gr.Progress()):
    if not slug or not take:
        raise gr.Error("Elige la toma a usar")
    progress(0, desc="Masterizando (batería y bajo con los valores por defecto)…")
    try:
        rep = master.master(OUT / slug / "songs" / take, log=lambda *_: None)
    except Exception as e:
        raise gr.Error(str(e))
    a = rep["after"]
    return (f"✅ **{take}** es ahora la toma de esta pista ({a['lufs']} LUFS, pico {a['true_peak_dbtp']} dBTP). "
            "Vuelve a **Armar mix** para que entre en la playlist.")


def pl_transition(name):
    if not name:
        return gr.update(), gr.update()
    pl = pl_load(name)
    gap = float(pl.get("gap", 1))
    if gap > 0:
        return "Pausa de silencio", gap
    return "Crossfade", float(pl.get("crossfade", 4))


def pl_regen_all(name, auto_master, progress=gr.Progress()):
    """Genera tomas NUEVAS para todas las canciones (las anteriores se conservan)."""
    if not name:
        raise gr.Error("Elige la playlist")
    sts = mixmod.status(pl_load(name))
    log = [f"🔁 Regenerando {len(sts)} canciones (las tomas anteriores se conservan)…"]
    yield "\n".join(log)
    for i, st in enumerate(sts, 1):
        progress((i - 1) / len(sts), desc=f"{i}/{len(sts)} · {st['title']}")
        sp = OUT / st["slug"] / "song.json"
        if not sp.exists():
            log.append(f"⚠ {st['title']}: falta song.json, la salto")
            yield "\n".join(log)
            continue
        song = json.loads(sp.read_text(encoding="utf-8"))
        log.append(f"\n{i}/{len(sts)} · {st['title']}")
        yield "\n".join(log)
        try:
            files = music.generate(song, sp.parent, None, log=log.append)
            if auto_master and files:
                log.append(f"  🎚 masterizando {files[0].name}…")
                yield "\n".join(log)
                master.master(files[0], log=lambda *_: None)
                log.append("  ✅ queda como toma en uso")
        except Exception as e:
            log.append(f"  ⚠ {e}")
        yield "\n".join(log)
    log.append("\nListo. Revisa en 🎚️ Master (◀ ▶) y masteriza la toma que más te guste de cada una; "
               "luego Armar mix.")
    yield "\n".join(log)


def pl_mix(name, mode, secs, progress=gr.Progress()):
    pf = PLAYLISTS / f"{name}.json"
    pl = pl_load(name)
    if mode == "Pausa de silencio":
        pl["gap"] = float(secs)
    else:
        pl["gap"] = 0
        pl["crossfade"] = float(secs)
    pf.write_text(json.dumps(pl, indent=2, ensure_ascii=False), encoding="utf-8")  # se recuerda para la próxima
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


def pl_gpt_prompt(name):
    try:
        text, _ = chatgpt_prompt.for_target(PLAYLISTS / f"{name}.json")
    except Exception as e:
        raise gr.Error(str(e))
    return text


def song_gpt_prompt(project):
    if not project:
        raise gr.Error("Elige un proyecto")
    try:
        text, _ = chatgpt_prompt.for_target(OUT / project)
    except Exception as e:
        raise gr.Error(str(e))
    return gr.update(value=text, visible=True)


def pl_comfy(name, n, with_title, progress=gr.Progress()):
    d = pl_dir(name)
    pl = pl_load(name)
    cj = d / "cover.json"
    if not cj.exists():
        cj.write_text(json.dumps({"title": pl.get("cover_title", pl["title"]), "scene": pl.get("scene", ""),
                                  "format": pl.get("format", "horizontal")}, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    lines = []
    progress(0, desc="Generando en ComfyUI…")
    try:
        files = cover_local.generate(d, int(n), with_title=with_title, log=lines.append)
    except Exception as e:
        raise gr.Error(str(e))
    allv = sorted(d.glob("cover_v*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [str(p) for p in allv], "\n".join(lines)


def pl_pick(name, evt: gr.SelectData):
    path = evt.value["image"]["path"] if isinstance(evt.value, dict) else evt.value
    return pl_cover(name, path)


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
        progress(0.05, desc="Claude Code está mirando la portada para planear la animación… (1–2 min)")
        try:
            animate.plan(img)
        except Exception as e:
            raise gr.Error(f"No pude planear la animación: {e}. Pega el plan que te dé Claude en el chat.")
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
            go_idea = gr.Button("Escribir prompts", variant="primary")
            with gr.Accordion("Alternativa: pega aquí un brief que te dio Claude en el chat", open=False):
                pasted = gr.Textbox(label="Brief (JSON)", lines=6)
                load_btn = gr.Button("Cargar propuestas")
            briefs = gr.State([])
            pick = gr.Radio(label="Elige una", visible=False)
            preview = gr.Markdown()
            use = gr.Button("Usar esta → 🎵 Canción", visible=False)

        with gr.Tab("🎵 Canción", id="song"):
            with gr.Row():
                proj = gr.Dropdown(projects(), value=None, label="Proyecto", allow_custom_value=False, scale=3)
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
            with gr.Row():
                song_gpt_btn = gr.Button("Prompt de portada para ChatGPT", scale=0)
                song_gpt = gr.Textbox(label="Prompt para ChatGPT", lines=6, buttons=["copy"], visible=False, scale=4)
            log = gr.Textbox(label="Registro", lines=3)
            outs = [gr.Audio(visible=False, type="filepath") for _ in range(4)]
            with gr.Accordion("📝 Textos para publicar (YouTube e Instagram)", open=False):
                song_pub_btn = gr.Button("Generar textos")
                song_pub = publish_box()

        with gr.Tab("🎚️ Master", id="master"):
            with gr.Row():
                m_pl = gr.Dropdown([ALL_SONGS] + playlist_names(), value=(playlist_names() or [ALL_SONGS])[0],
                                   label="Playlist", scale=2)
                m_prev = gr.Button("◀ Anterior", scale=0)
                m_song = gr.Dropdown(label="Canción  (✅ masterizada · 🎵 con tomas · ⬜ sin generar)", scale=3)
                m_next = gr.Button("Siguiente ▶", scale=0)
                reload_t = gr.Button("↻", scale=0)
            m_info = gr.Markdown()
            with gr.Row():
                take = gr.Dropdown(label="Toma", info="Al elegirla suena en «Antes»; masteriza la que más te guste", scale=5)
                m_del = gr.Button("🗑 Eliminar toma", variant="stop", scale=0)
            m_del_msg = gr.Markdown(visible=False)
            with gr.Row():
                m_del_yes = gr.Button("Sí, eliminar", variant="stop", visible=False, scale=0)
                m_del_no = gr.Button("Cancelar", visible=False, scale=0)
            m_del_done = gr.Markdown()
            with gr.Accordion("🎨 Repaint: rehacer solo un tramo de esta toma", open=False):
                rp_dur = gr.Markdown()
                with gr.Row():
                    rp_start = gr.Slider(0, 300, value=60, step=1, label="Desde (segundos)")
                    rp_end = gr.Slider(0, 300, value=90, step=1, label="Hasta (segundos)")
                rp_cap = gr.Textbox(label="Caption para el tramo (puedes ajustarlo solo para este repaint)", lines=3)
                rp_go = gr.Button("Repintar tramo (2 variantes)")
                rp_log = gr.Textbox(label="Registro", lines=3)
                with gr.Row():
                    rp_a1 = gr.Audio(visible=False, type="filepath")
                    rp_a2 = gr.Audio(visible=False, type="filepath")
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
            with gr.Row():
                pl_gen = gr.Button("Generar canciones faltantes")
                pl_regen = gr.Button("🔁 Regenerar toda la playlist")
            pl_regen_auto = gr.Checkbox(value=False, label="Al regenerar, masterizar la primera toma nueva y dejarla en uso "
                                        "(más rápido; si no, eliges tú en 🎚️ Master)")
            pl_log = gr.Textbox(label="Registro", lines=4)
            with gr.Accordion("🎧 Escuchar la playlist por canción", open=False):
                gr.Markdown("Cada canción con la toma que se usará en el mix. Para cambiar una, usa 🔁 Regenerar una pista.")
                pl_players_out = [gr.Audio(type="filepath", visible=False) for _ in range(MAX_TRACKS)]
            with gr.Accordion("🔁 Regenerar una pista", open=False):
                with gr.Row():
                    rg_track = gr.Dropdown(label="Pista", info="Despliega el menú (▾) y elige la canción a regenerar", scale=3)
                    rg_seed = gr.Textbox(label="Seed (vacío = aleatorio)", scale=1)
                rg_info = gr.Markdown()
                rg_caption = gr.Textbox(label="Caption (puedes ajustarlo; el cambio se guarda en su song.json)", lines=3)
                rg_crackle = gr.Checkbox(label="Quitar crepitar / fuego del caption")
                rg_go = gr.Button("Regenerar tomas")
                rg_log = gr.Textbox(label="Registro", lines=2)
                with gr.Row():
                    rg_a1 = gr.Audio(visible=False, type="filepath")
                    rg_a2 = gr.Audio(visible=False, type="filepath")
                with gr.Row():
                    rg_take = gr.Dropdown(label="Toma a usar en la playlist", info="Al elegirla la puedes escuchar abajo", scale=3)
                    rg_use = gr.Button("Masterizar y usar esta toma", variant="primary", scale=1)
                rg_listen = gr.Audio(type="filepath", visible=False)
                rg_done = gr.Markdown()
            gr.Markdown("**2 · Mix** — une los masters (crossfade o pausa entre canciones), fade in / fade out de 5 s al inicio y al final, normaliza todo y escribe los capítulos.")
            with gr.Row():
                pl_trans = gr.Radio(["Pausa de silencio", "Crossfade"], value="Pausa de silencio", label="Entre canciones")
                pl_trans_s = gr.Slider(0, 8, value=1, step=0.5, label="Segundos")
            pl_mix_btn = gr.Button("Armar mix", variant="primary")
            pl_info = gr.Markdown()
            with gr.Row():
                pl_audio = gr.Audio(label="Mix", type="filepath")
                pl_chapters = gr.Textbox(label="Capítulos para la descripción de YouTube", lines=8, buttons=["copy"])
            gr.Markdown("**3 · Portada** — copia el prompt, pégalo en ChatGPT con tus referencias de Mishka (la oficial primero) y sube aquí la imagen.")
            with gr.Row():
                pl_gpt_btn = gr.Button("Prompt para ChatGPT", scale=0)
                pl_gpt = gr.Textbox(label="Prompt para ChatGPT", lines=6, buttons=["copy"], scale=4)
            with gr.Accordion("Opcional: generar en tu GPU con ComfyUI", open=False):
                with gr.Row():
                    pl_n = gr.Slider(1, 4, value=2, step=1, label="Variantes")
                    pl_title_on = gr.Checkbox(value=True, label="Con título en la imagen")
                    pl_comfy_btn = gr.Button("Generar con ComfyUI (local)")
                pl_gallery = gr.Gallery(label="Variantes (clic = usar como portada)", columns=4, height=260)
                pl_comfy_log = gr.Textbox(label="Registro", lines=2)
            with gr.Row():
                pl_cover_in = gr.Image(label="Portada", type="filepath", sources=["upload", "clipboard"])
                pl_cover_save = gr.Button("Guardar portada", scale=0)
            gr.Markdown("**4 · Animación** — déjalo vacío y Claude Code planea solo qué animar mirando la portada, o pega un plan del chat.")
            pl_spec = gr.Textbox(label="anim_spec.json", lines=5)
            pl_anim_btn = gr.Button("Animar portada")
            with gr.Row():
                pl_check = gr.Image(label="Zonas animadas", type="filepath")
                pl_loop = gr.Video(label="Loop de 10 s")
            gr.Markdown("**5 · Video final** — repite el loop durante todo el mix.")
            pl_vid_btn = gr.Button("Renderizar video", variant="primary")
            pl_vid_log = gr.Textbox(label="Resultado", lines=2)
            pl_video_out = gr.Video(label="Video")
            gr.Markdown("**6 · Publicar** — textos de YouTube (con capítulos) e Instagram, listos para copiar.")
            pl_pub_btn = gr.Button("Generar textos para publicar")
            pl_pub = publish_box()

    # eventos
    go_idea.click(make_briefs, [idea, n_var], [briefs, pick, preview, use])
    load_btn.click(load_pasted, pasted, [briefs, pick, preview, use])
    pick.change(show_brief, [pick, briefs], preview)
    use.click(use_brief, [pick, briefs, idea], [proj, tabs])
    tabs.select(lambda: gr.update(choices=projects()), None, proj)
    fields = [title, caption, lyrics, bpm, key, ts, duration, batch, seed]
    proj.change(load_song, proj, fields)
    reload_p.click(lambda: gr.update(choices=projects()), None, proj)
    go.click(gen_song, fields + [no_crackle], [log] + outs + [proj])
    m_pl.change(m_pick_playlist, m_pl, m_song)
    app.load(m_pick_playlist, m_pl, m_song)
    m_song.change(m_pick_song, m_song, [take, m_info, a_before])
    take.change(m_preview, take, [a_before, rp_start, rp_end, rp_dur])
    take.change(lambda: (gr.update(visible=False), gr.update(visible=False), gr.update(visible=False)),
                None, [m_del_msg, m_del_yes, m_del_no])
    m_del.click(m_delete_ask, take, [m_del_msg, m_del_yes, m_del_no])
    m_del_no.click(lambda: (gr.update(visible=False), gr.update(visible=False), gr.update(visible=False)),
                   None, [m_del_msg, m_del_yes, m_del_no])
    m_del.click(lambda: "", None, m_del_done)
    m_del_yes.click(m_delete, take, m_del_done).success(
        lambda: (gr.update(visible=False), gr.update(visible=False), gr.update(visible=False)),
        None, [m_del_msg, m_del_yes, m_del_no]).then(
        lambda p, s_: gr.update(choices=m_song_list(p), value=s_), [m_pl, m_song], m_song).then(
        m_pick_song, m_song, [take, m_info, a_before])
    m_song.change(rp_caption, m_song, rp_cap)
    rp_go.click(lambda: "⏳ Repintando el tramo con ACE-Step… no cierres Studio.", None, rp_log).then(
        rp_run, [m_song, take, rp_start, rp_end, rp_cap], [rp_log, rp_a1, rp_a2]).success(
        lambda s_: m_pick_song(s_)[0], m_song, take)
    m_prev.click(lambda p, s_: m_step(p, s_, -1), [m_pl, m_song], m_song)
    m_next.click(lambda p, s_: m_step(p, s_, 1), [m_pl, m_song], m_song)
    reload_t.click(lambda p: gr.update(choices=[ALL_SONGS] + playlist_names(), value=p), m_pl, m_pl).then(
        m_pick_playlist, m_pl, m_song)
    pl_name.change(pl_status, pl_name, [pl_table, pl_cover_in, pl_loop, pl_video_out])
    # el menú arranca con la primera playlist ya elegida: cargar su estado y sus pistas al abrir la página
    app.load(pl_status, pl_name, [pl_table, pl_cover_in, pl_loop, pl_video_out])
    app.load(pl_track_choices, pl_name, rg_track)
    app.load(pl_players, pl_name, pl_players_out)
    app.load(pl_transition, pl_name, [pl_trans, pl_trans_s])
    pl_name.change(pl_transition, pl_name, [pl_trans, pl_trans_s])
    pl_name.change(pl_players, pl_name, pl_players_out)
    rg_take.change(rg_preview, [rg_track, rg_take], rg_listen)
    pl_reload.click(lambda: gr.update(choices=playlist_names()), None, pl_name)
    pl_pub_btn.click(pl_publish, pl_name, pl_pub)
    song_pub_btn.click(song_publish, proj, song_pub)
    pl_name.change(pl_track_choices, pl_name, rg_track)
    rg_track.change(rg_load, rg_track, [rg_caption, rg_info, rg_take])
    rg_go.click(lambda: ("⏳ Generando 2 tomas nuevas con ACE-Step… (tarda un poco, no cierres Studio)", ""),
                None, [rg_log, rg_done]).then(
        rg_generate, [rg_track, rg_caption, rg_seed, rg_crackle], [rg_log, rg_take, rg_a1, rg_a2]).success(
        lambda: "🎵 Listo. Escucha las tomas, elige una en «Toma a usar» y dale **Masterizar y usar esta toma**.", None, rg_done)
    rg_use.click(lambda: "⏳ **Masterizando…** separando batería y bajo, normalizando y revisando picos. "
                         "No cierres Studio; tarda unos segundos (más si es la primera vez de esta toma).", None, rg_done).then(
        rg_master, [rg_track, rg_take], rg_done).then(
        pl_status, pl_name, [pl_table, pl_cover_in, pl_loop, pl_video_out]).then(
        rg_load, rg_track, [rg_caption, rg_info, rg_take]).then(pl_players, pl_name, pl_players_out)
    pl_regen.click(pl_regen_all, [pl_name, pl_regen_auto], pl_log).then(
        pl_status, pl_name, [pl_table, pl_cover_in, pl_loop, pl_video_out]).then(pl_players, pl_name, pl_players_out)
    pl_gen.click(pl_generate, pl_name, pl_log).then(pl_status, pl_name, [pl_table, pl_cover_in, pl_loop, pl_video_out]).then(
        pl_players, pl_name, pl_players_out)
    pl_mix_btn.click(pl_mix, [pl_name, pl_trans, pl_trans_s], [pl_audio, pl_chapters, pl_info]).then(
        pl_status, pl_name, [pl_table, pl_cover_in, pl_loop, pl_video_out])
    pl_cover_save.click(pl_cover, [pl_name, pl_cover_in], pl_cover_in)
    pl_gpt_btn.click(pl_gpt_prompt, pl_name, pl_gpt)
    song_gpt_btn.click(song_gpt_prompt, proj, song_gpt)
    pl_comfy_btn.click(pl_comfy, [pl_name, pl_n, pl_title_on], [pl_gallery, pl_comfy_log])
    pl_gallery.select(pl_pick, pl_name, pl_cover_in)
    pl_anim_btn.click(pl_anim, [pl_name, pl_spec], [pl_check, pl_loop])
    pl_vid_btn.click(pl_video, pl_name, [pl_video_out, pl_vid_log])
    run.click(do_master, [take, drums, bass, lufs, ceil], [a_before, a_after, report, files]).success(
        lambda p, s_: (gr.update(choices=m_song_list(p), value=s_)), [m_pl, m_song], m_song).then(
        m_pick_song, m_song, [take, m_info, a_before])

if __name__ == "__main__":
    app.launch(server_port=7870, inbrowser=True, allowed_paths=[str(OUT)], theme=THEME)
