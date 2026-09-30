import test from "node:test";
import assert from "node:assert/strict";
import { actualWorkContour } from "../src/writer.ts";

test("the actual-work contour is a single finished segment (#56)", () => {
  // same figures as tests/test_core.py::test_actual_work_contour_is_a_single_finished_segment
  const blob = actualWorkContour(0.5, 9600 * 100.0 * 0.5, 9600);
  const d = new DataView(blob.buffer);
  assert.equal(blob.length, 56);
  assert.deepEqual([d.getUint16(0, true), d.getUint16(2, true), d.getUint32(4, true)], [1, 24, 36]);
  assert.equal(d.getFloat64(8, true), 5000.0);
  assert.equal(d.getFloat64(16, true), 480000.0);
  assert.equal(d.getUint32(24, true), 9600 * 8);
  assert.equal(d.getUint32(32, true), 0); // block starts at the start
  assert.equal(d.getFloat64(36, true), 480000.0); // cumulative work
  assert.equal(d.getFloat64(44, true), 5000.0); // units
  assert.equal(d.getUint32(52, true), 9600 * 8);
});
