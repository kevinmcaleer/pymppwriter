import test from "node:test";
import assert from "node:assert/strict";
import {
  workPattern, workingTenths, advanceWorking, nextWorkingMoment, previousWorkingMoment,
  weeklyOverlapMinutes, linkDrivenStart, encodeRtfNotes, decodeRtfNotes, validate,
  type Project, type Calendar,
} from "../src/model.ts";

const D = (y: number, m: number, d: number, h = 0, min = 0) => new Date(Date.UTC(y, m - 1, d, h, min));
const iso = (d: Date | null) => (d ? d.toISOString() : null);

test("working time on the Standard calendar", () => {
  assert.equal(workingTenths(D(2026, 9, 7, 8), D(2026, 9, 7, 17)), 4800); // one full day
  assert.equal(workingTenths(D(2026, 9, 7, 8), D(2026, 9, 7, 12)), 2400); // morning only
  assert.equal(workingTenths(D(2026, 9, 7, 8), D(2026, 9, 9, 17)), 14400); // Mon-Wed
  assert.equal(workingTenths(D(2026, 9, 4, 8), D(2026, 9, 7, 17)), 9600); // Fri + Mon
  assert.equal(workingTenths(D(2026, 9, 7, 8), D(2026, 9, 7, 8)), 0);
});

test("working time on a custom calendar", () => {
  const cal: Calendar = {
    week: { 2: [[480, 720]], 5: [[540, 780]] }, // Wednesday half day, Saturday working
    exceptions: [{ start: D(2026, 9, 8), name: "Hol" }],
  };
  const p = workPattern(cal);
  assert.equal(workingTenths(D(2026, 9, 9, 8), D(2026, 9, 9, 17), p), 2400); // Wed morning
  assert.equal(workingTenths(D(2026, 9, 12, 0), D(2026, 9, 12, 23, 59), p), 2400); // Saturday
  assert.equal(workingTenths(D(2026, 9, 8, 8), D(2026, 9, 8, 17), p), 0); // the holiday
  assert.equal(workingTenths(D(2026, 9, 7, 8), D(2026, 9, 11, 17), p), 4800 * 3 + 2400);
});

test("advancing, and the working moments either side of a boundary", () => {
  assert.equal(iso(advanceWorking(D(2026, 9, 7, 8), 2400)), iso(D(2026, 9, 7, 12)));
  assert.equal(iso(advanceWorking(D(2026, 9, 7, 8), 4800)), iso(D(2026, 9, 7, 17)));
  assert.equal(iso(advanceWorking(D(2026, 9, 7, 8), 7200)), iso(D(2026, 9, 8, 12)));
  assert.equal(iso(advanceWorking(D(2026, 9, 4, 13), 4800)), iso(D(2026, 9, 7, 12)));

  assert.equal(iso(nextWorkingMoment(D(2026, 9, 7, 12))), iso(D(2026, 9, 7, 13)));
  assert.equal(iso(nextWorkingMoment(D(2026, 9, 7, 17))), iso(D(2026, 9, 8, 8)));
  assert.equal(iso(nextWorkingMoment(D(2026, 9, 5, 9))), iso(D(2026, 9, 7, 8))); // Sat -> Mon

  assert.equal(iso(previousWorkingMoment(D(2026, 9, 9, 8))), iso(D(2026, 9, 8, 17)));
  assert.equal(iso(previousWorkingMoment(D(2026, 9, 7, 8))), iso(D(2026, 9, 4, 17)));
  assert.equal(iso(previousWorkingMoment(D(2026, 9, 7, 17))), iso(D(2026, 9, 7, 17)));
});

test("link-driven starts", () => {
  const pred = { start: D(2026, 9, 7, 8), finish: D(2026, 9, 8, 17) };
  assert.equal(iso(linkDrivenStart({ predUid: 1, succUid: 2 }, pred, 4800)), iso(D(2026, 9, 9, 8)));
  assert.equal(iso(linkDrivenStart({ predUid: 1, succUid: 2 }, pred, 0)), iso(D(2026, 9, 8, 17)));
  // a day of lag lands on Wednesday 17:00, which is not a start: Project rolls it on
  assert.equal(iso(linkDrivenStart({ predUid: 1, succUid: 2, lagDays: 1 }, pred, 4800)),
    iso(D(2026, 9, 10, 8)));
  assert.equal(iso(linkDrivenStart({ predUid: 1, succUid: 2, type: "SS" }, pred, 4800)),
    iso(D(2026, 9, 7, 8)));
  assert.equal(linkDrivenStart({ predUid: 1, succUid: 2, type: "FF" }, pred, 4800), null);
});

test("a night calendar shares no working time with the standard one", () => {
  const nights: Calendar = {
    name: "Nights",
    week: { 0: [[1080, 1320]], 1: [[1080, 1320]], 2: [[1080, 1320]], 3: [[1080, 1320]], 4: null, 5: null, 6: null },
  };
  assert.equal(weeklyOverlapMinutes(workPattern(nights), workPattern(null)), 0);
  assert.ok(weeklyOverlapMinutes(workPattern(null), workPattern(null)) > 0);
});

test("notes survive the RTF envelope", () => {
  const text = "a {b}\nc\\d é";
  const rtf = encodeRtfNotes(text);
  assert.ok(String.fromCharCode(...rtf).startsWith("{\\rtf1\\ansi"));
  assert.ok(String.fromCharCode(...rtf).includes("a \\{b\\}\\par c\\\\d \\u233?"));
  assert.equal(decodeRtfNotes(rtf), text);
});

test("the validator rejects what Project would silently repair", () => {
  const task = (uid: number, extra: Record<string, unknown> = {}) => ({
    uid, name: "t", start: D(2026, 1, 5, 8), finish: D(2026, 1, 5, 17), ...extra,
  });
  const project = (tasks: unknown[], relations: unknown[] = []): Project =>
    ({ title: "p", start: D(2026, 1, 5), tasks, relations }) as Project;

  validate(project([task(1), task(2, { outlineLevel: 2, parentUid: 1 })], [{ predUid: 1, succUid: 2 }]));
  assert.throws(() => validate(project([task(1), task(1)])), /duplicate/);
  assert.throws(() => validate(project([task(1), task(2, { outlineLevel: 3, parentUid: 1 })])), /does not follow/);
  assert.throws(() => validate(project([task(1), task(2), task(3, { outlineLevel: 2, parentUid: 1 })])), /does not match/);
  assert.throws(() => validate(project([task(1)], [{ predUid: 1, succUid: 9 }])), /unknown task/);
  assert.throws(() => validate(project([task(1)], [{ predUid: 1, succUid: 1 }])), /itself/);
  assert.throws(() => validate(project([task(1), task(2), task(3)],
    [{ predUid: 1, succUid: 2 }, { predUid: 2, succUid: 3 }, { predUid: 3, succUid: 1 }])), /cycle/);
});

test("the validator handles chains deeper than a recursive walk would", () => {
  const n = 5000;
  const tasks = Array.from({ length: n }, (_, i) =>
    ({ uid: i + 1, name: `t${i}`, start: D(2026, 1, 5, 8), finish: D(2026, 1, 5, 17) }));
  const relations = Array.from({ length: n - 1 }, (_, i) => ({ predUid: i + 1, succUid: i + 2 }));
  const p = { title: "p", start: D(2026, 1, 5), tasks, relations } as Project;
  validate(p); // no stack overflow
  assert.throws(() => validate({ ...p, relations: [...relations, { predUid: n, succUid: 1 }] }), /cycle/);
});
