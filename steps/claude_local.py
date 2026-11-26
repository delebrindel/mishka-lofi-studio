"""Claude sin API key: llama a Claude Code instalado en tu compu (`claude -p`), que usa tu suscripción
de Claude (Pro / Max). Docs: https://code.claude.com/docs/en/headless

Requisitos (una sola vez):
    1. Instalar Claude Code  →  https://code.claude.com  (en Windows, el instalador nativo o `npm i -g @anthropic-ai/claude-code`)
    2. Abrir una terminal, correr `claude` y entrar con tu cuenta de Claude (la misma de claude.ai).

Ojo: si existe ANTHROPIC_API_KEY en el entorno, Claude Code la usaría en vez de tu suscripción;
este módulo la quita antes de llamar, para que siempre se use el plan.
"""
import json, os, shutil, subprocess, tempfile
from pathlib import Path

CLAUDE_BIN = os.getenv("MISHKA_CLAUDE_BIN", "")          # ruta opcional al ejecutable `claude`
CLAUDE_MODEL = os.getenv("MISHKA_CLAUDE_CODE_MODEL", "")  # opcional: "opus", "sonnet"… (vacío = el de tu Claude Code)


def binary() -> str | None:
    if CLAUDE_BIN and Path(CLAUDE_BIN).exists():
        return CLAUDE_BIN
    found = shutil.which("claude")
    if found:
        return found
    # instalador nativo de Windows / macOS / Linux
    for p in (Path.home() / ".local" / "bin" / "claude.exe", Path.home() / ".local" / "bin" / "claude",
              Path.home() / ".claude" / "local" / "claude"):
        if p.exists():
            return str(p)
    return None


def available() -> bool:
    return binary() is not None


def ask(prompt: str, cwd: Path | None = None, allow_read=False, timeout=600) -> str:
    """Manda `prompt` por stdin a `claude -p` y devuelve el texto de la respuesta."""
    exe = binary()
    if not exe:
        raise RuntimeError("No encuentro Claude Code (`claude`). Instálalo desde https://code.claude.com, "
                           "corre `claude` una vez para iniciar sesión con tu cuenta de Claude, y reinicia Studio.")
    cmd = [exe, "-p", "--output-format", "json"]
    if allow_read:                       # p. ej. para que vea la portada
        cmd += ["--allowedTools", "Read"]
    if CLAUDE_MODEL:
        cmd += ["--model", CLAUDE_MODEL]
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    workdir = str(cwd) if cwd else tempfile.mkdtemp(prefix="mishka_claude_")
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=workdir, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Claude Code tardó demasiado en responder")
    out = (r.stdout or "").strip()
    try:
        data = json.loads(out.splitlines()[-1] if out else "{}")
    except json.JSONDecodeError:
        data = {"result": out, "is_error": r.returncode != 0}
    text = data.get("result") or ""
    if r.returncode != 0 or data.get("is_error"):
        hint = ""
        low = (text + (r.stderr or "")).lower()
        if "login" in low or "auth" in low or "credential" in low:
            hint = " → abre una terminal, corre `claude` y entra con tu cuenta de Claude."
        elif "limit" in low:
            hint = " → llegaste al límite de uso de tu plan; espera a que se renueve."
        raise RuntimeError(f"Claude Code no pudo responder: {(text or r.stderr or 'sin detalle')[:400]}{hint}")
    return text
