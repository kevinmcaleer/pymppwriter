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
