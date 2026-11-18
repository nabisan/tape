// @ts-check
/** @typedef {import("../types/api").PriceModel} Model */
/** @typedef {import("../types/api").PriceWindow} PriceWindow */
/** @typedef {import("../types/api").PathsTrace} Trace */
/** @typedef {import("../types/globals").PlotlyApi} PlotlyApi */
/** @typedef {import("../types/globals").PlotlyDiv} PlotlyDiv */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const TICKS = 8;
const DAY_MS = 86_400_000;
const SETTLE_MS = 250;

/**
 * @param {string} url
 * @returns {Promise<PlotlyApi>}
 */
function plotly(url) {
  if (window.Plotly) return Promise.resolve(window.Plotly);
  window.__tapePlotly ??= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = url;
    script.onload = () => (window.Plotly ? resolve(window.Plotly) : reject(new Error(`${url} did not define Plotly`)));
    script.onerror = () => reject(new Error(`could not load ${url}`));
    document.head.append(script);
  });
  return window.__tapePlotly;
}

/** @param {number} n */
const two = (n) => String(n).padStart(2, "0");

/**
 * @param {number} ms
 * @param {boolean} clock
 * @returns {string}
 */
export function stamp(ms, clock) {
  const d = new Date(ms);
  const day = `${two(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  return clock ? `${day} ${two(d.getUTCHours())}:${two(d.getUTCMinutes())}` : day;
}

/**
 * @param {PriceWindow} w
 * @param {boolean} intraday
 * @returns {{tickvals: number[], ticktext: string[]}}
 */
export function ticks(w, intraday) {
  const n = w.index.length;
  if (!n) return { tickvals: [], ticktext: [] };
  const span = (w.time[n - 1] ?? 0) - (w.time[0] ?? 0);
  const clock = intraday && span < 3 * DAY_MS;
  const picks = [...new Set(Array.from({ length: Math.min(TICKS, n) }, (_, i) => Math.round((i * (n - 1)) / Math.max(1, Math.min(TICKS, n) - 1))))];
  return {
    tickvals: picks.map((i) => w.index[i] ?? 0),
    ticktext: picks.map((i) => stamp(w.time[i] ?? 0, clock)),
  };
}

/**
 * @param {Trace} template
 * @param {PriceWindow} w
 * @param {boolean} intraday
 * @returns {object}
 */
export function candles(template, w, intraday) {
  const per = w.factor > 1 ? ` · ${w.factor.toLocaleString("en-US")} bars` : "";
  return {
    ...template,
    x: w.index,
    open: w.open,
    high: w.high,
    low: w.low,
    close: w.close,
    text: w.time.map((t) => stamp(t, intraday) + per),
    hoverinfo: "text+y",
  };
}

/**
 * @param {Trace} template
 * @param {PriceWindow} w
 * @param {"buy" | "sell"} side
 * @returns {object}
 */
export function marks(template, w, side) {
  const found = w.fills.filter((f) => f.side === side);
  return {
    ...template,
    x: found.map((f) => f.index),
    y: found.map((f) => f.price),
    customdata: found.map((f) => [f.quantity, f.tag, stamp(f.time, true)]),
    hovertemplate: "%{customdata[2]}  %{y}  qty %{customdata[0]}  %{customdata[1]}<extra></extra>",
  };
}

/**
 * @param {readonly Trace[]} templates
 * @param {PriceWindow} w
 * @param {boolean} intraday
 * @param {Record<string, string>} [labels]
 * @returns {object[]}
 */
export function traces(templates, w, intraday, labels = {}) {
  const lines = templates.filter((t) => t.meta === "indicator");
  const named = Object.keys(w.indicators).sort();
  const indicators = named.map((name, i) => ({ ...lines[i % Math.max(1, lines.length)], name: labels[name] ?? name, x: w.index, y: w.indicators[name] }));
  const out = [];
  for (const t of templates) {
    if (t.meta === "candles") out.push(candles(t, w, intraday), ...indicators);
    if (t.meta === "buy" || t.meta === "sell") out.push(marks(t, w, t.meta));
  }
  return out;
}

/**
 * @param {PriceWindow} w
 * @returns {string}
 */
export function describe(w) {
  const bars = w.last - w.first + 1;
  const n = (/** @type {number} */ v) => v.toLocaleString("en-US");
  const whole = w.first === 0 && w.last === w.count - 1;
  const parts = [whole ? `full run, ${n(w.count)} bars` : `${n(bars)} of ${n(w.count)} bars`];
  parts.push(w.factor > 1 ? `each candle aggregates ${n(w.factor)} bars, drag to zoom for detail` : "every bar shown");
  if (w.fills_in_range > w.fills.length) parts.push(`${n(w.fills_in_range)} fills in view, zoom in to see them`);
  if (!whole) parts.push("double-click for the full run");
  return parts.join(" · ");
}

/**
 * @param {Record<string, unknown>} event
 * @returns {[number, number] | "full" | null}
 */
export function zoomed(event) {
  if (event["xaxis.autorange"]) return "full";
  const pair = Array.isArray(event["xaxis.range"]) ? /** @type {unknown[]} */ (event["xaxis.range"]) : [];
  const lo = event["xaxis.range[0]"] ?? pair[0];
  const hi = event["xaxis.range[1]"] ?? pair[1];
  if (typeof lo === "number" && typeof hi === "number") return [Math.max(0, Math.floor(lo)), Math.ceil(hi)];
  return null;
}

/**
 * @param {[number, number]} asked
 * @param {[number, number]} shown
 * @returns {boolean}
 */
export const same = (asked, shown) => Math.abs(asked[0] - shown[0]) <= 1 && Math.abs(asked[1] - shown[1]) <= 1;

/**
 * @param {Response} r
 * @returns {Response}
 */
const ok = (r) => {
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r;
};

/** @type {import("@anywidget/types").AnyWidget<Model>} */
export default {
  async render({ model, el }) {
    const spec = model.get("figure");
    const intraday = model.get("intraday");
    const labels = model.get("labels");
    let resetAt = 0;
    const box = /** @type {PlotlyDiv} */ (document.createElement("div"));
    box.style.width = "100%";
    box.style.minHeight = `${Number(spec.layout["height"] ?? 440)}px`;
    box.textContent = "Loading the full run…";
    const note = document.createElement("div");
    note.className = "tw-note";
    el.append(box, note);
    /** @type {ResizeObserver | null} */
    let observer = null;
    let asked = 0;
    /** @type {[number, number]} */
    let shown = [-1, -1];
    /** @type {ReturnType<typeof setTimeout> | undefined} */
    let settle;
    const api = model.get("api");
    const key = model.get("key");
    try {
      const Plotly = await plotly(model.get("plotly"));
      /**
       * @param {number | null} first
       * @param {number | null} last
       */
      const draw = async (first, last) => {
        const mine = ++asked;
        const query = new URLSearchParams({ points: String(Math.max(200, Math.round((box.clientWidth || 1000) * 2))) });
        if (first !== null) query.set("first", String(first));
        if (last !== null) query.set("last", String(last));
        const w = /** @type {PriceWindow} */ (await fetch(`${api}/runs/${key}/price?${query}`).then(ok).then((r) => r.json()));
        if (mine !== asked) return;
        shown = [w.first, w.last];
        const axis = /** @type {Record<string, unknown>} */ (spec.layout["xaxis"] ?? {});
        const layout = {
          ...spec.layout,
          xaxis: { ...axis, ...ticks(w, intraday), range: [w.first - 0.5, w.last + 0.5], autorange: false },
          yaxis: { .../** @type {Record<string, unknown>} */ (spec.layout["yaxis"] ?? {}), autorange: true },
        };
        if (!box.layout) box.textContent = "";
        await Plotly.react(box, traces(spec.traces, w, intraday, labels), layout, spec.config);
        note.textContent = describe(w);
      };
      await draw(null, null);
      /** @param {[number, number] | "full"} range */
      const reload = (range) => {
        clearTimeout(settle);
        settle = setTimeout(() => {
          const [first, last] = range === "full" ? [null, null] : range;
          draw(first, last).catch((e) => (note.textContent = `Could not load that range: ${e instanceof Error ? e.message : String(e)}`));
        }, SETTLE_MS);
      };
      box.on?.("plotly_doubleclick", () => {
        resetAt = Date.now();
        reload("full");
      });
      box.on?.("plotly_relayout", (event) => {
        if (Date.now() - resetAt < 2 * SETTLE_MS) return;
        const range = zoomed(/** @type {Record<string, unknown>} */ (event));
        if (range === null || (range !== "full" && same(range, shown))) return;
        reload(range);
      });
      observer = new ResizeObserver(() => {
        if (box.layout) delete box.layout.width;
        Plotly.Plots.resize(box).catch(() => {});
      });
      observer.observe(box);
    } catch (e) {
      box.textContent = `The price chart could not load: ${e instanceof Error ? e.message : String(e)}`;
    }
    return () => {
      clearTimeout(settle);
      observer?.disconnect();
      window.Plotly?.purge(box);
    };
  },
};
