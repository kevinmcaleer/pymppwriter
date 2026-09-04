/**
 * The writer, checked against the Python implementation byte for byte (#70).
 *
 * The same project, the same template, the same injected GUIDs and clock — so
 * any difference is the two implementations disagreeing about the format.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync, existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { MppWriter } from "../src/writer.ts";
import { clearBaseline, setBaseline } from "../src/model.ts";
import type { Calendar, Project } from "../src/model.ts";

const repo = new URL("../..", import.meta.url).pathname.replace(/\/$/, "");
const python = `${repo}/.venv/bin/python`;
const template = `${repo}/templates/template.mpp`;
const runnable = existsSync(python) && existsSync(template);

const D = (y: number, m: number, d: number, h = 0, min = 0) => new Date(Date.UTC(y, m - 1, d, h, min));
const guid = (n: number) => new Uint8Array(16).fill(n);

const STD: Calendar = {
  week: { 2: [[480, 720]] }, // Wednesday half day
  exceptions: [{ start: D(2027, 3, 8), name: "Team offsite" }],
  guid: guid(30),
};
const NIGHTS: Calendar = {
  name: "Nights",
  week: { 0: [[1080, 1320]], 1: [[1080, 1320]], 2: [[1080, 1320]], 3: [[1080, 1320]], 4: null, 5: null, 6: null },
  guid: guid(31),
};

const project: Project = {
  title: "Parity build",
  start: D(2027, 3, 1, 8),
  tasks: [
    { uid: 1, name: "Design phase", start: D(2027, 3, 1, 8), finish: D(2027, 3, 5, 12), outlineLevel: 1, guid: guid(1) },
    { uid: 2, name: "Sketch the robot", start: D(2027, 3, 1, 8), finish: D(2027, 3, 2, 17), durationDays: 2,
      outlineLevel: 2, parentUid: 1, notes: "Pencil first,\nCAD later {ok}", wbs: "1.1",
      text: { 1: "design", 30: "T30" }, number: { 2: 42.5 }, date: { 1: D(2027, 12, 25, 8) },
      flag: { 3: true }, percentComplete: 50, priority: 800, guid: guid(2) },
    { uid: 3, name: "CAD model", start: D(2027, 3, 3, 8), finish: D(2027, 3, 5, 12), durationDays: 2,
      outlineLevel: 2, parentUid: 1, taskType: "fixed_duration", deadline: D(2027, 3, 5, 17),
      constraint: "SNET", constraintDate: D(2027, 3, 3, 8), guid: guid(3) },
    { uid: 4, name: "Design complete", start: D(2027, 3, 5, 12), finish: D(2027, 3, 5, 12), durationDays: 0,
      outlineLevel: 2, parentUid: 1, guid: guid(4) },
    { uid: 5, name: "Build phase", start: D(2027, 3, 9, 13), finish: D(2027, 3, 15, 22), outlineLevel: 1, guid: guid(5) },
    { uid: 6, name: "Print parts", start: D(2027, 3, 9, 13), finish: D(2027, 3, 12, 17), durationDays: 3,
      outlineLevel: 2, parentUid: 5, estimated: true, guid: guid(6) },
    { uid: 7, name: "Assemble", start: D(2027, 3, 15, 18), finish: D(2027, 3, 15, 22), durationDays: 0.5,
      outlineLevel: 2, parentUid: 5, calendar: "Nights", guid: guid(7) },
    { uid: 8, name: "Film the video", start: D(2027, 3, 16, 8), finish: D(2027, 3, 18, 12), durationDays: 2,
      outlineLevel: 1, manual: true, guid: guid(8) },
  ],
  relations: [
    { predUid: 2, succUid: 3 }, { predUid: 3, succUid: 4 }, { predUid: 4, succUid: 6, lagDays: 1 },
    { predUid: 6, succUid: 7 }, { predUid: 6, succUid: 8, type: "SS" },
  ],
  resources: [
    { uid: 1, name: "Kevin McAleer", initials: "KM", email: "kev@example.com", guid: guid(20) },
    { uid: 2, name: "Robot Arm", initials: "RA", maxUnits: 2, guid: guid(21) },
  ],
  assignments: [{ taskUid: 2, resourceUid: 1 }, { taskUid: 3, resourceUid: 1, units: 0.5 }, { taskUid: 6, resourceUid: 2 }],
  calendar: STD,
  calendars: [NIGHTS],
  defaultCalendar: "Standard",
  author: "Kevin McAleer",
  subject: "Robotics",
  keywords: "robot;video",
  comments: "Written twice",
  manager: "Kev",
  company: "Kev's Robots",
  category: "Build",
  statusDate: D(2027, 3, 8, 17),
  currencySymbol: "£",
  currencyCode: "GBP",
};

// baselines on tasks, resources and assignments: slot 0 and a numbered slot,
// plus a slot set then cleared so the cleared-entry shape is compared too
setBaseline(project);
setBaseline(project, 4);
setBaseline(project, 7);
clearBaseline(project, 7);

test("the writer matches the Python implementation byte for byte", { skip: !runnable }, () => {
  const dir = mkdtempSync(join(tmpdir(), "mppwriter-writer-"));
  const pyOut = join(dir, "python.mpp");
  execFileSync(python, [`${repo}/js/test/parity_writer.py`, repo, template, pyOut], { cwd: repo });

  let counter = 0;
  const writer = new MppWriter(new Uint8Array(readFileSync(template)), {
    newGuid: () => {
      counter += 1;
      const b = new Uint8Array(16);
      new DataView(b.buffer).setUint32(0, counter, true);
      return b;
    },
    now: () => D(2026, 9, 4, 12, 0),
    onWarning: () => {},
  });
  const ours = writer.build(project);
  const theirs = new Uint8Array(readFileSync(pyOut));

  assert.equal(ours.length, theirs.length, `sizes differ: ${ours.length} vs ${theirs.length}`);
  const firstDiff = ours.findIndex((b, i) => b !== theirs[i]);
  if (firstDiff !== -1) {
    const at = (b: Uint8Array) => Buffer.from(b.subarray(firstDiff, firstDiff + 16)).toString("hex");
    assert.fail(`writers differ at byte ${firstDiff}\n  ours   =${at(ours)}\n  python =${at(theirs)}`);
  }
});
