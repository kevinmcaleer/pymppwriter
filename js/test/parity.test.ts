/**
 * Parity with the Python implementation (#70).
 *
 * Both are deterministic given the same input, so the same tree has to produce
 * the same bytes. This is what lets the port be trusted without opening
 * Microsoft Project for every change — the format lessons the Python side has
 * learned are enforced here automatically.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync, existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { readCfb, writeCfb } from "../src/cfb.ts";
import * as B from "../src/blocks.ts";

const hex = (b: Uint8Array) => Buffer.from(b).toString("hex");

const repo = new URL("../..", import.meta.url).pathname.replace(/\/$/, "");
const python = `${repo}/.venv/bin/python`;
const template = `${repo}/templates/template.mpp`;
const runnable = existsSync(python) && existsSync(template);

test("the container matches the Python writer byte for byte", { skip: !runnable }, () => {
  const dir = mkdtempSync(join(tmpdir(), "mppwriter-parity-"));
  const pyOut = join(dir, "python.mpp");
  execFileSync(python, [
    "-c",
    `from pymppwriter.cfb import load_cfb, write_cfb\n` +
      `open(${JSON.stringify(pyOut)}, "wb").write(write_cfb(load_cfb(${JSON.stringify(template)})))`,
  ], { cwd: repo });

  const ours = writeCfb(readCfb(new Uint8Array(readFileSync(template))));
  const theirs = new Uint8Array(readFileSync(pyOut));
  assert.equal(ours.length, theirs.length, "container sizes differ");
  const firstDiff = ours.findIndex((b, i) => b !== theirs[i]);
  assert.equal(firstDiff, -1, `containers differ at byte ${firstDiff}`);
});


test("the block layer matches the Python implementation", { skip: !runnable }, () => {
  const raw = execFileSync(python, [`${repo}/js/test/parity_fixtures.py`, repo, template], {
    cwd: repo,
    maxBuffer: 64 * 1024 * 1024,
  });
  const want = JSON.parse(raw.toString()) as Record<string, string | string[]>;

  const tree = readCfb(new Uint8Array(readFileSync(template)));
  const props = B.parseProps(tree.get("   114/Props")!);
  assert.equal(hex(B.buildProps(props.header, props.values, props.order)), want["props_rebuild"],
    "rebuilt Props differ");

  const patched = new Map(props.values);
  patched.set(B.PROPS_TITLE, new Uint8Array([...B.encodeUtf16le("Parity"), 0, 0, 0, 0]));
  assert.equal(hex(B.buildProps(props.header, patched, props.order)), want["props_patched"],
    "Props with a patched title differ");

  const varMeta = B.parseVarMeta(tree.get("   114/TBkndTask/VarMeta")!);
  const varData = tree.get("   114/TBkndTask/Var2Data")!;
  const values = varMeta.entries.map((e) => ({
    uid: e.uid, type: e.type, payload: B.readVar(varData, e.offset),
  }));
  values.push({ uid: 99, type: 15, payload: encodeRtfNotes("parity note") });
  const built = B.buildVarBlocks(varMeta.header, values);
  assert.equal(hex(built.meta), want["var_meta"], "VarMeta differs");
  assert.equal(hex(built.data), want["var_data"], "Var2Data differs");

  const fixed = B.parseFixedMetaAuto(tree.get("   114/TBkndTask/FixedMeta")!, 47);
  assert.equal(hex(B.buildFixedMeta(fixed.header, fixed.items, 4321)), want["fixed_meta"],
    "FixedMeta differs");

  const days: Array<[number, Array<[number, number]>]> =
    Array.from({ length: 7 }, () => [B.CAL_DAY_DEFAULT, []]);
  days[3] = [B.CAL_DAY_WORKING, [[480, 720], [780, 1020]]];
  days[0] = [B.CAL_DAY_NONWORKING, []];
  const cal = B.buildCalendarData(days, [
    [new Date(Date.UTC(2026, 8, 21)), new Date(Date.UTC(2026, 8, 21)), "Hol"],
    [new Date(Date.UTC(2026, 9, 1)), new Date(Date.UTC(2026, 9, 2)), "Golf"],
  ]);
  assert.equal(hex(cal), want["calendar"], "calendar blob differs");

  const stamps: Array<Date | null> = [
    new Date(Date.UTC(2026, 8, 7, 8, 0)), new Date(Date.UTC(1984, 0, 1)),
    new Date(Date.UTC(2027, 2, 15, 22, 0)), new Date(Date.UTC(2026, 11, 31, 23, 59)), null,
  ];
  assert.deepEqual(stamps.map((s) => hex(B.encodeTimestamp(s))), want["timestamps"],
    "timestamps differ");
  assert.deepEqual(["Design", "Café £", ""].map((s) => hex(B.encodeUnicode(s))), want["unicode"],
    "encoded strings differ");

  const si = B.updatePropertySetStrings(tree.get("\u0005SummaryInformation")!, new Map([
    [2, "Parity title"], [3, "Subject"], [4, "Kevin McAleer"], [5, "a;b"],
  ]));
  assert.equal(hex(si), want["summary_info"], "SummaryInformation differs");
});

/** The RTF envelope Project writes for notes (writer.py's encode_rtf_notes). */
function encodeRtfNotes(text: string): Uint8Array {
  let body = "";
  for (const ch of text) {
    if (ch === "\\" || ch === "{" || ch === "}") body += "\\" + ch;
    else if (ch === "\n") body += "\\par ";
    else if (ch.charCodeAt(0) > 127) body += `\\u${ch.charCodeAt(0)}?`;
    else body += ch;
  }
  const rtf =
    "{\\rtf1\\ansi\\ansicpg1252\\deff0\\nouicompat\\deflang1033" +
    "{\\fonttbl{\\f0\\fnil\\fcharset0 Segoe UI;}}\\viewkind4\\uc1 " +
    "\\pard\\f0\\fs20 " + body + "}";
  return Uint8Array.from(rtf, (c) => c.charCodeAt(0));
}
