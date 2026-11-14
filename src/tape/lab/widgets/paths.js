// @ts-check
/** @typedef {import("../types/api").PathsModel} Model */
/** @typedef {import("../types/api").PathsMeta} PathsMeta */
/** @typedef {import("../types/api").PathsTrace} PathsTrace */
/** @typedef {import("../types/globals").PlotlyApi} PlotlyApi */
/** @typedef {import("../types/globals").PlotlyDiv} PlotlyDiv */

/**
 * @typedef Paths
 * @prop {number} n
 * @prop {Float64Array} x
 * @prop {(name: string) => Float32Array | null} series
 */

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

/**
 * @param {PathsMeta} meta
 * @param {ArrayBuffer} values
 * @returns {Paths}
 */
export function decode(meta, values) {
  const n = meta.dates.length;
  const all = new Float32Array(values);
  const index = new Map(meta.series.map((name, i) => [name, i]));
  return {
    n,
    x: Float64Array.from(meta.dates, (d) => Date.parse(`${d}T00:00:00Z`)),
    series: (name) => {
      const i = index.get(name);
      return i === undefined ? null : all.subarray(i * n, (i + 1) * n);
    },
  };
}

/**
 * @param {Response} r
 * @returns {Response}
 */
const ok = (r) => {
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r;
};

/**
 * @param {string} api
 * @param {string} key
 * @returns {Promise<Paths>}
 */
async function load(api, key) {
  const [meta, values] = await Promise.all([
    fetch(`${api}/sweeps/${key}/paths/meta`).then(ok).then((r) => /** @type {Promise<PathsMeta>} */ (r.json())),
    fetch(`${api}/sweeps/${key}/paths/values`).then(ok).then((r) => r.arrayBuffer()),
  ]);
  return decode(meta, values);
}

/**
 * @param {Paths} data
 * @param {readonly string[]} runs
 * @returns {Float32Array[]}
 */
const columns = (data, runs) => runs.map(data.series).filter((c) => c !== null);

/**
 * @param {Paths} data
 * @param {readonly string[]} runs
 * @returns {{x: Float64Array, y: Float32Array}}
 */
export function stacked(data, runs) {
  const found = columns(data, runs);
  const stride = data.n + 1;
  const x = new Float64Array(found.length * stride).fill(NaN);
  const y = new Float32Array(found.length * stride).fill(NaN);
  found.forEach((column, c) => {
    x.set(data.x, c * stride);
    y.set(column, c * stride);
  });
  return { x, y };
}

/**
 * @param {Paths} data
 * @param {readonly string[]} runs
 * @returns {Float32Array}
 */
export function median(data, runs) {
  const found = columns(data, runs);
  const out = new Float32Array(data.n);
  const row = new Float32Array(found.length);
  for (let i = 0; i < data.n; i++) {
    let k = 0;
    for (const column of found) {
      const v = column[i];
      if (v !== undefined && !Number.isNaN(v)) row[k++] = v;
    }
    if (!k) {
      out[i] = NaN;
      continue;
    }
    const sorted = row.subarray(0, k).sort();
    const mid = Math.floor(k / 2);
    out[i] = k % 2 ? (sorted[mid] ?? NaN) : ((sorted[mid - 1] ?? NaN) + (sorted[mid] ?? NaN)) / 2;
  }
  return out;
}

/**
 * @param {PathsTrace} trace
 * @param {Paths} data
 * @param {readonly string[]} runs
 * @returns {object}
 */
export function fill(trace, data, runs) {
  const role = trace.meta;
  if (role === "runs") return { ...trace, ...stacked(data, runs) };
  if (role === "median") return { ...trace, x: data.x, y: median(data, runs) };
  const name = role.startsWith("best:") ? role.slice(5) : (trace.name ?? "");
  return { ...trace, x: data.x, y: data.series(name) ?? new Float32Array(data.n).fill(NaN) };
}

/** @type {import("@anywidget/types").AnyWidget<Model>} */
export default {
  async render({ model, el }) {
    const spec = model.get("figure");
    const box = /** @type {PlotlyDiv} */ (document.createElement("div"));
    box.style.width = "100%";
    box.style.minHeight = `${Number(spec.layout["height"] ?? 380)}px`;
    box.textContent = "Loading equity paths…";
    el.append(box);
    /** @type {ResizeObserver | null} */
    let observer = null;
    try {
      const [Plotly, data] = await Promise.all([plotly(model.get("plotly")), load(model.get("api"), model.get("key"))]);
      const runs = model.get("runs");
      const traces = spec.traces.map((t) => fill(t, data, runs));
      const axis = /** @type {Record<string, unknown>} */ (spec.layout["xaxis"] ?? {});
      box.textContent = "";
      await Plotly.newPlot(box, traces, { ...spec.layout, xaxis: { ...axis, type: "date" } }, spec.config);
      observer = new ResizeObserver(() => {
        if (box.layout) delete box.layout.width;
        Plotly.Plots.resize(box).catch(() => {});
      });
      observer.observe(box);
    } catch (e) {
      box.textContent = `The equity paths could not load: ${e instanceof Error ? e.message : String(e)}`;
    }
    return () => {
      observer?.disconnect();
      window.Plotly?.purge(box);
    };
  },
};
