// @ts-check
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { megabytes } from "../../src/tape/lab/widgets/ledger.js";
import { decode, fill, median, stacked } from "../../src/tape/lab/widgets/paths.js";

const meta = { dates: ["2024-01-02", "2024-01-03", "2024-01-04"], series: ["a", "b", "c"] };
const values = Float32Array.from([1, 2, 3, 10, NaN, 30, 5, 6, 7]).buffer;
const data = decode(meta, values);

/** @param {ArrayLike<number>} xs */
const list = (xs) => Array.from(xs);

describe("paths", () => {
  test("decode slices column-major series and parses dates as UTC", () => {
    assert.equal(data.n, 3);
    assert.deepEqual(list(data.x), [Date.UTC(2024, 0, 2), Date.UTC(2024, 0, 3), Date.UTC(2024, 0, 4)]);
    assert.deepEqual(list(data.series("a") ?? []), [1, 2, 3]);
    assert.deepEqual(list(data.series("b") ?? []), [10, NaN, 30]);
    assert.equal(data.series("missing"), null);
  });

  test("stacked joins runs with a NaN break and skips unknown runs", () => {
    const { x, y } = stacked(data, ["a", "missing", "c"]);
    assert.equal(x.length, 8);
    assert.deepEqual(list(y), [1, 2, 3, NaN, 5, 6, 7, NaN]);
    assert.ok(Number.isNaN(x[3]));
  });

  test("median ignores gaps per date", () => {
    assert.deepEqual(list(median(data, ["a", "b", "c"])), [5, 4, 7]);
    assert.deepEqual(list(median(data, ["a", "b"])), [5.5, 2, 16.5]);
  });

  test("median of no runs is NaN", () => {
    assert.ok(list(median(data, [])).every(Number.isNaN));
  });

  test("fill routes each trace role", () => {
    const best = /** @type {{ y: Float32Array }} */ (fill({ meta: "best:c", name: "Best" }, data, ["a"]));
    assert.deepEqual(list(best.y), [5, 6, 7]);
    const named = /** @type {{ y: Float32Array }} */ (fill({ meta: "series", name: "b" }, data, []));
    assert.deepEqual(list(named.y), [10, NaN, 30]);
    const gone = /** @type {{ y: Float32Array }} */ (fill({ meta: "series", name: "zz" }, data, []));
    assert.ok(list(gone.y).every(Number.isNaN));
  });
});

test("megabytes renders one decimal", () => {
  assert.equal(megabytes(2_345_678), "2.3");
  assert.equal(megabytes(null), "0.0");
  assert.equal(megabytes(1_250_000_000), "1,250.0");
});
