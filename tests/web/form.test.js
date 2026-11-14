// @ts-check
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { clamp, combinations, count, fieldsOf, gridOf, label, nonEmpty, resolve, span, timeframeOf, visible, windows, yearsBefore } from "../../src/tape/lab/form/form.js";

/** @typedef {import("../../src/tape/lab/types/api").ConfigSchema} ConfigSchema */
/** @typedef {import("../../src/tape/lab/types/api").Ordered} Ordered */
/** @typedef {import("../../src/tape/lab/types/api").JsonInput} Json */

/** @type {Ordered[]} */
const STRICT = [{ lower: "fast", upper: "slow", strict: true }];
/** @type {Ordered[]} */
const LOOSE = [{ lower: "fast", upper: "slow", strict: false }];

describe("text", () => {
  test("label humanises snake case", () => {
    assert.equal(label("stop_loss_pct"), "Stop loss pct");
    assert.equal(label(5), "5");
  });

  test("count pluralises and groups", () => {
    assert.equal(count(1, "run"), "1 run");
    assert.equal(count(0, "run"), "0 runs");
    assert.equal(count(1234, "ticker"), "1,234 tickers");
  });
});

describe("dates", () => {
  test("yearsBefore keeps the calendar day", () => {
    assert.equal(yearsBefore("2025-03-15", 3), "2022-03-15");
  });

  test("yearsBefore folds a leap day to the 28th", () => {
    assert.equal(yearsBefore("2024-02-29", 1), "2023-02-28");
    assert.equal(yearsBefore("2024-02-29", 4), "2020-02-29");
  });

  test("clamp bounds iso dates", () => {
    assert.equal(clamp("2020-01-01", "2021-01-01", "2022-01-01"), "2021-01-01");
    assert.equal(clamp("2023-01-01", "2021-01-01", "2022-01-01"), "2022-01-01");
    assert.equal(clamp("2021-06-01", "2021-01-01", "2022-01-01"), "2021-06-01");
  });

  test("span offers only presets the history covers", () => {
    const s = span({ preset: "Max", start: null, end: null }, "2023-06-01", "2025-06-01");
    assert.deepEqual(s.offered, ["1Y", "Max"]);
    assert.deepEqual([s.start, s.end], ["2023-06-01", "2025-06-01"]);
  });

  test("span falls back to Max when the preset is not offered", () => {
    const s = span({ preset: "5Y", start: null, end: null }, "2023-06-01", "2025-06-01");
    assert.equal(s.preset, "Max");
    assert.equal(s.start, "2023-06-01");
  });

  test("span applies a preset from the last session", () => {
    const s = span({ preset: "1Y", start: "2010-01-01", end: "2011-01-01" }, "2015-01-02", "2025-06-02");
    assert.deepEqual([s.start, s.end], ["2024-06-02", "2025-06-02"]);
  });

  test("span clamps a manual range into the history and orders it", () => {
    const s = span({ preset: null, start: "2010-01-01", end: "2009-01-01" }, "2015-01-02", "2025-06-02");
    assert.equal(s.preset, null);
    assert.deepEqual([s.start, s.end], ["2015-01-02", "2015-01-02"]);
  });
});

describe("schema", () => {
  const defs = { Mode: { enum: ["long", "short"], type: "string", default: "long" } };

  test("resolve merges a $ref with its siblings", () => {
    assert.deepEqual(resolve({ $ref: "#/$defs/Mode", title: "Side" }, defs), { ...defs.Mode, title: "Side" });
  });

  test("resolve follows a single allOf reference", () => {
    assert.deepEqual(resolve({ allOf: [{ $ref: "#/$defs/Mode" }], default: "short" }, defs), { ...defs.Mode, default: "short" });
  });

  test("resolve passes plain fields through", () => {
    const prop = { type: "number", minimum: 0 };
    assert.equal(resolve(prop, defs), prop);
  });

  test("visible checks every when condition", () => {
    assert.equal(visible({}, {}), true);
    assert.equal(visible({ when: { intraday: true } }, { intraday: true }), true);
    assert.equal(visible({ when: { intraday: true } }, { intraday: false }), false);
  });

  /** @type {ConfigSchema} */
  const schema = {
    properties: {
      timeframe: { const: "5m" },
      fast: { type: "integer", default: 10 },
      side: { $ref: "#/$defs/Mode" },
      stop: { type: "boolean", default: false },
      stop_pct: { type: "number", default: 0.25, when: { stop: true } },
      version: { const: 2 },
    },
    $defs: defs,
  };

  test("fieldsOf pins constants, fills defaults and skips windows", () => {
    /** @type {Record<string, Json>} */
    const params = {};
    const out = fieldsOf(schema, params, new Set(["timeframe", "fast"]));
    assert.deepEqual(
      out.map(([n]) => n),
      ["side", "stop"],
    );
    assert.deepEqual(params, { side: "long", stop: false, version: 2 });
  });

  test("fieldsOf shows a conditional field once its condition holds and keeps edits", () => {
    /** @type {Record<string, Json>} */
    const params = { stop: true, stop_pct: 0.5, stop_extra: 1 };
    const out = fieldsOf(schema, params, new Set(["timeframe", "fast"]));
    assert.deepEqual(
      out.map(([n]) => n),
      ["side", "stop", "stop_pct"],
    );
    assert.equal(params["stop_pct"], 0.5);
  });

  test("fieldsOf drops a hidden conditional value", () => {
    /** @type {Record<string, Json>} */
    const params = { stop: false, stop_pct: 0.5 };
    fieldsOf(schema, params, new Set());
    assert.equal("stop_pct" in params, false);
  });

  test("timeframeOf honours a constant", () => {
    assert.deepEqual(timeframeOf(schema, ["1m", "5m", "1d"], "1d"), { timeframe: "5m", fixed: true });
  });

  /** @type {ConfigSchema} */
  const free = { properties: { timeframe: { default: "1h" } } };

  test("timeframeOf keeps a current frame, else the default, else the coarsest", () => {
    assert.deepEqual(timeframeOf(free, ["5m", "1h", "1d"], "5m"), { timeframe: "5m", fixed: false });
    assert.deepEqual(timeframeOf(free, ["5m", "1h", "1d"], "4h"), { timeframe: "1h", fixed: false });
    assert.deepEqual(timeframeOf(free, ["5m", "1d"], null), { timeframe: "1d", fixed: false });
  });
});

describe("single line windows", () => {
  const choices = { fast: [5, 10, 20, 50], slow: [10, 20, 50, 200] };
  const props = { fast: { default: 10 }, slow: { default: 200 } };

  test("keeps a valid pair", () => {
    /** @type {Record<string, Json>} */
    const params = { fast: 10, slow: 50 };
    const out = windows(choices, STRICT, params, props);
    assert.deepEqual(out, [
      ["fast", [5, 10, 20, 50]],
      ["slow", [20, 50, 200]],
    ]);
    assert.deepEqual(params, { fast: 10, slow: 50 });
  });

  test("moves the upper window above the lower one", () => {
    /** @type {Record<string, Json>} */
    const params = { fast: 50, slow: 20 };
    const out = windows(choices, STRICT, params, props);
    assert.deepEqual(out[1], ["slow", [200]]);
    assert.equal(params["slow"], 200);
  });

  test("fills defaults and falls back to the first option", () => {
    /** @type {Record<string, Json>} */
    const params = {};
    windows({ fast: [5, 10], slow: [10, 20] }, STRICT, params, { fast: { default: 99 } });
    assert.deepEqual(params, { fast: 5, slow: 10 });
  });

  test("a loose order allows equal windows", () => {
    /** @type {Record<string, Json>} */
    const params = { fast: 20, slow: 20 };
    windows(choices, LOOSE, params, props);
    assert.equal(params["slow"], 20);
  });
});

describe("simulation grid", () => {
  const choices = { fast: [10, 20, 50], slow: [10, 20, 50] };

  test("offers only windows with a valid partner", () => {
    const { rows, picked } = gridOf(choices, STRICT, {});
    assert.deepEqual(
      rows.map((r) => [r.name, r.options]),
      [
        ["fast", [10, 20]],
        ["slow", [20, 50]],
      ],
    );
    assert.deepEqual(picked, { fast: [10, 20], slow: [20, 50] });
  });

  test("keeps hidden picks so they return when they become valid again", () => {
    const { rows, picked } = gridOf(choices, STRICT, { fast: [20, 50] });
    const fast = rows[0];
    assert.ok(fast);
    assert.deepEqual([fast.current, fast.hidden], [[20], [50]]);
    assert.deepEqual(picked, { fast: [20], slow: [50] });
  });

  test("combinations counts only ordered pairs", () => {
    assert.equal(combinations({ fast: [5, 10, 20], slow: [10, 20] }, STRICT), 3);
    assert.equal(combinations({ fast: [5, 10, 20], slow: [10, 20] }, LOOSE), 5);
  });

  test("combinations of an empty grid is one run", () => {
    assert.equal(combinations({}, STRICT), 1);
  });

  test("combinations of an emptied window is zero", () => {
    assert.equal(combinations({ fast: [], slow: [10] }, STRICT), 0);
  });

  test("unconstrained windows multiply", () => {
    assert.equal(combinations({ a: [1, 2, 3], b: [1, 2] }, []), 6);
  });
});

test("nonEmpty narrows", () => {
  assert.equal(nonEmpty([]), false);
  assert.equal(nonEmpty(["a"]), true);
});
