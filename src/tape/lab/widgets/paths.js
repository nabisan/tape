function plotly(url) {
  if (window.Plotly) return Promise.resolve(window.Plotly);
  window.__tapePlotly ??= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = url;
    script.onload = () => resolve(window.Plotly);
    script.onerror = () => reject(new Error(`could not load ${url}`));
    document.head.append(script);
  });
  return window.__tapePlotly;
}

async function load(api, key) {
  const [meta, values] = await Promise.all([
    fetch(`${api}/sweeps/${key}/paths/meta`).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status}`)))),
    fetch(`${api}/sweeps/${key}/paths/values`).then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(`${r.status}`)))),
  ]);
  const n = meta.dates.length;
  const all = new Float32Array(values);
  const index = new Map(meta.series.map((name, i) => [name, i]));
  const series = (name) => (index.has(name) ? all.subarray(index.get(name) * n, (index.get(name) + 1) * n) : null);
  const x = Float64Array.from(meta.dates, (d) => Date.parse(`${d}T00:00:00Z`));
  return { n, x, series };
}

function stacked(data, runs) {
  const columns = runs.map(data.series).filter(Boolean);
  const stride = data.n + 1;
  const xs = new Float64Array(columns.length * stride).fill(NaN);
  const ys = new Float32Array(columns.length * stride).fill(NaN);
  columns.forEach((column, c) => {
    xs.set(data.x, c * stride);
    ys.set(column, c * stride);
  });
  return { x: xs, y: ys };
}

function median(data, runs) {
  const columns = runs.map(data.series).filter(Boolean);
  const out = new Float32Array(data.n);
  const row = new Float32Array(columns.length);
  for (let i = 0; i < data.n; i++) {
    let k = 0;
    for (const column of columns) if (!Number.isNaN(column[i])) row[k++] = column[i];
    if (!k) {
      out[i] = NaN;
      continue;
    }
    const sorted = row.subarray(0, k).sort();
    out[i] = k % 2 ? sorted[(k - 1) / 2] : (sorted[k / 2 - 1] + sorted[k / 2]) / 2;
  }
  return out;
}

function fill(trace, data, runs) {
  const role = trace.meta;
  if (role === "runs") return { ...trace, ...stacked(data, runs) };
  if (role === "median") return { ...trace, x: data.x, y: median(data, runs) };
  const name = role.startsWith("best:") ? role.slice(5) : trace.name;
  return { ...trace, x: data.x, y: data.series(name) ?? new Float32Array(data.n).fill(NaN) };
}

export default {
  async render({ model, el }) {
    const box = document.createElement("div");
    box.style.width = "100%";
    box.style.minHeight = `${model.get("figure").layout?.height ?? 380}px`;
    box.textContent = "Loading equity paths…";
    el.append(box);
    let observer = null;
    try {
      const [Plotly, data] = await Promise.all([plotly(model.get("plotly")), load(model.get("api"), model.get("key"))]);
      const spec = model.get("figure");
      const runs = model.get("runs");
      const traces = spec.traces.map((t) => fill(t, data, runs));
      const layout = { ...spec.layout, xaxis: { ...spec.layout.xaxis, type: "date" } };
      box.textContent = "";
      await Plotly.newPlot(box, traces, layout, { ...spec.config, responsive: false });
      observer = new ResizeObserver(() => {
        if (box.layout) delete box.layout.width;
        Plotly.Plots.resize(box).catch(() => {});
      });
      observer.observe(box);
    } catch (e) {
      box.textContent = `The equity paths could not load: ${e.message}`;
    }
    return () => {
      observer?.disconnect();
      window.Plotly?.purge(box);
    };
  },
};
