// @ts-check
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { candles, describe as caption, marks, same, stamp, ticks, traces, zoomed } from "../../src/tape/lab/widgets/price.js";

const t0 = Date.UTC(2024, 0, 2, 14, 30);
/** @type {import("../../src/tape/lab/types/api").PriceWindow} */
const w = {
  count: 1000,
  first: 0,
  last: 999,
  factor: 10,
  index: [0, 10, 20],
  time: [t0, t0 + 50 * 60_000, t0 + 100 * 60_000],
  open: [1, 2, 3],
  high: [2, 3, 4],
  low: [0.5, 1.5, 2.5],
  close: [2, 3, 4],
  indicators: { sma_50: [null, 2, 3], ema_20: [1, 2, null] },
  fills: [
    { index: 4, time: t0, side: "buy", price: 1.5, quantity: 100, tag: "signal" },
    { index: 15, time: t0, side: "sell", price: 2.5, quantity: 100, tag: "signal" },
  ],
  fills_in_range: 2,
  fill_limit: 4000,
};

describe("price", () => {
  test("stamp formats UTC wall time", () => {
    assert.equal(stamp(t0, true), "02 Jan 2024 14:30");
    assert.equal(stamp(t0, false), "02 Jan 2024");
  });

  test("ticks sit on bucket positions and show clock time for short intraday spans", () => {
    const found = ticks(w, true);
    assert.deepEqual(found.tickvals, [0, 10, 20]);
    assert.equal(found.ticktext[2], "02 Jan 2024 16:10");
    assert.deepEqual(ticks({ ...w, index: [], time: [] }, true), { tickvals: [], ticktext: [] });
  });

  test("candles keep global positions and label aggregated buckets", () => {
    const c = /** @type {Record<string, any>} */ (candles({ meta: "candles", type: "candlestick" }, w, true));
    assert.deepEqual(c["x"], [0, 10, 20]);
    assert.equal(c["text"][0], "02 Jan 2024 14:30 · 10 bars");
  });

  test("marks split fills by side", () => {
    const buys = /** @type {Record<string, any>} */ (marks({ meta: "buy" }, w, "buy"));
    assert.deepEqual(buys["x"], [4]);
    assert.deepEqual(buys["y"], [1.5]);
  });

  test("traces put indicators after the candles in a fixed order", () => {
    const templates = [
      { meta: "candles" },
      { meta: "buy" },
      { meta: "sell" },
      { meta: "indicator", line: { color: "a" } },
      { meta: "indicator", line: { color: "b" } },
    ];
    const out = /** @type {Record<string, any>[]} */ (traces(templates, w, true));
    assert.deepEqual(out.map((t) => t["meta"] === "indicator" ? t["name"] : t["meta"]), ["candles", "ema_20", "sma_50", "buy", "sell"]);
    assert.equal(out[1]?.["line"]?.color, "a");
    const named = /** @type {Record<string, any>[]} */ (traces(templates, w, true, { sma_50: "Sma 50" }));
    assert.deepEqual(named.filter((t) => t["meta"] === "indicator").map((t) => t["name"]), ["ema_20", "Sma 50"]);
  });

  test("the caption says what each candle is and how to get more detail", () => {
    assert.match(caption(w), /full run, 1,000 bars · each candle aggregates 10 bars/);
    assert.match(caption({ ...w, first: 100, last: 199, factor: 1 }), /100 of 1,000 bars · every bar shown · double-click/);
    assert.match(caption({ ...w, fills: [], fills_in_range: 9000 }), /9,000 fills in view, zoom in to see them/);
  });

  test("zoom events become bar ranges or a reset", () => {
    assert.deepEqual(zoomed({ "xaxis.range[0]": 10.4, "xaxis.range[1]": 99.2 }), [10, 100]);
    assert.equal(zoomed({ "xaxis.autorange": true }), "full");
    assert.equal(zoomed({ dragmode: "pan" }), null);
    assert.deepEqual(zoomed({ "xaxis.range": [-0.5, 447484.5] }), [0, 447485]);
    assert.ok(same([99, 201], [100, 200]));
    assert.ok(!same([50, 200], [100, 200]));
  });
});
