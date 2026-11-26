import { lstat, mkdir, open, readFile, readdir, realpath, rmdir, unlink } from "node:fs/promises";
import { homedir } from "node:os";
import { join, sep } from "node:path";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

const slugPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const profiles = ["mishka", "mishka+voice", "rock", "rock+voice"];
const profileEntry = "mishka-profile";
const actions = ["profile", "brief", "status", "generate"];
const usage = "/skill:mishka-profile [mishka|mishka+voice|rock|rock+voice], /skill:mishka-brief <idea>, /skill:mishka-status [slug], /skill:mishka-generate <slug>";

async function stat(path: string) {
  try {
    return await lstat(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
}

async function directory(path: string) {
  const entry = await stat(path);
  if (entry && !entry.isDirectory()) throw new Error(`Expected a real directory: ${path}`);
  return entry;
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Expected a JSON object");
  return value as Record<string, unknown>;
}

function string(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) throw new Error("Expected non-empty text in the brief");
  return value;
}

function integer(value: unknown, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max) {
    throw new Error(`Expected an integer between ${min} and ${max} in the song`);
  }
  return value;
}

async function saveBrief(out: string, idea: string, text: string, profile: string) {
  const data = object(JSON.parse(text.trim().replace(/^```(?:json)?\s*\n([\s\S]*?)\n```$/i, "$1")));
  const title = string(data.title);
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  if (!slugPattern.test(slug) || slug.length > 120) throw new Error("Title does not produce a usable release slug");
  if (!Array.isArray(data.tags) || !data.tags.length) throw new Error("Expected tags in the brief");
  const fields = object(data.song);
  const lyrics = string(fields.lyrics);
  const vocalLanguage = profile.endsWith("+voice") ? string(fields.vocal_language) : undefined;
  if (vocalLanguage && (!/^[a-z]{2}$/.test(vocalLanguage) || !lyrics.replace(/\[[^\]]*\]/g, "").trim())) {
    throw new Error("Vocal briefs need a two-letter language code and sung lyrics");
  }
  const song = {
    caption: string(fields.caption), lyrics,
    ...(profile === "mishka" ? {} : { profile }),
    ...(vocalLanguage ? { vocal_language: vocalLanguage } : {}),
    bpm: integer(fields.bpm, 30, 300), key: string(fields.key),
    time_signature: integer(fields.time_signature, 2, 4),
    duration: integer(fields.duration, 10, 480), batch_size: integer(fields.batch_size, 1, 2),
  };
  const brief = { title, description: string(data.description), tags: data.tags.map(string), scene: string(data.scene), song, idea };
  const release = join(out, slug);
  if (!await directory(out)) await mkdir(out, { recursive: true });
  try {
    await mkdir(release);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "EEXIST") throw new Error(`Release already exists: ${release}`);
    throw error;
  }
  const created: string[] = [];
  const json = (value: object) => JSON.stringify(value, null, 2);
  try {
    for (const [name, content] of Object.entries({
      "brief.json": json(brief), "song.json": json({ title, ...song, seed: null }), "desc.txt": brief.description.trim() + "\n",
    })) {
      const path = join(release, name);
      const file = await open(path, "wx");
      created.push(path);
      try {
        await file.writeFile(content);
      } finally {
        await file.close();
      }
    }
  } catch (error) {
    await Promise.allSettled(created.map(path => unlink(path)));
    await rmdir(release).catch(() => {});
    throw error;
  }
  return release;
}

export default function (pi: ExtensionAPI) {
  async function run(action: string, value: string, ctx: ExtensionContext) {
    try {
      if (!actions.includes(action) ||
          ((action === "brief" || action === "generate") && !value) ||
          ((action === "status" || action === "generate") && value && !slugPattern.test(value))) {
        throw new Error(usage);
      }
      const root = await realpath(ctx.cwd);
      const guidePath = join(root, "config", "brief_instructions.md");
      if (!(await stat(guidePath))?.isFile() || !(await stat(join(root, "steps", "music.py")))?.isFile()) {
        throw new Error("Start Pi in a Mishka Studio working copy");
      }
      const cache = join(homedir(), ".opensrc");
      if (root.startsWith(cache + sep)) throw new Error("Use a working copy, not the OpenSrc snapshot");
      const selected = ctx.sessionManager.getBranch().findLast(entry => entry.type === "custom" && entry.customType === profileEntry);
      const profile = selected?.type === "custom" && typeof selected.data === "string" && profiles.includes(selected.data) ? selected.data : "mishka";
      if (action === "profile") {
        if (value && !profiles.includes(value)) throw new Error(`Unknown profile. Choose: ${profiles.join(", ")}`);
        if (value) pi.appendEntry(profileEntry, value);
        ctx.ui.notify(`Active Mishka profile: ${value || profile}`, "info");
        return;
      }
      const out = join(root, "out");
      const hasOutput = await directory(out);

      if (action === "brief") {
        if (!ctx.model) throw new Error("Select a model in Pi first");
        const voice = profile.endsWith("+voice");
        const style = profile.startsWith("rock")
          ? "Rock with a distinctive guitar riff, a driving rhythm section, and layered arrangements."
          : "Intimate, nostalgic cozy lo-fi with warm felt piano or Rhodes, brushed drums, round bass, subtle tape texture and restrained layers. Mishka is an ice-blue-eyed Spitz; preserve that visual identity in the cover scene.";
        const guide = profile === "mishka" ? await readFile(guidePath, "utf8") : `${style}\n${voice
          ? "Describe the instrumentation and vocal character in an English caption. Write actual sung lyrics with section tags. Use the requested language (or infer it from the idea), and leave space in the arrangement for expressive vocals."
          : "Keep it instrumental. Use the lyrics field for arrangement tags only."}`;
        const response = await ctx.modelRegistry.complete(ctx.model, {
          systemPrompt: guide,
          messages: [{ role: "user", timestamp: Date.now(), content: `Draft one release for this idea: ${value}
Return only one JSON object using this exact shape and value types. Adapt the example to the idea and studio guide; encode 4/4 as the integer 4, not a string.
{"title":"Soft Rain","description":"Full YouTube description","tags":["music"],"scene":"Cover scene in English","song":{"caption":"${voice ? "Expressive lead vocal over a layered arrangement" : "Layered instrumental arrangement"}","lyrics":"${voice ? "[Verse]\\nExample sung line" : "[Intro]"}","bpm":66,"key":"F","time_signature":4,"duration":210,"batch_size":2${voice ? ',"vocal_language":"en"' : ""}}}
${voice ? "Use a two-letter ISO 639-1 code for vocal_language." : ""}` }],
        }, { maxTokens: 4000, signal: AbortSignal.timeout(120_000) });
        if (response.stopReason !== "stop") throw new Error(`Model did not complete the brief (${response.stopReason}); check the active provider or retry`);
        const text = response.content.map(part => part.type === "text" ? part.text : "").join("");
        ctx.ui.notify(`Release files saved in ${await saveBrief(out, value, text, profile)}`, "info");
        return;
      }

      if (action === "status") {
        const slugs = value ? [value] : hasOutput ? (await readdir(out, { withFileTypes: true }))
          .filter(entry => entry.isDirectory() && slugPattern.test(entry.name)).map(entry => entry.name).sort() : [];
        const lines = [];
        for (const slug of slugs) {
          const folder = join(out, slug);
          if (!await directory(folder)) throw new Error(`Release not found: ${slug}`);
          const songs = join(folder, "songs");
          const wavs = await directory(songs) ? (await readdir(songs, { withFileTypes: true }))
            .filter(entry => entry.isFile() && entry.name.endsWith(".wav")).map(entry => entry.name) : [];
          const masters = wavs.filter(name => name.endsWith("_master.wav")).length;
          const files = await Promise.all(["song.json", "cover.png", "video.mp4"].map(async name => (await stat(join(folder, name)))?.isFile() ? name : ""));
          lines.push(`${slug}: ${wavs.length - masters} takes, ${masters} masters; ${files.filter(Boolean).join(", ") || "no release files"}`);
        }
        ctx.ui.notify(lines.join("\n") || "No releases yet", "info");
        return;
      }

      const folder = join(out, value);
      const song = join(folder, "song.json");
      if (!await directory(folder) || !(await stat(song))?.isFile()) throw new Error(`Missing song.json for ${value}`);
      await directory(join(folder, "songs"));
      const python = process.env.MISHKA_PYTHON || join(root, ".venv", "bin", "python");
      ctx.ui.notify(`Generating ${value} with local ACE-Step…`, "info");
      const result = await pi.exec(python, [join(root, "steps", "music.py"), song], { cwd: root, timeout: 1_920_000, maxBuffer: 64 * 1024 });
      if (result.code !== 0) throw new Error(result.stderr.trim().split("\n").at(-1) || `ACE-Step command exited with code ${result.code}`);
      ctx.ui.notify(`Takes saved in ${join(folder, "songs")}`, "info");
    } catch (error) {
      ctx.ui.notify(error instanceof Error ? error.message : String(error), "error");
    }
  }

  pi.on("input", async (event, ctx) => {
    if (event.source === "extension") return { action: "continue" };
    const match = /^\/skill:mishka-(profile|brief|status|generate)(?:\s+([\s\S]*))?$/.exec(event.text.trim());
    if (!match) return { action: "continue" };
    await run(match[1], match[2]?.trim() ?? "", ctx);
    return { action: "handled" };
  });

  pi.registerCommand("mishka", {
    description: "Legacy command for Mishka release workflows",
    handler: async (args, ctx) => {
      const [action] = args.trim().split(/\s+/);
      await run(action, args.trim().slice(action.length).trim(), ctx);
    },
  });
}
