"""Mishka LoFi Studio — interfaz local del pipeline.

    python ui.py        → abre http://localhost:7870

Pestañas:
  💡 Idea     de una idea a título, descripción y prompt musical (Claude Code con tu suscripción)
  🎵 Canción  generar tomas con ACE-Step (necesita start_api_1_7.bat corriendo)
  🎚️ Master   bajar batería y bajo + normalizar + evitar clips, con comparación antes / después
  📀 Playlist generar lo que falte, unir en un mix con capítulos, portada animada y video final

Arriba, una barra dice si ACE-Step, Claude Code y la GPU están listos y qué se está procesando.
Cada trabajo largo muestra su avance (barra, etapa, tiempo, cuánto falta) en un panel bajo su botón.
"""
import html, json, os, re, shutil, subprocess, sys, threading, time
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
import keycheck  # noqa: E402
import export as exportmod  # noqa: E402

OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)
PLAYLISTS = ROOT / "playlists"
PLAYLISTS.mkdir(exist_ok=True)
K = gr.update()  # "no cambies este componente"


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "sin-titulo"


def projects():
    return sorted(p.name for p in OUT.iterdir() if p.is_dir() and not p.name.startswith("_"))


def playlist_names():
    return sorted(p.stem for p in PLAYLISTS.glob("*.json"))


# ---------------- avance y estado ----------------
RUNNING = {}   # trabajos en curso, para la barra de arriba (se ven desde cualquier pestaña)
_LOCK = threading.Lock()


def clock(s):
    s = int(max(s, 0))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def ago(ts):
    d = time.time() - ts
    if d < 90:
        return "hace un momento"
    if d < 3600:
        return f"hace {int(d // 60)} min"
    if d < 86400:
        return f"hace {int(d // 3600)} h"
    return "ayer" if d < 172800 else f"hace {int(d // 86400)} días"


def esc(s):
    return html.escape(str(s))


def n_(n, one, many):
    """'1 canción' / '3 canciones'"""
    return f"{n} {one if n == 1 else many}"


def ok(msg, title="Listo"):
    gr.Success(msg, title=title)


def info(msg, title="Aviso"):
    gr.Info(msg, title=title)


def warn(msg, title="Ojo"):
    gr.Warning(msg, title=title)


class Job:
    """Un trabajo largo: registro, avance (0–1) y etapa. Lo pinta job_card()."""

    def __init__(self, title, typical=None, eta_from_frac=True):
        self.title, self.typical, self.eta_from_frac = title, typical, eta_from_frac
        self.lines, self.frac, self.stage, self.t0 = [], None, "", time.time()
        self.items = None  # (total, hechas, inicio de la actual) en trabajos de varias canciones

    def log(self, *a):
        self.lines.append(" ".join(str(x) for x in a))

    def progress(self, frac=None, text=""):
        if frac is not None:
            self.frac = max(0.0, min(1.0, float(frac)))
        if text:
            self.stage = text

    def sub(self, i, n, prefix="", lo=0.0, hi=1.0):
        """Avance de la parte i (desde 0) de n, en el tramo [lo, hi] de esa parte."""
        return lambda f=None, text="": self.progress(None if f is None else (i + lo + (hi - lo) * f) / n,
                                                     f"{prefix}{text}")

    @property
    def elapsed(self):
        return time.time() - self.t0

    def eta(self):
        if self.items:
            n, done, cur = self.items
            if not done or done >= n:
                return None
            return max((cur - self.t0) / done * (n - done) - (time.time() - cur), 0)
        if self.typical:
            return self.typical - self.elapsed if self.elapsed < self.typical else None
        if self.eta_from_frac and self.frac and 0.08 <= self.frac < 0.99 and self.elapsed > 4:
            return self.elapsed * (1 - self.frac) / self.frac
        return None

    def text(self):
        return "\n".join(self.lines)


def card(kind, title, detail="", frac=None, right=""):
    """Panel de estado. kind: run | wait | ok | warn | err."""
    now = time.time()  # las animaciones siguen en su fase aunque el panel se repinte
    icon = {"run": f'<span class="mk-spin" style="animation-delay:-{now % 0.8:.2f}s"></span>',
            "wait": "⏳", "ok": "✅", "warn": "⚠️", "err": "❌"}[kind]
    bar = ""
    if kind in ("run", "wait"):
        bar = (f'<div class="mk-bar"><div style="width:{frac * 100:.1f}%"></div></div>' if frac is not None else
               f'<div class="mk-bar indet"><div style="animation-delay:-{now % 1.4:.2f}s"></div></div>')
    det = f'<div class="mk-detail">{esc(detail)}</div>' if detail else ""
    return (f'<div class="mk-card {kind}"><div class="mk-head">{icon}<b>{esc(title)}</b>'
            f'<span class="mk-right">{esc(right)}</span></div>{bar}{det}</div>')


def job_card(job):
    pct = f"{job.frac * 100:.0f}% · " if job.frac is not None else ""
    right = clock(job.elapsed)
    e = job.eta()
    if e is not None:
        right += f" · faltan ~{clock(e)}"
    return card("run", job.title, pct + (job.stage or "trabajando…"), job.frac, right)


def done_card(job, title, detail="", kind="ok"):
    return card(kind, title, detail, right=f"en {clock(job.elapsed)}")


def why(e):
    """Mensaje claro para el usuario a partir de una excepción."""
    if isinstance(e, subprocess.CalledProcessError):
        return f"ffmpeg terminó con error (código {e.returncode}). El detalle está en la ventana negra de Studio."
    if e.__class__.__name__ in ("ConnectionError", "ConnectTimeout", "ReadTimeout"):
        return "ACE-Step dejó de responder. ¿Se cerró su ventana? Ábrelo con start_api_1_7.bat y vuelve a intentar."
    return str(e) or e.__class__.__name__


def fail(job, title, e, hint=""):
    msg = why(e)
    if len(msg) > 600:  # un traceback largo no cabe en un aviso: va completo a la ventana de Studio
        print(msg, file=sys.stderr)
        msg = msg[:600].rsplit(" ", 1)[0] + "… (detalle completo en la ventana negra de Studio)"
    msg += f"\n{hint}" if hint else ""
    warn(msg if len(msg) < 260 else msg[:250].rsplit(" ", 1)[0] + "…", title=title)
    return card("err", title, msg, right=f"tras {clock(job.elapsed)}")


WAIT = card("wait", "Empezando…", "Si ya hay un trabajo igual corriendo, este espera su turno y arranca solo.")


def live(job, fn, paint, every=0.5):
    """Corre fn() en un hilo y, mientras termina, va devolviendo paint() para refrescar la UI.
    Uso dentro de un generador:  resultado = yield from live(job, fn, paint)
    Si cierras la pestaña, el trabajo sigue en segundo plano hasta terminar (y se ve en la barra de arriba)."""
    box = {}

    def target():
        try:
            box["out"] = fn()
        except BaseException as e:  # noqa: BLE001 — se reporta en la UI
            box["err"] = e
        finally:
            with _LOCK:
                RUNNING.pop(id(job), None)

    with _LOCK:
        RUNNING[id(job)] = job
    th = threading.Thread(target=target, daemon=True)
    th.start()
    yield paint()
    while True:
        th.join(every)
        if not th.is_alive():
            break
        yield paint()
    if "err" in box:
        raise box["err"]
    return box.get("out")


def painter(job, n_rest, with_log=True):
    """paint() estándar: [panel, registro (solo si cambió), sin cambios × n_rest]."""
    last = [None]

    def paint():
        out = [job_card(job)]
        if with_log:
            t = job.text()
            out.append(t if t != last[0] else K)
            last[0] = t
        out += [K] * n_rest
        return out[0] if len(out) == 1 else tuple(out)
    return paint


# ---------------- barra de estado ----------------
_SVC = {"ace": (0.0, None), "gpu": None}


def _probe_gpu():
    """Revisa PyTorch/CUDA en otro proceso (no carga torch en Studio)."""
    code = ("import torch; ok = torch.cuda.is_available(); "
            "print('ok' if ok else 'cpu'); print(torch.cuda.get_device_name(0) if ok else torch.__version__)")
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=180)
        lines = r.stdout.strip().splitlines()
        _SVC["gpu"] = (lines[0], lines[1]) if len(lines) >= 2 else ("err", (r.stderr.strip().splitlines() or ["?"])[-1])
    except Exception as e:  # noqa: BLE001
        _SVC["gpu"] = ("err", str(e))


threading.Thread(target=_probe_gpu, daemon=True).start()


def chip(kind, text, tip=""):
    return f'<span class="mk-chip {kind}" title="{esc(tip)}">{esc(text)}</span>'


def services_html(force=False):
    t, info = _SVC["ace"]
    if force or time.time() - t > 8:
        info = music.server_info(timeout=1.5)
        _SVC["ace"] = (time.time(), info)
    chips = []
    if info is None:
        chips.append(chip("bad", "ACE-Step apagado", "Ábrelo con start_api_1_7.bat en D:\\ACE-Step-1.5"))
    else:
        dit = (info.get("loaded_model") or "").replace("acestep-", "")
        lm = info.get("loaded_lm_model") or ""
        chips.append(chip("ok", f"ACE-Step {dit}".strip(), music.API))
        if "1.7" in lm:
            chips.append(chip("ok", "LM 1.7B"))
        elif lm:
            chips.append(chip("warn", f"LM {lm.replace('acestep-5Hz-lm-', '')} · abre start_api_1_7.bat para el 1.7B"))
        elif info.get("llm_initialized") is False:
            chips.append(chip("warn", "LM sin cargar (se carga al generar)"))
    import claude_local
    if claude_local.available():
        chips.append(chip("ok", "Claude Code"))
    elif os.getenv("ANTHROPIC_API_KEY"):
        chips.append(chip("ok", "Claude vía API"))
    else:
        chips.append(chip("off", "Claude Code no instalado", "Instálalo y corre `claude` una vez; mientras, pega briefs del chat"))
    g = _SVC["gpu"]
    if g is None:
        chips.append(chip("off", "GPU: revisando…"))
    elif g[0] == "ok":
        chips.append(chip("ok", f"GPU {g[1].replace('NVIDIA GeForce ', '')}"))
    else:
        chips.append(chip("bad", "Demucs en CPU (lento)", f"{g[1]} — corre python gpu_check.py"))
    with _LOCK:
        jobs = list(RUNNING.values())
    for j in jobs:
        pct = f" {j.frac * 100:.0f}%" if j.frac is not None else ""
        chips.append(chip("run", f"⏳ {j.title}{pct} · {clock(j.elapsed)}", j.stage))
    return '<div class="mk-chips">' + "".join(chips) + "</div>"


def open_folder(path):
    p = Path(path)
    if not p.exists():
        warn("Esa carpeta todavía no existe: se crea al generar.")
        return
    try:
        if sys.platform == "win32":
            os.startfile(str(p))  # noqa: S606
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p)])
        info(str(p), title="Abriendo carpeta")
    except Exception:  # noqa: BLE001
        info(str(p), title="La carpeta está en")


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
    ok(f"{n_(len(bs), 'propuesta cargada', 'propuestas cargadas')}: elige abajo")
    return bs, gr.update(choices=titles, value=titles[0], visible=True), brief_md(bs[0]), gr.update(visible=True)


def make_briefs(idea, n):
    """Salidas: [panel, briefs, pick, preview, use]"""
    if not idea.strip():
        warn("Escribe una idea primero")
        yield "", K, K, K, K
        return
    import claude_local
    if not claude_local.available() and not os.getenv("ANTHROPIC_API_KEY"):
        msg = ("No encuentro Claude Code en esta compu. Instálalo (https://code.claude.com), corre `claude` una vez "
               "para entrar con tu cuenta y reinicia Studio. Mientras, pide el brief en el chat y pégalo abajo.")
        warn(msg, title="Falta Claude Code")
        yield card("err", "Falta Claude Code", msg), K, K, K, K
        return
    via = "Claude Code (tu suscripción)" if briefmod.backend() == "claude-code" else "la API de Anthropic"
    job = Job(f"Claude está escribiendo {n_(int(n), 'propuesta', 'propuestas')}")
    job.progress(None, f"vía {via} · suele tardar 1–2 min")
    try:
        bs = yield from live(job, lambda: briefmod.ask_claude(idea, int(n)), painter(job, 4, with_log=False))
    except Exception as e:  # noqa: BLE001
        yield fail(job, "No se pudieron escribir los prompts", e,
                   "Si sigue fallando, pide el brief en el chat de Claude y pégalo abajo."), K, K, K, K
        return
    titles = [b["title"] for b in bs]
    ok(n_(len(bs), "propuesta lista", "propuestas listas"))
    yield (done_card(job, n_(len(bs), "propuesta lista", "propuestas listas"), "Elige una abajo y dale «Usar esta → 🎵 Canción»."),
           bs, gr.update(choices=titles, value=titles[0], visible=True), brief_md(bs[0]), gr.update(visible=True))


def show_brief(title, bs):
    b = next((b for b in bs or [] if b["title"] == title), None)
    return brief_md(b) if b else ""


def use_brief(title, bs, idea):
    b = next(b for b in bs if b["title"] == title)
    d = briefmod.save(b, idea)
    ok(f"Guardado en out/{d.name}: ya está cargado en 🎵 Canción")
    return gr.update(choices=projects(), value=d.name), gr.Tabs(selected="song")


# ---------------- 🎵 Canción ----------------
SONG_FIELDS = ["title", "caption", "lyrics", "bpm", "key", "time_signature", "duration", "batch_size", "seed"]


def load_song(project):
    if not project:
        return [K] * len(SONG_FIELDS)
    sp = OUT / project / "song.json"
    s = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {"title": project}
    return [s.get("title", ""), s.get("caption", ""), s.get("lyrics", ""), s.get("bpm", 70), s.get("key", ""),
            str(s.get("time_signature", 4)), s.get("duration", 210), s.get("batch_size", 2),
            "" if s.get("seed") is None else str(s.get("seed"))]


def lint_md(caption, lyrics="", bpm=0, key="", duration=0):
    """Revisión en vivo del caption según la guía de prompts."""
    if not (caption or "").strip():
        return ""
    try:
        w = music.lint({"caption": caption, "lyrics": lyrics or "", "bpm": int(bpm or 0), "key": key or "",
                        "duration": int(duration or 0)})
    except (TypeError, ValueError):
        return ""
    n = len(caption.split())
    if not w:
        return f"✅ Caption según la guía · {n} palabras"
    return "⚠️ **Revisa antes de generar:**\n" + "\n".join(f"- {x}" for x in w)


def ace_typical():
    """Segundos que suele tardar ACE-Step (su promedio) y trabajos en su fila."""
    st = music.stats()
    avg = st.get("avg_job_seconds")
    return (float(avg) if avg else None), int(st.get("queue_size") or 0)


def tone_short(h):
    """'✅ 9%' para una entrada de takes.json (vacío si no se ha medido)."""
    t = (h or {}).get("tono") or {}
    return f"{keycheck.verdict(t['fuera'])} {t['fuera']:.0f}%" if t.get("fuera") is not None else ""


def tones_summary(slug, files):
    """'take_1: ✅ 9% · take_2: ⚠️ 27% (suena fuera de tono)' para las tomas recién generadas."""
    hist = take_history(slug)
    parts = []
    for f in files:
        t = (hist.get(Path(f).name) or {}).get("tono") or {}
        if t.get("fuera") is None:
            continue
        v = keycheck.verdict(t["fuera"])
        note = " (suena fuera de tono)" if v == "⚠️" else (" (revísala)" if v == "🟡" else "")
        parts.append(f"{Path(f).stem}: {v} {t['fuera']:.0f}% fuera de escala{note}")
    return ("Tono: " + " · ".join(parts)) if parts else ""


TONE_LEGEND = "🎯 notas fuera de escala: ✅ hasta 18% · 🟡 18–25% · ⚠️ más de 25% (suele sonar desafinada)"


def gen_song(title, caption, lyrics, bpm, key, ts, duration, batch, seed, no_crackle):
    """Salidas: [panel, registro, audio×4, proyecto]"""
    if not title or not caption:
        warn("Falta título o caption")
        yield ("", K) + (K,) * 5
        return
    song = {"title": title, "caption": caption, "lyrics": lyrics or "[Instrumental]", "bpm": int(bpm),
            "key": key, "time_signature": int(ts), "duration": int(duration), "batch_size": int(batch),
            "seed": int(seed) if str(seed).strip() else None}
    d = OUT / slugify(title)
    d.mkdir(parents=True, exist_ok=True)
    (d / "song.json").write_text(json.dumps(song, indent=2, ensure_ascii=False), encoding="utf-8")
    if no_crackle:
        song["caption"] = music.strip_crackle(song["caption"])
    avg, queue = ace_typical()
    job = Job(f"Generando {n_(int(batch), 'toma', 'tomas')} de «{title}»", typical=avg, eta_from_frac=False)
    job.progress(None, f"hay {n_(queue, 'trabajo', 'trabajos')} antes en ACE-Step" if queue else "enviando a ACE-Step")
    try:
        files = yield from live(job, lambda: music.generate(song, d, song["seed"], log=job.log, progress=job.progress),
                                painter(job, 5))
    except Exception as e:  # noqa: BLE001
        yield (fail(job, "No se pudo generar", e), job.text()) + (K,) * 5
        return
    audios = [gr.update(value=str(f), label=f.name, visible=True) for f in files[:4]]
    audios += [gr.update(value=None, visible=False)] * (4 - len(audios))
    ok(f"{n_(len(files), 'toma nueva', 'tomas nuevas')} de «{title}»")
    tone = tones_summary(d.name, files)
    yield (done_card(job, f"{n_(len(files), 'toma nueva', 'tomas nuevas')} de «{title}»",
                     (tone + "\n" if tone else "") +
                     "Escúchalas abajo. Para usarlas en una playlist, masteriza la que te guste en 🎚️ Master."),
           job.text(), *audios, gr.update(choices=projects(), value=d.name))


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


def m_refresh_list(pl, slug):
    return gr.update(choices=m_song_list(pl), value=slug)


def take_history(slug):
    tj = OUT / slug / "songs" / "takes.json"
    hist = json.loads(tj.read_text(encoding="utf-8")) if tj.exists() else []
    return {h["file"]: h for h in hist if not h.get("deleted")}


def m_pick_song(slug):
    """Tomas de la canción (la más nueva primero); la que está en uso marcada y preseleccionada."""
    if not slug:
        return gr.update(choices=[], value=None), "", None
    st = mixmod.track_status(slug)
    used = st["chosen"].name.replace("_master.wav", ".wav") if st["chosen"] else None
    hist = take_history(slug)
    songs = OUT / slug / "songs"

    def label(t):
        h = hist.get(t, {})
        l = t[:-4]
        if h.get("created"):
            l += f"  · {h['created'][5:]}"
        if tone_short(h):
            l += f"  · 🎯 {tone_short(h)}"
        if h.get("repaint_of"):
            a, b = h.get("repaint_range", ["?", "?"])
            l += f"  🎨 repaint {a:.0f}–{b:.0f}s de {h['repaint_of'][:-4]}"
        if t == used:
            l += "  ✅ en uso"
        elif (songs / t.replace(".wav", "_master.wav")).exists():
            l += "  🎚 masterizada"
        return l
    choices = [(label(t), f"{slug}/songs/{t}") for t in st["takes"]]
    value = f"{slug}/songs/{used}" if used else (choices[0][1] if choices else None)
    sj = OUT / slug / "song.json"
    song = json.loads(sj.read_text(encoding="utf-8")) if sj.exists() else {}
    info = (f"**{st['title']}** · {song.get('bpm', '?')} BPM · {song.get('key', '?')} · "
            f"{len(st['takes'])} tomas (la más nueva arriba) · en uso: **{used[:-4] if used else 'ninguna'}**")
    if not st["takes"]:
        info += " · ⬜ sin generar (genérala en 📀 Playlist o 🎵 Canción)"
    elif not used:
        info += f" · si no masterizas ninguna, el mix usará la más reciente ({st['takes'][0][:-4]})"
    if music.key_scale(song.get("key", "")) in music.FLAT_MAP:
        info += (f"  \n⚠️ Tonalidad con bemol ({song.get('key')}): ACE-Step no la respeta y suele sonar fuera de tono; "
                 f"al regenerar se usará {music.safe_key(song.get('key'))}.")
    unmeasured = sum(1 for t in st["takes"] if not (hist.get(t) or {}).get("tono"))
    info += f"  \n{TONE_LEGEND}" + (f" · {n_(unmeasured, 'toma sin medir', 'tomas sin medir')}: dale «🎯 Medir tono»"
                                    if unmeasured else "")
    return gr.update(choices=choices, value=value), info, (str(OUT / value) if value else None)


def m_tone(take):
    """Detalle del tono de la toma elegida."""
    if not take:
        return ""
    slug, name = Path(take).parts[0], Path(take).name
    t = (take_history(slug).get(name) or {}).get("tono")
    if not t or t.get("fuera") is None:
        return "🎯 Tono sin medir: dale «🎯 Medir tono»."
    sj = OUT / slug / "song.json"
    want = json.loads(sj.read_text(encoding="utf-8")).get("key", "") if sj.exists() else ""
    v = keycheck.verdict(t["fuera"])
    msg = {"✅": "suena dentro de su escala", "🟡": "algunas notas fuera de escala: escúchala con atención",
           "⚠️": "muchas notas fuera de escala: suele sonar desafinada"}[v]
    return (f"🎯 {v} **{t['fuera']:.0f}% fuera de escala** — {msg} · suena en **{t['real']}** "
            f"(se pidió {music.safe_key(want) or '?'}) · afinación {t['afinacion']:+.1f} cents")


def m_measure(slug):
    """Mide el tono de las tomas de la canción que no lo tengan."""
    if not slug:
        raise gr.Error("Elige una canción")
    n = keycheck.measure_song(OUT / slug, log=lambda *_: None)
    if n:
        ok(f"{n_(n, 'toma medida', 'tomas medidas')}: el resultado aparece junto a cada toma (🎯)")
    else:
        info("Todas las tomas de esta canción ya estaban medidas")


def m_step(pl, slug, delta):
    songs = [v for _, v in m_song_list(pl)]
    if not songs:
        return K
    i = songs.index(slug) if slug in songs else -1
    return gr.update(value=songs[(i + delta) % len(songs)])


def after_delete(slug, name):
    """Qué toma quedaría en uso si se elimina `name`."""
    songs = OUT / slug / "songs"
    left = [t for t in mixmod.track_status(slug)["takes"] if t != f"{name}.wav"]
    masters = sorted((m for m in songs.glob("*_master.wav") if m.name.replace("_master.wav", ".wav") in left),
                     key=lambda p: p.stat().st_mtime)
    if masters:
        return f"la playlist pasará a usar **{masters[-1].stem.replace('_master', '')}** (la masterizaste antes)"
    if left:
        return (f"la canción se queda sin master: el mix usaría la más reciente (**{left[0][:-4]}**) con valores "
                "por defecto, o masteriza la que prefieras")
    return "la canción se queda sin tomas: tendrás que generarla de nuevo"


def m_delete_ask(take):
    if not take:
        raise gr.Error("Elige la toma a eliminar")
    name, slug = Path(take).stem, Path(take).parts[0]
    st = mixmod.track_status(slug)
    in_use = st["chosen"] is not None and st["chosen"].name == f"{name}_master.wav"
    note = f" ⚠️ **Es la toma en uso**: {after_delete(slug, name)}." if in_use else ""
    return (gr.update(value=f"¿Eliminar **{name}**?{note} Se mueve a la papelera de la canción (`songs/_papelera`), "
                            "junto con su master y sus stems.", visible=True),
            gr.update(value=f"Sí, eliminar {name}", visible=True), gr.update(visible=True))


def m_delete(take):
    """Mueve la toma, su master y sus stems a songs/_papelera (recuperable, sin pisar nada de ahí)."""
    if not take:
        raise gr.Error("Elige la toma a eliminar")
    src = OUT / take
    if not src.exists():
        raise gr.Error("Esa toma ya no existe (¿la moviste a mano?). Dale ↻ para actualizar la lista.")
    songs, slug, name = src.parent, Path(take).parts[0], src.stem
    trash = songs / "_papelera"
    trash.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    before = mixmod.track_status(slug)
    was_used = before["chosen"] is not None and before["chosen"].name == f"{name}_master.wav"
    consequence = after_delete(slug, name) if was_used else ""

    def dest(p):
        d = trash / p.name
        return d if not d.exists() else trash / f"{p.stem}__{stamp}{p.suffix}"
    moved = 0
    for f in [src, *songs.glob(f"{name}_master.*")]:
        if f.exists():
            shutil.move(str(f), str(dest(f)))
            moved += 1
    stems = songs / "stems" / name
    if stems.exists():
        d = trash / f"stems_{name}"
        shutil.move(str(stems), str(d if not d.exists() else trash / f"stems_{name}__{stamp}"))
    tj = songs / "takes.json"
    if tj.exists():  # se marca solo la entrada viva de esta toma (puede haber otra borrada con el mismo nombre)
        hist = json.loads(tj.read_text(encoding="utf-8"))
        for h in reversed(hist):
            if h.get("file") == src.name and not h.get("deleted"):
                h["deleted"] = time.strftime("%Y-%m-%d %H:%M")
                break
        tj.write_text(json.dumps(hist, indent=2, ensure_ascii=False), encoding="utf-8")
    ok(f"{name} está en la papelera", title="Toma eliminada")
    msg = f"🗑 **{name}** movida a la papelera ({moved} archivos + stems)."
    if was_used:
        msg += f" Era la toma en uso: {consequence}. Vuelve a **Armar mix** para que la playlist lo refleje."
    return msg + f" Para recuperarla, sácala de `{trash}`."


def m_preview(take):
    if not take:
        return None, K, K, ""
    import soundfile as sf
    dur = sf.info(str(OUT / take)).duration
    return (str(OUT / take), gr.update(maximum=round(dur), value=min(60, round(dur) - 30)),
            gr.update(maximum=round(dur), value=min(90, round(dur))), f"Duración de la toma: **{int(dur // 60)}:{int(dur % 60):02d}**")


def rp_caption(slug):
    sj = OUT / slug / "song.json" if slug else None
    return json.loads(sj.read_text(encoding="utf-8")).get("caption", "") if sj and sj.exists() else ""


def rp_run(slug, take, start, end, caption):
    """Salidas: [panel, registro, variante 1, variante 2]"""
    if not slug or not take:
        warn("Elige canción y toma")
        yield "", K, K, K
        return
    if end - start < 5:
        warn("El tramo debe durar al menos 5 segundos")
        yield "", K, K, K
        return
    song = json.loads((OUT / slug / "song.json").read_text(encoding="utf-8"))
    if caption.strip():
        song["caption"] = caption.strip()   # solo para este repaint; no cambia song.json
    avg, _ = ace_typical()
    job = Job(f"Repintando {start:.0f}–{end:.0f} s de {Path(take).stem}", typical=avg, eta_from_frac=False)
    try:
        files = yield from live(job, lambda: music.repaint(song, OUT / take, float(start), float(end), log=job.log,
                                                           progress=job.progress), painter(job, 2))
    except Exception as e:  # noqa: BLE001
        yield fail(job, "No se pudo repintar", e), job.text(), K, K
        return
    ups = [gr.update(value=str(f), label=f"{f.stem} (repaint {start:.0f}–{end:.0f}s)", visible=True) for f in files[:2]]
    ups += [gr.update(value=None, visible=False)] * (2 - len(ups))
    ok(f"{n_(len(files), 'variante', 'variantes')} del tramo")
    yield (done_card(job, f"{n_(len(files), 'variante', 'variantes')} del tramo {start:.0f}–{end:.0f} s",
                     "Ya aparecen en «Toma» (🎨). Elige la que te guste y masterízala."), job.text(), *ups)


def do_master(take, drums_db, bass_db, lufs, ceiling):
    """Salidas: [panel, antes, después, reporte, archivos]"""
    if not take:
        warn("Elige una toma")
        yield "", K, K, K, K
        return
    wav = OUT / take
    job = Job(f"Masterizando {wav.stem}")
    if (abs(drums_db) > 0.05 or abs(bass_db) > 0.05) and not master.stems_cached(wav):
        job.progress(0, "primera vez de esta toma: separa batería y bajo (~30–60 s en GPU)")
    try:
        rep = yield from live(job, lambda: master.master(wav, drums_db, lufs, ceiling, log=job.log, bass_db=bass_db,
                                                         progress=job.progress), painter(job, 4, with_log=False))
    except Exception as e:  # noqa: BLE001
        yield fail(job, "No se pudo masterizar", e), K, K, K, K
        return
    b, a = rep["before"], rep["after"]
    md = (f"| | LUFS | True peak | Muestras clipeadas |\n|---|---|---|---|\n"
          f"| Antes | {b['lufs']} | {b['true_peak_dbtp']} dBTP | {b['clipped_samples']} |\n"
          f"| **Después** | **{a['lufs']}** | **{a['true_peak_dbtp']} dBTP** | **{a['clipped_samples']}** |\n\n"
          f"batería {drums_db:+.0f} dB · bajo {bass_db:+.0f} dB · ganancia {rep['gain_db']:+.1f} dB")
    title = "Master listo, sin clips" if rep["ok"] else "Master listo, pero el pico quedó sobre el techo"
    (gr.Success if rep["ok"] else gr.Warning)(f"{wav.stem}: {a['lufs']} LUFS, pico {a['true_peak_dbtp']} dBTP", title=title)
    yield (done_card(job, title, f"{wav.stem} queda como la toma en uso de la canción. "
                     "Si ya habías armado el mix, vuelve a armarlo.", kind="ok" if rep["ok"] else "warn"),
           str(wav), rep["files"][0], md, rep["files"])


# ---------------- 📀 Playlist ----------------
def pl_load(name):
    return json.loads((PLAYLISTS / f"{name}.json").read_text(encoding="utf-8"))


def pl_dir(name):
    d = OUT / "_playlists" / pl_load(name)["slug"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_cover(d):
    return next((d / n for n in ("cover.png", "cover.jpg") if (d / n).exists()), None)


def pl_checklist(name):
    """Checklist de la playlist: qué está listo, qué falta y qué quedó desactualizado."""
    if not name:
        return ""
    pl, d = pl_load(name), pl_dir(name)
    sts = mixmod.status(pl)
    n = len(sts)
    mastered = sum(1 for s in sts if s["chosen"])
    with_takes = sum(1 for s in sts if s["takes"])
    rows = []
    if mastered == n:
        rows.append(("1 · Canciones", f"✅ {n}/{n} con toma masterizada"))
    else:
        parts = [f"✅ {n_(mastered, 'masterizada', 'masterizadas')}"] if mastered else []
        if with_takes - mastered:
            parts.append(f"🎵 {with_takes - mastered} con tomas sin masterizar (el mix usará la más reciente)")
        if n - with_takes:
            parts.append(f"⬜ {n - with_takes} sin generar")
        rows.append(("1 · Canciones", " · ".join(parts)))
    ms = mixmod.mix_state(pl)
    mix_wav = d / "mix.wav"
    if ms["state"] == "missing":
        rows.append(("2 · Mix", "⬜ falta armarlo" + (" (primero genera las canciones que faltan)" if n - with_takes else "")))
    elif ms["state"] == "ok":
        rows.append(("2 · Mix", f"✅ al día · {ms['duration']} · armado {ago(ms['built'])}"))
    else:
        rows.append(("2 · Mix", f"⚠️ **desactualizado** ({n_(len(ms['reasons']), 'cambio', 'cambios')}, abajo) — vuelve a **Armar mix**"))
    cover = find_cover(d)
    rows.append(("3 · Portada", f"✅ {cover.name}" if cover else "⬜ falta (pide el prompt para ChatGPT y súbela)"))
    loop = d / "cover_loop.mp4"
    if loop.exists():
        stale = cover is not None and cover.stat().st_mtime > loop.stat().st_mtime
        rows.append(("4 · Animación", "⚠️ la portada es más nueva — vuelve a **Animar portada**" if stale else "✅ loop de 10 s"))
    else:
        rows.append(("4 · Animación", "⬜ opcional (sin animación, el video usa la portada fija)"))
    vid = d / "video.mp4"
    if vid.exists():
        vt, why_v = vid.stat().st_mtime, []
        if mix_wav.exists() and mix_wav.stat().st_mtime > vt:
            why_v.append("el mix es más nuevo")
        elif ms["state"] == "stale":
            why_v.append("usa un mix desactualizado")
        if loop.exists() and loop.stat().st_mtime > vt:
            why_v.append("la animación es más nueva")
        elif not loop.exists() and cover and cover.stat().st_mtime > vt:
            why_v.append("la portada es más nueva")
        rows.append(("5 · Video", f"⚠️ **desactualizado**: {', '.join(why_v)} — vuelve a **Renderizar video**"
                     if why_v else f"✅ al día · {vt and ago(vt)}"))
    else:
        rows.append(("5 · Video", "⬜ falta"))
    pub = d / "publish_youtube.txt"
    chap = d / "chapters.txt"
    if pub.exists():
        old = chap.exists() and chap.stat().st_mtime > pub.stat().st_mtime
        rows.append(("6 · Publicar", "⚠️ los capítulos cambiaron — vuelve a **Generar textos**" if old else "✅ textos listos"))
    else:
        rows.append(("6 · Publicar", "⬜ falta generar los textos"))
    es = exportmod.export_state(pl)
    if es["state"] == "missing":
        rows.append(("7 · Pistas sueltas", "⬜ opcional: guarda cada canción en orden con su nombre"))
    elif es["state"] == "ok":
        rows.append(("7 · Pistas sueltas", f"✅ {n_(es['n'], 'pista', 'pistas')} en pistas/ · {ago(es['when'])}"))
    else:
        rows.append(("7 · Pistas sueltas", f"⚠️ desactualizadas ({n_(len(es['reasons']), 'cambio', 'cambios')}) — "
                                           "vuelve a **Guardar pistas en orden**"))
    md = "| Paso | Estado |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows)
    if ms["state"] == "stale":
        rs = ms["reasons"]
        md += "\n\n**Qué cambió desde el último mix:**\n" + "\n".join(f"- {r}" for r in rs[:8])
        if len(rs) > 8:
            md += f"\n- … y {len(rs) - 8} más"
    return md


def pl_table(name):
    if not name:
        return ""
    pl = pl_load(name)
    changed = set(mixmod.mix_state(pl)["changed"])
    rows = ["| # | Canción | Estado | Toma en uso | Tono | Mix |", "|---|---|---|---|---|---|"]
    for i, st in enumerate(mixmod.status(pl), 1):
        hist = take_history(st["slug"])
        if st["chosen"]:
            state, take = "✅ masterizada", st["chosen"].name.replace("_master.wav", "")
            tone = tone_short(hist.get(take + ".wav"))
        elif st["takes"]:
            state, take = f"🎵 {len(st['takes'])} tomas, sin master", f"(usaría {st['takes'][0][:-4]})"
            tone = tone_short(hist.get(st["takes"][0]))
        else:
            state, take, tone = "⬜ sin generar", "", ""
        rows.append(f"| {i} | {st['title']} | {state} | {take} | {tone} | {'⚠️ cambió' if st['slug'] in changed else ''} |")
    rows.append(f"\n{TONE_LEGEND}")
    return "\n".join(rows)


def pl_status(name):
    """[checklist, tabla, portada, loop, video]"""
    if not name:
        return "", "", None, None, None
    d = pl_dir(name)
    cover = find_cover(d)
    loop = d / "cover_loop.mp4"
    vid = d / "video.mp4"
    return (pl_checklist(name), pl_table(name), str(cover) if cover else None,
            str(loop) if loop.exists() else None, str(vid) if vid.exists() else None)


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


def pl_track_choices(name, keep=None):
    if not name:
        return gr.update(choices=[], value=None)
    sts = mixmod.status(pl_load(name))
    choices = [(f"{i}. {'✅' if st['chosen'] else ('🎵' if st['takes'] else '⬜')} {st['title']}", st["slug"])
               for i, st in enumerate(sts, 1)]
    return gr.update(choices=choices, value=keep if keep in {s["slug"] for s in sts} else None)


def pl_light(name, rg_slug):
    """Refresco de la pestaña Playlist tras cambios en otra parte: [checklist, tabla, pista] + reproductores."""
    if not name:
        return ("", "", K) + (K,) * MAX_TRACKS
    return (pl_checklist(name), pl_table(name), pl_track_choices(name, rg_slug), *pl_players(name))


def rg_preview(slug, take):
    if not slug or not take:
        return gr.update(value=None, visible=False)
    return gr.update(value=str(OUT / slug / "songs" / take), label=f"Escuchar {take}", visible=True)


def rg_load(slug):
    if not slug:
        return "", "", gr.update(choices=[], value=None)
    sp = OUT / slug / "song.json"
    song = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    st = mixmod.track_status(slug)
    used = st["chosen"].name.replace("_master.wav", ".wav") if st["chosen"] else None
    info = (f"Toma en uso: **{used[:-4] if used else 'ninguna'}** · {song.get('bpm', '?')} BPM · "
            f"{song.get('key', '?')} · {len(st['takes'])} tomas generadas (la más nueva arriba)")
    return song.get("caption", ""), info, gr.update(choices=rg_take_choices(slug), value=used)


def rg_take_choices(slug):
    """[(etiqueta, archivo)] de las tomas, la más nueva primero, con la que está en uso marcada."""
    st = mixmod.track_status(slug)
    used = st["chosen"].name.replace("_master.wav", ".wav") if st["chosen"] else None
    hist = take_history(slug)
    return [(t[:-4] + (f"  · {hist[t]['created'][5:]}" if hist.get(t, {}).get("created") else "")
             + (f"  · 🎯 {tone_short(hist.get(t))}" if tone_short(hist.get(t)) else "")
             + ("  ✅ en uso" if t == used else ""), t) for t in st["takes"]]


def rg_generate(slug, caption, seed, no_crackle):
    """Salidas: [panel, registro, toma, audio1, audio2]"""
    if not slug:
        warn("Elige la pista")
        yield "", K, K, K, K
        return
    sp = OUT / slug / "song.json"
    if not sp.exists():
        warn("Esa pista no tiene song.json")
        yield "", K, K, K, K
        return
    song = json.loads(sp.read_text(encoding="utf-8"))
    if caption.strip() and caption.strip() != song.get("caption"):
        song["caption"] = caption.strip()
        sp.write_text(json.dumps(song, indent=2, ensure_ascii=False), encoding="utf-8")  # se guarda el cambio
        info("El caption nuevo se guardó en su song.json")
    if no_crackle:
        song["caption"] = music.strip_crackle(song["caption"])
    avg, queue = ace_typical()
    job = Job(f"Regenerando «{song.get('title', slug)}»", typical=avg, eta_from_frac=False)
    job.progress(None, f"hay {n_(queue, 'trabajo', 'trabajos')} antes en ACE-Step" if queue else "enviando a ACE-Step")
    try:
        files = yield from live(job, lambda: music.generate(song, sp.parent, int(seed) if str(seed).strip() else None,
                                                            log=job.log, progress=job.progress), painter(job, 3))
    except Exception as e:  # noqa: BLE001
        yield fail(job, "No se pudo regenerar", e), job.text(), K, K, K
        return
    names = [f.name for f in files]
    audios = [gr.update(value=str(f), label=f.name, visible=True) for f in files[:2]]
    audios += [gr.update(value=None, visible=False)] * (2 - len(audios))
    ok(f"{n_(len(files), 'toma nueva', 'tomas nuevas')} de «{song.get('title', slug)}»")
    tone = tones_summary(slug, files)
    yield (done_card(job, n_(len(files), "toma nueva", "tomas nuevas"), (tone + "\n" if tone else "") +
                     "Escúchalas, elige una en «Toma a usar» y dale «Masterizar y usar esta toma»."),
           job.text(), gr.update(choices=rg_take_choices(slug), value=names[0] if names else None), *audios)


def rg_master(slug, take):
    """Salidas: [panel]"""
    if not slug or not take:
        warn("Elige la toma a usar")
        yield ""
        return
    wav = OUT / slug / "songs" / take
    job = Job(f"Masterizando {wav.stem}")
    if not master.stems_cached(wav):
        job.progress(0, "primera vez de esta toma: separa batería y bajo (~30–60 s en GPU)")
    try:
        rep = yield from live(job, lambda: master.master(wav, log=lambda *_: None, progress=job.progress),
                              painter(job, 0, with_log=False))
    except Exception as e:  # noqa: BLE001
        yield fail(job, "No se pudo masterizar", e)
        return
    a = rep["after"]
    ok(f"{wav.stem} es ahora la toma de esta pista")
    yield done_card(job, f"{wav.stem} es ahora la toma de esta pista",
                    f"{a['lufs']} LUFS, pico {a['true_peak_dbtp']} dBTP. Vuelve a «Armar mix» para que entre en la playlist.")


def pl_transition(name):
    if not name:
        return K, K
    pl = pl_load(name)
    gap = float(pl.get("gap", 1))
    if gap > 0:
        return "Pausa de silencio", gap
    return "Crossfade", float(pl.get("crossfade", 4))


def pl_songs_job(name, regen_all, auto_master):
    """Genera las canciones que faltan, o tomas NUEVAS de todas (las anteriores se conservan).
    Salidas: [panel, registro]"""
    if not name:
        warn("Elige la playlist")
        yield "", K
        return
    sts = mixmod.status(pl_load(name))
    todo = sts if regen_all else [st for st in sts if not st["takes"]]
    if not todo:
        info("Todas las canciones ya tienen tomas")
        yield card("ok", "Nada que generar", "Todas las canciones ya tienen tomas. Revísalas y masterízalas en 🎚️ Master."), K
        return
    n = len(todo)
    job = Job(f"{'Regenerando' if regen_all else 'Generando'} {n_(n, 'canción', 'canciones')}")
    hi = 0.8 if auto_master else 1.0
    failed = []

    def work():
        for i, st in enumerate(todo):
            job.items = (n, i, time.time())
            pre = f"{i + 1}/{n} · {st['title']} · "
            job.progress(i / n, pre + "enviando a ACE-Step")
            sp = OUT / st["slug"] / "song.json"
            job.log(f"\n{i + 1}/{n} · {st['title']}")
            if not sp.exists():
                job.log("  ⚠ falta song.json, la salto")
                failed.append(st["title"])
                continue
            song = json.loads(sp.read_text(encoding="utf-8"))
            try:
                files = music.generate(song, sp.parent, None if regen_all else song.get("seed"), log=job.log,
                                       progress=job.sub(i, n, pre, 0.0, hi))
                if auto_master and files:
                    job.log(f"  🎚 masterizando {files[0].name}…")
                    master.master(files[0], log=lambda *_: None, progress=job.sub(i, n, pre + "master: ", hi, 1.0))
                    job.log("  ✅ queda como toma en uso")
            except Exception as e:  # noqa: BLE001
                if "no responde" in str(e) or e.__class__.__name__ == "ConnectionError":
                    raise  # ACE-Step se cayó: no tiene caso seguir con las demás
                job.log(f"  ⚠ {why(e)}")
                failed.append(st["title"])
        job.items = None
        job.progress(1.0, "listo")

    try:
        yield from live(job, work, painter(job, 0))
    except Exception as e:  # noqa: BLE001
        yield fail(job, "Se detuvo la generación", e,
                   "Las canciones que ya terminaron se conservan; vuelve a darle y sigue con las que falten."), job.text()
        return
    n_ok = n - len(failed)
    nxt = ("Revisa en 🎚️ Master (◀ ▶) y masteriza la toma que más te guste de cada una; luego «Armar mix»."
           if not auto_master else "Cada canción quedó con su primera toma nueva masterizada; ya puedes «Armar mix».")
    if failed:
        warn(f"{len(failed)} no se pudieron: {', '.join(failed)}", title=f"{n_ok} de {n_(n, 'canción lista', 'canciones listas')}")
        yield done_card(job, f"{n_ok} de {n_(n, 'canción', 'canciones')} con tomas nuevas", f"Fallaron: {', '.join(failed)} (detalle en el registro). {nxt}",
                        kind="warn"), job.text()
    else:
        ok(f"{n_(n, 'canción', 'canciones')} con tomas nuevas")
        yield done_card(job, f"{n_(n, 'canción', 'canciones')} con tomas nuevas", nxt), job.text()


def pl_gen_missing(name):
    yield from pl_songs_job(name, False, False)


def pl_regen_all(name, auto_master):
    yield from pl_songs_job(name, True, auto_master)


def pl_mix(name, mode, secs):
    """Salidas: [panel, audio, capítulos, info]"""
    if not name:
        warn("Elige la playlist")
        yield "", K, K, K
        return
    pf = PLAYLISTS / f"{name}.json"
    pl = pl_load(name)
    if mode == "Pausa de silencio":
        pl["gap"] = float(secs)
    else:
        pl["gap"] = 0
        pl["crossfade"] = float(secs)
    pf.write_text(json.dumps(pl, indent=2, ensure_ascii=False), encoding="utf-8")  # se recuerda para la próxima
    missing = [st["title"] for st in mixmod.status(pl) if not st["takes"]]
    if missing:
        msg = f"Faltan {len(missing)} canciones por generar: {', '.join(missing)}. Usa «Generar canciones faltantes»."
        warn(msg, title="Todavía no se puede armar el mix")
        yield card("err", "Todavía no se puede armar el mix", msg), K, K, K
        return
    job = Job(f"Armando el mix de «{pl['title']}»")
    try:
        info = yield from live(job, lambda: mixmod.build(pf, log=job.log, progress=job.progress),
                               painter(job, 3, with_log=False))
    except Exception as e:  # noqa: BLE001
        yield fail(job, "No se pudo armar el mix", e), K, K, K
        return
    d = Path(info["dir"])
    chapters = (d / "chapters.txt").read_text(encoding="utf-8")
    nxt = ("Tu video ya no coincide con este mix: vuelve a «Renderizar video»." if (d / "video.mp4").exists()
           else "Siguiente: portada → animación → «Renderizar video».")
    ok(f"{info['duration']} · {info['after']['lufs']} LUFS", title="Mix listo")
    yield (done_card(job, f"Mix listo · {info['duration']}", nxt), str(d / "mix.mp3"), chapters,
           f"**{info['duration']}** · {info['after']['lufs']} LUFS · pico {info['after']['true_peak_dbtp']} dBTP")


def pl_cover(name, img):
    if not name:
        raise gr.Error("Elige la playlist")
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
    nxt = " Vuelve a animarla." if (dst.parent / "cover_loop.mp4").exists() else ""
    ok(f"Ajustada a {w}×{h} y guardada como cover.png.{nxt}", title="Portada guardada")
    return str(dst)


def pl_gpt_prompt(name):
    if not name:
        raise gr.Error("Elige la playlist")
    try:
        text, _ = chatgpt_prompt.for_target(PLAYLISTS / f"{name}.json")
    except Exception as e:
        raise gr.Error(str(e))
    info("Cópialo con el botón 📋 y pégalo en ChatGPT con tus referencias de Mishka", title="Prompt listo")
    return text


def song_gpt_prompt(project):
    if not project:
        raise gr.Error("Elige un proyecto")
    try:
        text, _ = chatgpt_prompt.for_target(OUT / project)
    except Exception as e:
        raise gr.Error(str(e))
    info("Cópialo con el botón 📋 y pégalo en ChatGPT con tus referencias de Mishka", title="Prompt listo")
    return gr.update(value=text, visible=True)


def publish_texts(target):
    try:
        d = seo.build(target)
    except Exception as e:
        raise gr.Error(f"No pude armar los textos: {e}")
    ok("Cópialos con 📋. También quedaron en publish_youtube.txt y publish_instagram.txt", title="Textos listos")
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


def pl_comfy(name, n, with_title):
    """Salidas: [panel, galería, registro]"""
    if not name:
        warn("Elige la playlist")
        yield "", K, K
        return
    d = pl_dir(name)
    pl = pl_load(name)
    cj = d / "cover.json"
    if not cj.exists():
        cj.write_text(json.dumps({"title": pl.get("cover_title", pl["title"]), "scene": pl.get("scene", ""),
                                  "format": pl.get("format", "horizontal")}, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    job = Job(f"Generando {n_(int(n), 'portada', 'portadas')} en ComfyUI")
    try:
        yield from live(job, lambda: cover_local.generate(d, int(n), with_title=with_title, log=job.log),
                        lambda: (job_card(job), K, job.text()))
    except Exception as e:  # noqa: BLE001
        yield fail(job, "ComfyUI no pudo generar", e), K, job.text()
        return
    allv = sorted(d.glob("cover_v*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    ok("Haz clic en una para usarla como portada", title="Variantes listas")
    yield done_card(job, n_(int(n), "variante lista", "variantes listas"), "Haz clic en la que te guste para usarla como portada."), \
        [str(p) for p in allv], job.text()


def pl_pick(name, evt: gr.SelectData):
    path = evt.value["image"]["path"] if isinstance(evt.value, dict) else evt.value
    return pl_cover(name, path)


def pl_anim(name, spec_text):
    """Salidas: [panel, zonas, loop]"""
    import animate
    if not name:
        warn("Elige la playlist")
        yield "", K, K
        return
    d = pl_dir(name)
    img = d / "cover.png"
    if not img.exists():
        msg = "Primero guarda la portada (paso 3)."
        warn(msg)
        yield card("err", "Falta la portada", msg), K, K
        return
    sp = d / "anim_spec.json"
    if spec_text.strip():
        try:
            spec = json.loads(re.search(r"\{.*\}", spec_text, re.S).group(0))
        except Exception as e:  # noqa: BLE001
            warn(f"No pude leer el plan de animación: {e}")
            yield card("err", "Plan de animación inválido", str(e)), K, K
            return
        sp.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    job = Job("Animando la portada")

    def work():
        if not sp.exists():
            job.progress(0.02, "Claude Code está mirando la portada para planear qué animar (1–2 min)")
            try:
                animate.plan(img)
            except Exception as e:
                raise RuntimeError(f"No pude planear la animación: {why(e)}. Pega el plan que te dé Claude en el chat.")
        spec = json.loads(sp.read_text(encoding="utf-8"))
        job.progress(0.15, "dibujando las zonas animadas para revisar")
        animate.check(img, spec)
        job.progress(0.25, "renderizando el loop de 10 s")
        animate.render(img, spec, progress=master.span(job.progress, 0.25, 1.0))

    try:
        yield from live(job, work, painter(job, 2, with_log=False))
    except Exception as e:  # noqa: BLE001
        yield fail(job, "No se pudo animar", e), K, K
        return
    ok("Revisa las zonas: si algo quedó mal, corrige anim_spec.json y vuelve a animar", title="Loop listo")
    yield (done_card(job, "Loop de 10 s listo", "Revisa las zonas (cuadros de colores). Si ya tenías video, "
                     "vuelve a «Renderizar video»."), str(d / "anim_check.png"), str(d / "cover_loop.mp4"))


def pl_video(name):
    """Salidas: [panel, video, resultado]"""
    if not name:
        warn("Elige la playlist")
        yield "", K, K
        return
    d = pl_dir(name)
    ms = mixmod.mix_state(pl_load(name))
    job = Job("Renderizando el video")
    try:
        out = yield from live(job, lambda: videomod.render(d, log=job.log, progress=job.progress),
                              lambda: (job_card(job), K, job.text()))
    except Exception as e:  # noqa: BLE001
        yield fail(job, "No se pudo renderizar", e), K, job.text()
        return
    size = out.stat().st_size / 1e9
    if ms["state"] == "stale":
        warn("El mix estaba desactualizado: arma el mix y vuelve a renderizar", title="Video listo, pero…")
        yield (done_card(job, "Video listo, pero con un mix desactualizado",
                         "Cambiaron tomas después de armar el mix. Dale «Armar mix» y vuelve a renderizar.", kind="warn"),
               str(out), job.text())
        return
    ok(f"video.mp4 · {size:.2f} GB", title="Video listo")
    yield done_card(job, f"Video listo · {size:.2f} GB", f"Está en {out}. Siguiente: «Generar textos para publicar»."), \
        str(out), job.text()


def pl_export(name):
    """Salidas: [panel, lista de pistas]"""
    if not name:
        warn("Elige la playlist")
        yield "", K
        return
    pl = pl_load(name)
    sin = [st["title"] for st in mixmod.status(pl) if not st["takes"]]
    if sin:
        msg = f"Faltan {len(sin)} canciones por generar: {', '.join(sin)}."
        warn(msg, title="Todavía no se pueden guardar las pistas")
        yield card("err", "Todavía no se pueden guardar las pistas", msg), K
        return
    job = Job(f"Guardando las pistas de «{pl['title']}»")
    try:
        res = yield from live(job, lambda: exportmod.export(PLAYLISTS / f"{name}.json", log=job.log,
                                                             progress=job.progress), painter(job, 1, with_log=False))
    except Exception as e:  # noqa: BLE001
        yield fail(job, "No se pudieron guardar las pistas", e), K
        return
    lst = "\n".join(f"{t['n']:02d}. **{t['title']}** · {mixmod.fmt_time(t['duration'])} · `{t['file']}.wav / .mp3`"
                    for t in res["tracks"])
    ok(f"{n_(res['n'], 'pista', 'pistas')} · {res['duration']}", title="Pistas guardadas")
    yield (done_card(job, f"{n_(res['n'], 'pista guardada', 'pistas guardadas')} en orden · {res['duration']}",
                     f"En {res['dir']}: WAV 24-bit + MP3 320k con nombre, número de pista y portada."),
           lst)


# ---------------- interfaz ----------------
THEME = gr.themes.Soft(primary_hue="amber", neutral_hue="stone")
CSS = """
.mk-card{border-radius:10px;padding:10px 14px;border:1px solid;font-size:14px;line-height:1.35}
.mk-card.run,.mk-card.wait{background:rgba(245,158,11,.08);border-color:rgba(245,158,11,.45)}
.mk-card.ok{background:rgba(34,197,94,.08);border-color:rgba(34,197,94,.45)}
.mk-card.warn{background:rgba(234,179,8,.10);border-color:rgba(234,179,8,.55)}
.mk-card.err{background:rgba(239,68,68,.08);border-color:rgba(239,68,68,.5)}
.mk-head{display:flex;align-items:center;gap:8px}
.mk-right{margin-left:auto;font-variant-numeric:tabular-nums;opacity:.75;font-size:13px;white-space:nowrap}
.mk-detail{margin-top:6px;opacity:.85;font-size:13px;white-space:pre-line}
.mk-bar{height:8px;border-radius:4px;background:rgba(120,113,108,.18);overflow:hidden;margin-top:8px}
.mk-bar>div{height:100%;background:#f59e0b;border-radius:4px}
.mk-bar.indet>div{width:30%;animation:mk-slide 1.4s ease-in-out infinite}
@keyframes mk-slide{0%{margin-left:-30%}100%{margin-left:100%}}
.mk-spin{width:14px;height:14px;border:2px solid rgba(245,158,11,.3);border-top-color:#f59e0b;border-radius:50%;
         animation:mk-rot .8s linear infinite;display:inline-block;flex:none}
@keyframes mk-rot{to{transform:rotate(360deg)}}
.mk-chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center;min-height:30px}
.mk-chip{padding:3px 10px;border-radius:999px;font-size:13px;border:1px solid rgba(120,113,108,.3);white-space:nowrap}
.mk-chip.ok{background:rgba(34,197,94,.10);border-color:rgba(34,197,94,.4)}
.mk-chip.warn{background:rgba(234,179,8,.12);border-color:rgba(234,179,8,.5)}
.mk-chip.bad{background:rgba(239,68,68,.10);border-color:rgba(239,68,68,.45)}
.mk-chip.run{background:rgba(245,158,11,.14);border-color:rgba(245,158,11,.55)}
.mk-chip.off{opacity:.7}
/* Gradio agrupa los avisos por tipo y les pone el nombre en inglés: se traduce aquí */
.toast-title{font-size:0!important;line-height:22.4px}
.toast-title::before{font-size:16px;font-weight:700}
.toast-title.success::before{content:"Listo"}
.toast-title.info::before{content:"Aviso"}
.toast-title.warning::before{content:"Ojo"}
.toast-title.error::before{content:"Error"}
"""


def busy(btn, text, fn, inputs, outputs, panel):
    """Clic → el botón se deshabilita con «⏳ text» y el panel dice «Empezando…»; al terminar vuelve a la normalidad.
    Devuelve el evento del trabajo (para encadenar refrescos con .then)."""
    label = btn.value
    run = btn.click(lambda: (gr.update(interactive=False, value=f"⏳ {text}"), WAIT), None, [btn, panel],
                    queue=False).then(fn, inputs, outputs, show_progress="hidden")
    run.then(lambda: (gr.update(interactive=True, value=label), services_html()), None, [btn, svc], queue=False)
    return run


def job_panel():
    return gr.HTML("", padding=False)


with gr.Blocks(title="Mishka LoFi Studio") as app:
    with gr.Row(equal_height=True):
        gr.Markdown("# 🐾 Mishka LoFi Studio")
        svc = gr.HTML(services_html, padding=False, scale=4)
        svc_btn = gr.Button("↻ Revisar", size="sm", scale=0, min_width=90)
    with gr.Tabs() as tabs:
        with gr.Tab("💡 Idea", id="idea"):
            idea = gr.Textbox(label="Idea", lines=2,
                              placeholder="ej. Mishka dormida junto a la ventana, primera nevada, domingo en la tarde")
            n_var = gr.Slider(1, 4, value=3, step=1, label="Propuestas")
            go_idea = gr.Button("Escribir prompts", variant="primary")
            idea_job = job_panel()
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
                proj_open = gr.Button("📂 Abrir carpeta", scale=0)
            title = gr.Textbox(label="Título")
            caption = gr.Textbox(label="Caption (prompt de estilo)", lines=4)
            cap_lint = gr.Markdown()
            lyrics = gr.Textbox(label="Lyrics / estructura", lines=4,
                                value="[Intro - piano]\n[Main Theme - piano and Rhodes]\n[Main Theme - piano]\n[Outro - piano, music fades out]")
            with gr.Row():
                bpm = gr.Number(label="BPM", value=70, precision=0)
                key = gr.Textbox(label="Tonalidad", value="F", placeholder="F, Am, C# minor…")
                ts = gr.Dropdown(["2", "3", "4", "6"], value="4", label="Compás")
                duration = gr.Slider(30, 600, value=210, step=5, label="Duración (s)")
                batch = gr.Slider(1, 4, value=2, step=1, label="Tomas")
                seed = gr.Textbox(label="Seed (vacío = aleatorio)")
            no_crackle = gr.Checkbox(label="Quitar crepitar / fuego del caption")
            go = gr.Button("Generar", variant="primary")
            song_job = job_panel()
            log = gr.Textbox(label="Registro", lines=3, autoscroll=True)
            outs = [gr.Audio(visible=False, type="filepath") for _ in range(4)]
            with gr.Row():
                song_gpt_btn = gr.Button("Prompt de portada para ChatGPT", scale=0)
                song_gpt = gr.Textbox(label="Prompt para ChatGPT", lines=6, buttons=["copy"], visible=False, scale=4)
            with gr.Accordion("📝 Textos para publicar (YouTube e Instagram)", open=False):
                song_pub_btn = gr.Button("Generar textos")
                song_pub = publish_box()

        with gr.Tab("🎚️ Master", id="master") as tab_master:
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
                m_meas = gr.Button("🎯 Medir tono", scale=0)
                m_del = gr.Button("🗑 Eliminar toma", variant="stop", scale=0)
            m_tone_md = gr.Markdown()
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
                rp_job = job_panel()
                rp_log = gr.Textbox(label="Registro", lines=3, autoscroll=True)
                with gr.Row():
                    rp_a1 = gr.Audio(visible=False, type="filepath")
                    rp_a2 = gr.Audio(visible=False, type="filepath")
            with gr.Row():
                drums = gr.Slider(-24, 0, value=master.DEFAULTS["drums_db"], step=1, label="Batería (dB)")
                bass = gr.Slider(-24, 0, value=master.DEFAULTS["bass_db"], step=1, label="Bajo (dB)")
                lufs = gr.Slider(-20, -9, value=master.DEFAULTS["lufs"], step=0.5, label="Loudness objetivo (LUFS)")
                ceil = gr.Slider(-3, -0.1, value=master.DEFAULTS["ceiling"], step=0.1, label="Techo de pico (dBTP)")
            run = gr.Button("Masterizar", variant="primary")
            m_job = job_panel()
            with gr.Row():
                a_before = gr.Audio(label="Antes", type="filepath")
                a_after = gr.Audio(label="Después", type="filepath")
            report = gr.Markdown()
            files = gr.Files(label="Archivos")

        with gr.Tab("📀 Playlist", id="playlist") as tab_pl:
            with gr.Row():
                pl_name = gr.Dropdown(playlist_names(), label="Playlist", scale=3)
                pl_reload = gr.Button("↻", scale=0)
                pl_open = gr.Button("📂 Abrir carpeta", scale=0)
            pl_check = gr.Markdown()
            with gr.Accordion("Canciones de la playlist", open=False):
                pl_table_md = gr.Markdown()
            gr.Markdown("**1 · Canciones** — genera las que falten. Luego, en 🎚️ Master, masteriza la toma que más te guste de cada una (si no, se usa la más reciente).")
            with gr.Row():
                pl_gen = gr.Button("Generar canciones faltantes")
                pl_regen = gr.Button("🔁 Regenerar toda la playlist")
            pl_regen_auto = gr.Checkbox(value=False, label="Al regenerar, masterizar la primera toma nueva y dejarla en uso "
                                        "(más rápido; si no, eliges tú en 🎚️ Master)")
            pl_job = job_panel()
            pl_log = gr.Textbox(label="Registro", lines=4, autoscroll=True)
            with gr.Accordion("🎧 Escuchar la playlist por canción", open=False):
                gr.Markdown("Cada canción con la toma que se usará en el mix. Para cambiar una, usa 🔁 Regenerar una pista o 🎚️ Master.")
                pl_players_out = [gr.Audio(type="filepath", visible=False) for _ in range(MAX_TRACKS)]
            with gr.Accordion("🔁 Regenerar una pista", open=False):
                with gr.Row():
                    rg_track = gr.Dropdown(label="Pista", info="Despliega el menú (▾) y elige la canción a regenerar", scale=3)
                    rg_seed = gr.Textbox(label="Seed (vacío = aleatorio)", scale=1)
                rg_info = gr.Markdown()
                rg_caption = gr.Textbox(label="Caption (puedes ajustarlo; el cambio se guarda en su song.json)", lines=3)
                rg_lint = gr.Markdown()
                rg_crackle = gr.Checkbox(label="Quitar crepitar / fuego del caption")
                rg_go = gr.Button("Regenerar tomas")
                rg_log = gr.Textbox(label="Registro", lines=2, autoscroll=True)
                with gr.Row():
                    rg_a1 = gr.Audio(visible=False, type="filepath")
                    rg_a2 = gr.Audio(visible=False, type="filepath")
                with gr.Row():
                    rg_take = gr.Dropdown(label="Toma a usar en la playlist", info="Al elegirla la puedes escuchar abajo", scale=3)
                    rg_use = gr.Button("Masterizar y usar esta toma", variant="primary", scale=1)
                rg_job = job_panel()
                rg_listen = gr.Audio(type="filepath", visible=False)
            gr.Markdown("**2 · Mix** — une los masters (pausa o crossfade entre canciones), fade in / fade out de 5 s al inicio y al final, normaliza todo y escribe los capítulos.")
            with gr.Row():
                pl_trans = gr.Radio(["Pausa de silencio", "Crossfade"], value="Pausa de silencio", label="Entre canciones")
                pl_trans_s = gr.Slider(0, 8, value=1, step=0.5, label="Segundos")
            pl_mix_btn = gr.Button("Armar mix", variant="primary")
            mix_job = job_panel()
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
                comfy_job = job_panel()
                pl_gallery = gr.Gallery(label="Variantes (clic = usar como portada)", columns=4, height=260)
                pl_comfy_log = gr.Textbox(label="Registro", lines=2)
            with gr.Row():
                pl_cover_in = gr.Image(label="Portada", type="filepath", sources=["upload", "clipboard"])
                pl_cover_save = gr.Button("Guardar portada", scale=0)
            gr.Markdown("**4 · Animación** — déjalo vacío y Claude Code planea solo qué animar mirando la portada, o pega un plan del chat.")
            pl_spec = gr.Textbox(label="anim_spec.json", lines=5)
            pl_anim_btn = gr.Button("Animar portada")
            anim_job = job_panel()
            with gr.Row():
                pl_zones = gr.Image(label="Zonas animadas", type="filepath")
                pl_loop = gr.Video(label="Loop de 10 s")
            gr.Markdown("**5 · Video final** — repite el loop durante todo el mix.")
            pl_vid_btn = gr.Button("Renderizar video", variant="primary")
            vid_job = job_panel()
            pl_vid_log = gr.Textbox(label="Resultado", lines=2)
            pl_video_out = gr.Video(label="Video")
            gr.Markdown("**6 · Publicar** — textos de YouTube (con capítulos) e Instagram, listos para copiar.")
            pl_pub_btn = gr.Button("Generar textos para publicar")
            pl_pub = publish_box()
            gr.Markdown("**7 · Pistas sueltas** — guarda cada canción masterizada en orden y con su nombre "
                        "(`01 - Título.wav` + `.mp3` con etiquetas y portada) en la carpeta `pistas/`, por si las necesitas después.")
            with gr.Row():
                pl_exp_btn = gr.Button("💾 Guardar pistas en orden", variant="primary")
                pl_exp_open = gr.Button("📂 Abrir carpeta de pistas", scale=0)
            exp_job = job_panel()
            pl_exp_list = gr.Markdown()

    # ---- barra de estado: se refresca sola (los trabajos en curso se ven desde cualquier pestaña)
    timer = gr.Timer(2)
    timer.tick(services_html, None, svc, show_progress="hidden")
    svc_btn.click(lambda: services_html(force=True), None, svc)

    PL_STATUS = [pl_check, pl_table_md, pl_cover_in, pl_loop, pl_video_out]
    PL_LIGHT = [pl_check, pl_table_md, rg_track] + pl_players_out

    # ---- 💡 Idea
    busy(go_idea, "Escribiendo…", make_briefs, [idea, n_var], [idea_job, briefs, pick, preview, use], idea_job)
    load_btn.click(load_pasted, pasted, [briefs, pick, preview, use])
    pick.change(show_brief, [pick, briefs], preview)
    use.click(use_brief, [pick, briefs, idea], [proj, tabs])
    tabs.select(lambda p: gr.update(choices=projects(), value=p), proj, proj)

    # ---- 🎵 Canción
    fields = [title, caption, lyrics, bpm, key, ts, duration, batch, seed]
    proj.change(load_song, proj, fields)
    reload_p.click(lambda: gr.update(choices=projects()), None, proj)
    proj_open.click(lambda p, t: open_folder(OUT / (p or slugify(t or ""))), [proj, title], None)
    for comp in (caption, lyrics, bpm, key, duration):
        comp.change(lint_md, [caption, lyrics, bpm, key, duration], cap_lint, show_progress="hidden")
    busy(go, "Generando…", gen_song, fields + [no_crackle], [song_job, log] + outs + [proj], song_job)
    song_gpt_btn.click(song_gpt_prompt, proj, song_gpt)
    song_pub_btn.click(song_publish, proj, song_pub)

    # ---- 🎚️ Master
    m_pl.change(m_pick_playlist, m_pl, m_song)
    app.load(m_pick_playlist, m_pl, m_song)
    tab_master.select(m_refresh_list, [m_pl, m_song], m_song)
    m_song.change(m_pick_song, m_song, [take, m_info, a_before])
    m_song.change(rp_caption, m_song, rp_cap)
    take.change(m_preview, take, [a_before, rp_start, rp_end, rp_dur])
    take.change(m_tone, take, m_tone_md)
    m_meas.click(m_measure, m_song, None).success(lambda s_: m_pick_song(s_)[:2], m_song, [take, m_info]).then(
        m_tone, take, m_tone_md).then(pl_light, [pl_name, rg_track], PL_LIGHT)
    hide3 = lambda: (gr.update(visible=False), gr.update(visible=False), gr.update(visible=False))  # noqa: E731
    take.change(hide3, None, [m_del_msg, m_del_yes, m_del_no])
    m_del.click(m_delete_ask, take, [m_del_msg, m_del_yes, m_del_no])
    m_del.click(lambda: "", None, m_del_done)
    m_del_no.click(hide3, None, [m_del_msg, m_del_yes, m_del_no])
    m_del_yes.click(m_delete, take, m_del_done).success(hide3, None, [m_del_msg, m_del_yes, m_del_no]).then(
        m_refresh_list, [m_pl, m_song], m_song).then(
        m_pick_song, m_song, [take, m_info, a_before]).then(
        pl_light, [pl_name, rg_track], PL_LIGHT).then(rg_load, rg_track, [rg_caption, rg_info, rg_take])
    busy(rp_go, "Repintando…", rp_run, [m_song, take, rp_start, rp_end, rp_cap], [rp_job, rp_log, rp_a1, rp_a2],
         rp_job).then(lambda s_: m_pick_song(s_)[0], m_song, take)
    m_prev.click(lambda p, s_: m_step(p, s_, -1), [m_pl, m_song], m_song)
    m_next.click(lambda p, s_: m_step(p, s_, 1), [m_pl, m_song], m_song)
    reload_t.click(lambda p: gr.update(choices=[ALL_SONGS] + playlist_names(), value=p), m_pl, m_pl).then(
        m_refresh_list, [m_pl, m_song], m_song).then(m_pick_song, m_song, [take, m_info, a_before])
    busy(run, "Masterizando…", do_master, [take, drums, bass, lufs, ceil], [m_job, a_before, a_after, report, files],
         m_job).then(m_refresh_list, [m_pl, m_song], m_song).then(
        lambda s_: m_pick_song(s_)[:2], m_song, [take, m_info]).then(pl_light, [pl_name, rg_track], PL_LIGHT)

    # ---- 📀 Playlist
    for ev in (pl_name.change, app.load):
        ev(pl_status, pl_name, PL_STATUS)
        ev(pl_track_choices, pl_name, rg_track)
        ev(pl_players, pl_name, pl_players_out)
        ev(pl_transition, pl_name, [pl_trans, pl_trans_s])
    tab_pl.select(pl_light, [pl_name, rg_track], PL_LIGHT)
    pl_reload.click(lambda p: gr.update(choices=playlist_names(), value=p), pl_name, pl_name).then(
        pl_light, [pl_name, rg_track], PL_LIGHT)
    pl_open.click(lambda p: open_folder(pl_dir(p)) if p else warn("Elige la playlist"), pl_name, None)
    rg_track.change(rg_load, rg_track, [rg_caption, rg_info, rg_take])
    rg_caption.change(lambda c: lint_md(c), rg_caption, rg_lint, show_progress="hidden")
    rg_take.change(rg_preview, [rg_track, rg_take], rg_listen)
    busy(rg_go, "Generando…", rg_generate, [rg_track, rg_caption, rg_seed, rg_crackle],
         [rg_job, rg_log, rg_take, rg_a1, rg_a2], rg_job).then(pl_light, [pl_name, rg_track], PL_LIGHT)
    busy(rg_use, "Masterizando…", rg_master, [rg_track, rg_take], [rg_job], rg_job).then(
        pl_light, [pl_name, rg_track], PL_LIGHT).then(rg_load, rg_track, [rg_caption, rg_info, rg_take])
    busy(pl_gen, "Generando…", pl_gen_missing, pl_name, [pl_job, pl_log], pl_job).then(
        pl_light, [pl_name, rg_track], PL_LIGHT)
    busy(pl_regen, "Regenerando…", pl_regen_all, [pl_name, pl_regen_auto], [pl_job, pl_log], pl_job).then(
        pl_light, [pl_name, rg_track], PL_LIGHT)
    busy(pl_mix_btn, "Armando mix…", pl_mix, [pl_name, pl_trans, pl_trans_s], [mix_job, pl_audio, pl_chapters, pl_info],
         mix_job).then(pl_light, [pl_name, rg_track], PL_LIGHT)
    pl_cover_save.click(pl_cover, [pl_name, pl_cover_in], pl_cover_in).success(pl_checklist, pl_name, pl_check)
    pl_gpt_btn.click(pl_gpt_prompt, pl_name, pl_gpt)
    busy(pl_comfy_btn, "Generando…", pl_comfy, [pl_name, pl_n, pl_title_on], [comfy_job, pl_gallery, pl_comfy_log],
         comfy_job)
    pl_gallery.select(pl_pick, pl_name, pl_cover_in).success(pl_checklist, pl_name, pl_check)
    busy(pl_anim_btn, "Animando…", pl_anim, [pl_name, pl_spec], [anim_job, pl_zones, pl_loop], anim_job).then(
        pl_checklist, pl_name, pl_check)
    busy(pl_vid_btn, "Renderizando…", pl_video, pl_name, [vid_job, pl_video_out, pl_vid_log], vid_job).then(
        pl_checklist, pl_name, pl_check)
    pl_pub_btn.click(pl_publish, pl_name, pl_pub).success(pl_checklist, pl_name, pl_check)
    busy(pl_exp_btn, "Guardando…", pl_export, pl_name, [exp_job, pl_exp_list], exp_job).then(
        pl_light, [pl_name, rg_track], PL_LIGHT)
    pl_exp_open.click(lambda p: open_folder(exportmod.folder(pl_load(p))) if p else warn("Elige la playlist"),
                      pl_name, None)

if __name__ == "__main__":
    app.launch(server_port=7870, inbrowser=True, allowed_paths=[str(OUT)], theme=THEME, css=CSS)
