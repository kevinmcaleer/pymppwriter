import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import {
  parseProps, buildProps, propsTypes, parseFieldMap, parseFixedMetaAuto, buildFixedMeta,
  splitFixedData, metaBit, setMetaBit, parseVarMeta, readVar, buildVarBlocks,
  buildCalendarData, decodeTimestamp, encodeTimestamp, encodeUnicode, decodeUnicode,
  encodeCp1252, CAL_DAY_DEFAULT, CAL_DAY_WORKING, CAL_DAY_NONWORKING, EPOCH_MS,
  PROPS_TASK_FIELD_MAP,
} from "../src/blocks.ts";
import { readCfb } from "../src/cfb.ts";

const TEMPLATE = new URL("../../templates/template.mpp", import.meta.url).pathname;
const dv = (b: Uint8Array) => new DataView(b.buffer, b.byteOffset, b.byteLength);

test("props round trip and patch the size header", () => {
  propsTypes.set(1, 4);
  propsTypes.set(2, 0);
  const values = new Map([
    [1, Uint8Array.from([1, 0, 0, 0])],
    [2, new Uint8Array([...encodeUnicode("hi"), 0, 0])],
  ]);
  const data = buildProps(new Uint8Array(16), values, [1, 2]);
  assert.equal(dv(data).getUint32(0, true), data.length - 4);
  assert.equal(dv(data).getUint32(4, true), data.length - 4);

  const back = parseProps(data);
  assert.deepEqual(back.order, [1, 2]);
  assert.deepEqual(back.values.get(1), Uint8Array.from([1, 0, 0, 0]));
  assert.equal(propsTypes.get(1), 4);
  assert.deepEqual(buildProps(back.header, back.values, back.order), data);
});

test("var blocks carry the field-class high word", () => {
  const { meta, data } = buildVarBlocks(new Uint8Array(24), [
    { uid: 1, type: 14, payload: encodeUnicode("x") },
    { uid: 0, type: 14, payload: encodeUnicode("y") },
  ]);
  const mv = dv(meta);
  assert.equal(mv.getUint32(24, true), 0); // sorted: uid 0 comes first
  assert.equal(mv.getUint16(32, true), 14);
  assert.equal(mv.getUint16(34, true), 0x0b40);
  assert.equal(mv.getUint32(20, true), data.length);
  const { table } = parseVarMeta(meta);
  assert.equal(decodeUnicode(readVar(data, table.get(1)!.get(14)!)), "x");
});

test("timestamps round trip, and NA decodes to null", () => {
  const d = new Date(Date.UTC(2026, 8, 7, 8, 0));
  assert.equal(decodeTimestamp(encodeTimestamp(d), 0)!.getTime(), d.getTime());
  assert.equal(decodeTimestamp(encodeTimestamp(null), 0), null);
  assert.deepEqual(encodeTimestamp(null), Uint8Array.from([255, 255, 255, 255]));
});

test("meta bitmaps set and read across both blocks", () => {
  const meta = new Uint8Array(47);
  const meta2 = new Uint8Array(92);
  for (const idx of [0, 10, 311, 312, 400]) {
    assert.equal(metaBit(meta, meta2, idx), 0);
    setMetaBit(meta, meta2, idx, true);
    assert.equal(metaBit(meta, meta2, idx), 1);
  }
  assert.ok(meta[8]! & 0x01); // bit 0 -> FixedMeta byte 8
  assert.ok(meta[46]! & 0x80); // bit 311 -> the last FixedMeta byte
  assert.ok(meta2[8]! & 0x01); // bit 312 -> the first Fixed2Meta bitmap byte
  setMetaBit(meta, meta2, 312, false);
  assert.equal(metaBit(meta, meta2, 312), 0);
  assert.equal(metaBit(meta, meta2, 311), 1); // its neighbour in the other block is untouched
});

test("build_fixed_meta patches the count and data length", () => {
  const hdr = new Uint8Array(16);
  const hv = dv(hdr);
  hv.setUint32(0, 0xfadfadba, true);
  const out = buildFixedMeta(hdr, [new Uint8Array(47), new Uint8Array(47), new Uint8Array(47)], 618);
  assert.equal(dv(out).getUint32(8, true), 3);
  assert.equal(dv(out).getUint32(12, true), 618);
});

test("calendar blob matches the shape Project writes", () => {
  const days: Array<[number, Array<[number, number]>]> = Array.from({ length: 7 }, () => [CAL_DAY_DEFAULT, []]);
  days[4] = [CAL_DAY_WORKING, [[480, 720], [780, 1020]]];
  days[0] = [CAL_DAY_NONWORKING, []];
  const day = (d: number) => new Date(EPOCH_MS + d * 86400000);
  const blob = buildCalendarData(days, [
    [new Date(Date.UTC(2026, 8, 21)), new Date(Date.UTC(2026, 8, 21)), "Hol"],
    [new Date(Date.UTC(2026, 9, 1)), new Date(Date.UTC(2026, 9, 2)), "Golf"],
  ]);
  // "Hol\0" = 8 bytes (aligned, no pad); "Golf\0" = 10 bytes (+2 pad) + 4 closing
  assert.equal(blob.length, 420 + 4 + 92 + 8 + 92 + 10 + 2 + 4);
  const bv = dv(blob);
  assert.equal(bv.getUint16(0, true), 0); // explicit non-working
  assert.equal(bv.getUint16(4 * 60, true), 0); // working days are wire type 0
  assert.equal(bv.getUint16(4 * 60 + 2, true), 2); // two ranges
  assert.equal(bv.getUint32(4 * 60 + 4, true), 4800); // total tenths
  assert.equal(bv.getUint32(4 * 60 + 40, true), 2400); // cumulative durations
  assert.equal(bv.getUint32(4 * 60 + 44, true), 4800);
  assert.equal(bv.getUint16(60, true), 1); // default day
  assert.equal(bv.getUint32(420, true), 2); // exception count
  const d1 = Math.round((Date.UTC(2026, 8, 21) - EPOCH_MS) / 86400000);
  assert.equal(bv.getUint16(424, true), d1);
  assert.equal(bv.getUint32(424 + 88, true), 8); // "Hol\0" in UTF-16
  assert.equal(decodeUnicode(blob.subarray(424 + 92, 424 + 100)), "Hol");
});

test("cp1252 covers the range that is not Latin-1", () => {
  assert.deepEqual(encodeCp1252("A~"), Uint8Array.from([0x41, 0x7e]));
  assert.deepEqual(encodeCp1252("£"), Uint8Array.from([0xa3]));
  assert.deepEqual(encodeCp1252("€"), Uint8Array.from([0x80])); // the cp1252-only slot
  assert.deepEqual(encodeCp1252("中"), Uint8Array.from([0x3f])); // unmappable -> '?'
});

test("parses the field map and fixed records of a real project file", { skip: !existsSync(TEMPLATE) }, () => {
  const tree = readCfb(new Uint8Array(readFileSync(TEMPLATE)));
  const { values } = parseProps(tree.get("   114/Props")!);
  const fm = parseFieldMap(values.get(PROPS_TASK_FIELD_MAP)!);
  assert.ok(fm.length > 100, `expected a full task field map, got ${fm.length}`);
  const uid = fm.find((f) => f.fieldId === 86 && f.inFixed); // UNIQUE_ID
  assert.ok(uid && uid.block === 0, "UNIQUE_ID should live in FixedData");

  const meta = parseFixedMetaAuto(tree.get("   114/TBkndTask/FixedMeta")!, 47);
  const recs = splitFixedData(tree.get("   114/TBkndTask/FixedData")!, meta.items);
  const real = recs.filter((r) => r.length > 100);
  assert.ok(real.length >= 3, "template should hold its three tasks");
  const names = parseVarMeta(tree.get("   114/TBkndTask/VarMeta")!);
  const varData = tree.get("   114/TBkndTask/Var2Data")!;
  const first = real[0]!;
  const uidValue = dv(first).getUint32(uid!.offset, true);
  assert.equal(decodeUnicode(readVar(varData, names.table.get(uidValue)!.get(14)!)).length > 0, true);
});
