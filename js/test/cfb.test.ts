import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { Storage, readCfb, writeCfb, compareNames, MINI_CUTOFF } from "../src/cfb.ts";

const TEMPLATE = new URL("../../templates/template.mpp", import.meta.url).pathname;

test("round trips small and large streams", () => {
  const root = new Storage();
  root.set("small", new TextEncoder().encode("hello"));
  const large = new Uint8Array(256 * 40).map((_, i) => i % 256);
  root.set("dir/large", large);
  root.set("dir/empty", new Uint8Array(0));
  root.storage("dir/emptydir");

  const back = readCfb(writeCfb(root));
  assert.deepEqual(back.get("small"), new TextEncoder().encode("hello"));
  assert.deepEqual(back.get("dir/large"), large);
  assert.equal(back.get("dir/empty")!.length, 0);
  assert.ok(back.storage("dir").children.get("emptydir") instanceof Storage);
});

test("engages DIFAT chains past 109 FAT sectors", () => {
  // ~9 MB, the same case the Python suite covers
  const unit = new TextEncoder().encode("0123456789abcdef".repeat(64));
  const big = new Uint8Array(unit.length * 9000);
  for (let i = 0; i < 9000; i++) big.set(unit, i * unit.length);
  const root = new Storage();
  root.set("big", big);
  root.set("dir/small", new TextEncoder().encode("mini stream data"));

  const back = readCfb(writeCfb(root));
  assert.equal(back.get("big")!.length, big.length);
  assert.deepEqual(back.get("big"), big);
  assert.deepEqual(back.get("dir/small"), new TextEncoder().encode("mini stream data"));
});

test("names order by length then upper case, as [MS-CFB] requires", () => {
  assert.ok(compareNames("b", "AA") < 0);
  assert.equal(compareNames("abc", "ABC"), 0);
  assert.ok(compareNames("   114", "   214") < 0);
});

test("streams either side of the mini-stream cutoff survive", () => {
  const root = new Storage();
  root.set("just-under", new Uint8Array(MINI_CUTOFF - 1).fill(7));
  root.set("exactly", new Uint8Array(MINI_CUTOFF).fill(8));
  root.set("just-over", new Uint8Array(MINI_CUTOFF + 1).fill(9));
  const back = readCfb(writeCfb(root));
  assert.equal(back.get("just-under")!.length, MINI_CUTOFF - 1);
  assert.equal(back.get("exactly")!.length, MINI_CUTOFF);
  assert.equal(back.get("just-over")!.length, MINI_CUTOFF + 1);
  assert.equal(back.get("just-over")![MINI_CUTOFF], 9);
});

test("reads a file Microsoft Project wrote, and rewraps it losslessly", { skip: !existsSync(TEMPLATE) }, () => {
  const original = new Uint8Array(readFileSync(TEMPLATE));
  const tree = readCfb(original);
  const paths = tree.paths();
  assert.ok(paths.length > 50, `expected a full project tree, got ${paths.length} streams`);
  assert.ok(paths.includes("   114/TBkndTask/FixedData"));
  assert.ok(tree.get("   114/Props")!.length > 0);

  // every stream must survive our own container round trip byte for byte
  const back = readCfb(writeCfb(tree));
  assert.deepEqual(back.paths().sort(), paths.sort());
  for (const p of paths) assert.deepEqual(back.get(p), tree.get(p), `stream changed: ${p}`);
});
