/**
 * The reader, checked against the Python implementation (#70).
 *
 * A reader returns a model rather than bytes, so parity compares the models —
 * read the same files with both and assert they describe the same project.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync, existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { readProject, MppReadError } from "../src/reader.ts";
import { MppWriter } from "../src/writer.ts";
import { Storage, writeCfb } from "../src/cfb.ts";

const repo = new URL("../..", import.meta.url).pathname.replace(/\/$/, "");
const python = `${repo}/.venv/bin/python`;
const template = `${repo}/templates/template.mpp`;
const runnable = existsSync(python) && existsSync(template);
const D = (y: number, m: number, d: number, h = 0, min = 0) => new Date(Date.UTC(y, m - 1, d, h, min));

/** The same JSON shape parity_reader.py prints. */
function asJson(bytes: Uint8Array): unknown {
  const p = readProject(bytes);
  // Python's isoformat() has no milliseconds
  const iso = (d: Date | null | undefined) => (d ? d.toISOString().replace(".000Z", "") : null);
  return {
    title: p.title,
    start: iso(p.start),
    tasks: p.tasks.map((t) => ({
      uid: t.uid, name: t.name, start: iso(t.start), finish: iso(t.finish),
      durationDays: t.durationDays, outlineLevel: t.outlineLevel, parentUid: t.parentUid,
      durationUnits: t.durationUnits, estimated: t.estimated, notes: t.notes,
      wbs: t.wbs ?? null, constraint: t.constraint ?? null, constraintDate: iso(t.constraintDate),
      percentComplete: t.percentComplete, priority: t.priority, manual: t.manual,
    })),
    relations: p.relations!.map((r) => ({ predUid: r.predUid, succUid: r.succUid, type: r.type, lagDays: r.lagDays })),
    resources: p.resources!.map((r) => ({ uid: r.uid, name: r.name, initials: r.initials, email: r.email, maxUnits: r.maxUnits })),
    assignments: p.assignments!.map((a) => ({ taskUid: a.taskUid, resourceUid: a.resourceUid, units: a.units })),
  };
}

test("both readers describe the same project", { skip: !runnable }, () => {
  const dir = mkdtempSync(join(tmpdir(), "mppwriter-reader-"));
  const generated = join(dir, "python.mpp");
  execFileSync(python, [`${repo}/js/test/parity_writer.py`, repo, template, generated], { cwd: repo });

  for (const file of [template, generated]) {
    const want = JSON.parse(
      execFileSync(python, [`${repo}/js/test/parity_reader.py`, repo, file], { cwd: repo }).toString(),
    );
    assert.deepEqual(asJson(new Uint8Array(readFileSync(file))), want, `models differ for ${file}`);
  }
});

test("a file this library wrote reads back as the project that went in", { skip: !runnable }, () => {
  const project = {
    title: "Round trip",
    start: D(2027, 3, 1, 8),
    tasks: [
      { uid: 1, name: "Phase", start: D(2027, 3, 1, 8), finish: D(2027, 3, 3, 17), outlineLevel: 1 },
      { uid: 2, name: "Design", start: D(2027, 3, 1, 8), finish: D(2027, 3, 2, 17), durationDays: 2,
        outlineLevel: 2, parentUid: 1, notes: "Pencil first,\nCAD later {ok}", wbs: "1.1",
        percentComplete: 50, priority: 800 },
      { uid: 3, name: "Ship", start: D(2027, 3, 3, 8), finish: D(2027, 3, 3, 8), durationDays: 0,
        outlineLevel: 2, parentUid: 1, manual: true },
    ],
    relations: [{ predUid: 2, succUid: 3, type: "SS" as const, lagDays: 1 }],
    resources: [{ uid: 1, name: "Kevin McAleer", initials: "KM", email: "k@example.com" }],
    assignments: [{ taskUid: 2, resourceUid: 1, units: 0.5 }],
  };
  const bytes = new MppWriter(new Uint8Array(readFileSync(template)), { onWarning: () => {} }).build(project);
  const back = readProject(bytes);

  assert.equal(back.title, "Round trip");
  const byUid = new Map(back.tasks.map((t) => [t.uid, t]));
  assert.deepEqual([...byUid.keys()].sort(), [1, 2, 3]);
  const design = byUid.get(2)!;
  assert.equal(design.name, "Design");
  assert.equal(design.durationDays, 2);
  assert.equal(design.notes, "Pencil first,\nCAD later {ok}"); // through the RTF envelope
  assert.equal(design.wbs, "1.1");
  assert.equal(design.percentComplete, 50);
  assert.equal(design.priority, 800);
  assert.equal(byUid.get(3)!.manual, true);
  assert.deepEqual(back.relations!.map((r) => [r.predUid, r.succUid, r.type, r.lagDays]), [[2, 3, "SS", 1]]);
  assert.deepEqual(back.resources!.map((r) => [r.uid, r.name, r.initials]), [[1, "Kevin McAleer", "KM"]]);
  assert.deepEqual(back.assignments!.map((a) => [a.taskUid, a.resourceUid, a.units]), [[2, 1, 0.5]]);
});

test("a compound file that is not a project is rejected", () => {
  const root = new Storage();
  root.set("something", new TextEncoder().encode("not a project"));
  assert.throws(() => readProject(writeCfb(root)), MppReadError);
});
