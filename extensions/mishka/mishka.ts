import { lstat, mkdir, open, readFile, readdir, realpath, rmdir, unlink } from "node:fs/promises";
import { homedir } from "node:os";
import { join, sep } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const slugPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const usage = "/mishka brief <idea> | status [slug] | generate <slug>";

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

async function saveBrief(out: string, idea: string, text: string) {
  const data = object(JSON.parse(text.trim().replace(/^```(?:json)?\s*\n([\s\S]*?)\n```$/i, "$1")));
  const title = string(data.title);
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  if (!slugPattern.test(slug) || slug.length > 120) throw new Error("Title does not produce a usable release slug");
  if (!Array.isArray(data.tags) || !data.tags.length) throw new Error("Expected tags in the brief");
  const fields = object(data.song);
  const song = {
    caption: string(fields.caption), lyrics: string(fields.lyrics),
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
  pi.registerCommand("mishka", {
    description: "Draft or generate a Mishka LoFi release",
    handler: async (args, ctx) => {
      try {
        const [action, ...words] = args.trim().split(/\s+/);
        const value = args.trim().slice(action.length).trim();
        if (!(["brief", "status", "generate"].includes(action)) ||
            (action !== "status" && !value) ||
            (action !== "brief" && value && (words.length !== 1 || !slugPattern.test(value)))) {
          throw new Error(usage);
        }
        const root = await realpath(ctx.cwd);
        const guidePath = join(root, "config", "brief_instructions.md");
        if (!(await stat(guidePath))?.isFile() || !(await stat(join(root, "steps", "music.py")))?.isFile()) {
          throw new Error("Start Pi in a Mishka Studio working copy");
        }
        const cache = join(homedir(), ".opensrc");
        if (root.startsWith(cache + sep)) throw new Error("Use a working copy, not the OpenSrc snapshot");
        const out = join(root, "out");
        const hasOutput = await directory(out);

        if (action === "brief") {
          if (!ctx.model) throw new Error("Select a model in Pi first");
          const response = await ctx.modelRegistry.complete(ctx.model, {
            systemPrompt: await readFile(guidePath, "utf8"),
            messages: [{ role: "user", timestamp: Date.now(), content: `Draft one release for this idea: ${value}
Return only one JSON object using this exact shape and value types. Adapt the example to the idea and studio guide; encode 4/4 as the integer 4, not a string.
{"title":"Soft Rain","description":"Full YouTube description","tags":["lofi"],"scene":"Cover scene in English","song":{"caption":"instrumental lofi","lyrics":"[Intro]","bpm":66,"key":"F","time_signature":4,"duration":210,"batch_size":2}}` }],
          }, { maxTokens: 4000, signal: AbortSignal.timeout(120_000) });
          if (response.stopReason !== "stop") throw new Error(`Model did not complete the brief (${response.stopReason}); check the active provider or retry`);
          const text = response.content.map(part => part.type === "text" ? part.text : "").join("");
          ctx.ui.notify(`Release files saved in ${await saveBrief(out, value, text)}`, "info");
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
    },
  });
}
