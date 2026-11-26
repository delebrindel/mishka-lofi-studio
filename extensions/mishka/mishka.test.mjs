import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import extension from "./mishka.ts";

const song = { caption: "instrumental lofi", lyrics: "[Intro]", bpm: 66, key: "F", time_signature: 4, duration: 210, batch_size: 2 };
const brief = { title: "Soft Rain", description: "Quiet afternoon. ", tags: ["lofi"], scene: "Rain outside.", song };

async function setup(t) {
  const cwd = await mkdtemp(join(tmpdir(), "mishka-test-"));
  t.after(() => rm(cwd, { recursive: true, force: true }));
  await mkdir(join(cwd, "config"));
  await mkdir(join(cwd, "steps"));
  await writeFile(join(cwd, "config", "brief_instructions.md"), "Studio guide");
  await writeFile(join(cwd, "steps", "music.py"), "");
  const model = { id: "active-model" };
  const notices = [], calls = [];
  let handler;
  let reply = JSON.stringify(brief);
  let exitCode = 0;
  extension({
    registerCommand(name, options) { assert.equal(name, "mishka"); handler = options.handler; },
    async exec(...args) { calls.push(args); return { code: exitCode, stderr: "ACE-Step unavailable" }; },
  });
  const ctx = {
    cwd, model, ui: { notify(...args) { notices.push(args); } },
    modelRegistry: {
      async complete(selected, context) {
        assert.equal(selected, model);
        assert.equal(context.systemPrompt, "Studio guide");
        assert.match(context.messages[0].content, /afternoon/);
        return { stopReason: "stop", content: [{ type: "text", text: reply }] };
      },
    },
  };
  return {
    cwd, notices, calls, run: args => handler(args, ctx),
    setReply: text => { reply = text; }, failExec: () => { exitCode = 1; },
  };
}

test("writes the legacy release contract via the active model, without overwriting", async t => {
  const f = await setup(t);
  f.setReply("```json\n" + JSON.stringify(brief) + "\n```");
  await f.run("brief a rainy afternoon");
  const path = join(f.cwd, "out", "soft-rain");
  assert.deepEqual(JSON.parse(await readFile(join(path, "song.json"), "utf8")), { title: brief.title, ...song, seed: null });
  assert.deepEqual(JSON.parse(await readFile(join(path, "brief.json"), "utf8")), { ...brief, idea: "a rainy afternoon" });
  assert.equal(await readFile(join(path, "desc.txt"), "utf8"), "Quiet afternoon.\n");
  f.setReply(JSON.stringify({ ...brief, description: "Changed" }));
  await f.run("brief another afternoon");
  assert.match(f.notices.at(-1)[0], /already exists/);
  assert.equal(await readFile(join(path, "desc.txt"), "utf8"), "Quiet afternoon.\n");
});

test("rejects malformed model data, traversal and symlinked output", async t => {
  const f = await setup(t);
  f.setReply(JSON.stringify({ ...brief, song: { ...song, batch_size: 999 } }));
  await f.run("brief rainy afternoon");
  assert.equal(f.notices.at(-1)[1], "error");
  await assert.rejects(readFile(join(f.cwd, "out", "soft-rain", "song.json")), { code: "ENOENT" });
  await f.run("generate ../elsewhere");
  assert.equal(f.calls.length, 0);
  await symlink(join(f.cwd, "steps"), join(f.cwd, "out"));
  await f.run("brief rainy afternoon");
  assert.match(f.notices.at(-1)[0], /Expected a real directory/);
});

test("reports local artifacts and invokes the existing generator without a shell", async t => {
  const f = await setup(t);
  await f.run("brief rainy afternoon");
  const path = join(f.cwd, "out", "soft-rain");
  await mkdir(join(path, "songs"));
  await writeFile(join(path, "songs", "take_1.wav"), "");
  await writeFile(join(path, "songs", "take_1_master.wav"), "");
  await f.run("status soft-rain");
  assert.match(f.notices.at(-1)[0], /1 takes, 1 masters; song\.json/);
  await f.run("generate soft-rain");
  assert.deepEqual(f.calls[0][1], [join(f.cwd, "steps", "music.py"), join(path, "song.json")]);
  f.failExec();
  await f.run("generate soft-rain");
  assert.deepEqual(f.notices.at(-1), ["ACE-Step unavailable", "error"]);
});
