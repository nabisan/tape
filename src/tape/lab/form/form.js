const PRESETS = [["1Y", 1], ["3Y", 3], ["5Y", 5], ["Max", null]];
const LEGS = ["A", "B"];
const EXECUTION = {
  commission: false,
  commission_per_share: 0.005,
  slippage: false,
  slippage_bps: 1.0,
  prob_fill_on_limit: 1.0,
  intraday: false,
  entry_delay_minutes: 0,
  squareoff_minutes_before_close: 5,
};
const PAGES = { run: "Single line", simulation: "Simulation" };
const SCHEMES = ["GICS", "SIC"];
const POLL_MS = 3000;

const label = (v) => {
  const t = String(v).replaceAll("_", " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
};

const yearsBefore = (iso, years) => {
  const [y, m, d] = iso.split("-").map(Number);
  const day = new Date(Date.UTC(y - years, m - 1, d));
  const fixed = day.getUTCMonth() === m - 1 ? day : new Date(Date.UTC(y - years, m - 1, 28));
  return fixed.toISOString().slice(0, 10);
};

const count = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;

const clamp = (iso, lo, hi) => (iso < lo ? lo : iso > hi ? hi : iso);

const resolve = (prop, defs) => {
  const ref = prop.$ref ?? (prop.allOf ?? []).map((p) => p.$ref).find(Boolean);
  if (!ref) return prop;
  const base = defs[ref.split("/").pop()] ?? {};
  const rest = Object.fromEntries(Object.entries(prop).filter(([k]) => k !== "$ref" && k !== "allOf"));
  return { ...base, ...rest };
};

const visible = (prop, chosen) => Object.entries(prop.when ?? {}).every(([k, v]) => chosen[k] === v);

const dispatch = (e) => e.currentTarget._on?.[e.type]?.(e);

const bind = (el, type) => {
  el._bound ??= new Set();
  if (!el._bound.has(type)) {
    el._bound.add(type);
    el.addEventListener(type, dispatch);
  }
};

const h = (tag, attrs = {}, ...children) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) {
      (el._on ??= {})[k.slice(2)] = v;
      bind(el, k.slice(2));
    }
    else if (k === "className") el.className = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c instanceof Node || typeof c === "string" || typeof c === "number") el.append(c);
  return el;
};

function morph(from, to) {
  if (from === to) return;
  if (from.nodeType !== to.nodeType || from.nodeName !== to.nodeName) {
    from.replaceWith(to);
    return;
  }
  if (from.nodeType !== Node.ELEMENT_NODE) {
    if (from.data !== to.data) from.data = to.data;
    return;
  }
  for (const a of [...from.attributes]) if (!to.hasAttribute(a.name)) from.removeAttribute(a.name);
  for (const a of [...to.attributes]) if (from.getAttribute(a.name) !== a.value) from.setAttribute(a.name, a.value);
  from._on = to._on;
  for (const type of Object.keys(to._on ?? {})) bind(from, type);
  morphChildren(from, [...to.childNodes]);
  if (from instanceof HTMLInputElement) {
    if (from.type === "checkbox") from.checked = to.checked;
    else if (from.value !== to.value) from.value = to.value;
  } else if (from instanceof HTMLSelectElement && from.value !== to.value) from.value = to.value;
  else if (from instanceof HTMLDetailsElement && from.open !== to.open) from.open = to.open;
}

function morphChildren(parent, next) {
  const current = [...parent.childNodes];
  next.forEach((node, i) => (i < current.length ? morph(current[i], node) : parent.append(node)));
  current.slice(next.length).forEach((node) => node.remove());
}

const nodes = (...parts) => parts.flat(Infinity).filter((p) => p instanceof Node);

const field = (title, control, hint) =>
  h("label", { className: "tf-field" }, h("span", {}, title, hint && h("em", {}, ` · ${hint}`)), control);

const select = (options, value, onchange) =>
  h(
    "select",
    { className: "tf-input", onchange: (e) => onchange(e.target.value) },
    options.map(([v, text]) => {
      const o = h("option", { value: String(v) }, text);
      o.selected = String(v) === String(value);
      return o;
    }),
  );

const toggle = (title, checked, onchange) => {
  const box = h("input", { type: "checkbox", onchange: (e) => onchange(e.target.checked) });
  box.checked = checked;
  return h("label", { className: "tf-toggle" }, box, h("span", {}, title));
};

const number = (value, { min, max, step }, oninput) =>
  h("input", {
    className: "tf-input",
    type: "number",
    value: String(value),
    min: min ?? undefined,
    max: max ?? undefined,
    step: step ?? "any",
    oninput: (e) => e.target.value !== "" && oninput(Number(e.target.value)),
  });

const grid = (items) => h("div", { className: "tf-grid" }, items);

const segmented = (options, value, onpick) =>
  h(
    "div",
    { className: "tf-seg" },
    options.map(([v, text]) =>
      h("button", { type: "button", className: String(v) === String(value) ? "on" : "", onclick: () => onpick(v) }, text),
    ),
  );

const chips = (options, chosen, onchange) =>
  h(
    "div",
    { className: "tf-chips" },
    options.map((v) =>
      h(
        "button",
        {
          type: "button",
          className: chosen.includes(v) ? "on" : "",
          onclick: () => onchange(chosen.includes(v) ? chosen.filter((x) => x !== v) : [...chosen, v].sort((a, b) => a - b)),
        },
        String(v),
      ),
    ),
  );

async function fetchJson(url, init) {
  const r = await fetch(url, init);
  if (!r.ok) {
    const body = await r.text();
    let detail = body;
    try {
      detail = JSON.parse(body).detail ?? body;
    } catch {}
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return r.json();
}

const post = (url, body) =>
  fetchJson(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body ?? {}) });

function multi(ui, key, title, options, chosen, onchange, { placeholder, searchable } = {}) {
  const summary = chosen.length === 0 ? placeholder : chosen.length <= 2 ? chosen.join(", ") : `${chosen.length} selected`;
  const details = h(
    "details",
    { className: "tf-multi", ontoggle: (e) => (e.currentTarget.open ? ui.menus.add(key) : ui.menus.delete(key)) },
    h("summary", { className: "tf-input" }, summary),
  );
  details.open = ui.menus.has(key);
  const list = h("div", { className: "tf-multi-list" });
  const items = (filter) =>
    options
      .filter(([, text]) => !filter || text.toLowerCase().includes(filter.toLowerCase()))
      .map(([v, text]) => {
        const box = h("input", {
          type: "checkbox",
          onchange: (e) => onchange(e.target.checked ? [...chosen, v] : chosen.filter((x) => x !== v)),
        });
        box.checked = chosen.includes(v);
        return h("label", { className: "tf-multi-item" }, box, h("span", {}, text));
      });
  list.append(...items(ui.searches.get(key) ?? ""));
  const tools = [];
  if (searchable)
    tools.push(
      h("input", {
        className: "tf-input tf-search",
        placeholder: "Search",
        value: ui.searches.get(key) ?? "",
        oninput: (e) => {
          ui.searches.set(key, e.target.value);
          morphChildren(list, items(e.target.value));
        },
      }),
    );
  if (chosen.length) tools.push(h("button", { type: "button", className: "tf-clear", onclick: () => onchange([]) }, "Clear"));
  details.append(h("div", { className: "tf-multi-body" }, tools, list));
  return field(title, details);
}

function dates(state, first, last, redraw) {
  const offered = PRESETS.filter(([, y]) => y === null || yearsBefore(last, y) >= first);
  if (state.preset && !offered.some(([n]) => n === state.preset)) state.preset = "Max";
  if (state.preset) {
    const years = PRESETS.find(([n]) => n === state.preset)[1];
    state.start = years === null ? first : clamp(yearsBefore(last, years), first, last);
    state.end = last;
  }
  state.start = clamp(state.start ?? first, first, last);
  state.end = clamp(state.end ?? last, state.start, last);
  const input = (key, lo, hi) =>
    h("input", {
      className: "tf-input",
      type: "date",
      value: state[key],
      min: lo,
      max: hi,
      onchange: (e) => {
        if (!e.target.value) return;
        state[key] = e.target.value;
        state.preset = null;
        redraw();
      },
    });
  return [
    segmented(offered.map(([n]) => [n, n]), state.preset, (n) => ((state.preset = n), redraw())),
    grid([field("From", input("start", first, state.end)), field("To", input("end", state.start, last))]),
  ];
}

function fieldsOf(found, params, skip) {
  const props = found.config_schema.properties;
  const defs = found.config_schema.$defs ?? {};
  const out = [];
  for (const [name, raw] of Object.entries(props)) {
    if (skip.has(name)) continue;
    const prop = resolve(raw, defs);
    if ("const" in prop) {
      params[name] = prop.const;
      continue;
    }
    if (!(name in params)) params[name] = prop.default;
    if (prop.when && !visible(prop, params)) {
      delete params[name];
      continue;
    }
    out.push([name, prop]);
  }
  return out;
}

function input(params, name, prop, redraw) {
  const title = prop.title ?? label(name);
  if (prop.enum) return field(title, select(prop.enum.map((v) => [v, label(v)]), params[name], (v) => ((params[name] = v), redraw())));
  if (prop.type === "boolean") return toggle(title, Boolean(params[name]), (v) => ((params[name] = v), redraw()));
  const integer = prop.type === "integer";
  const limits = {
    min: prop.minimum ?? prop.exclusiveMinimum,
    max: prop.maximum ?? prop.exclusiveMaximum,
    step: prop.step ?? (integer ? 1 : undefined),
  };
  return field(title, number(params[name], limits, (v) => (params[name] = integer ? Math.round(v) : v)));
}

function timeframeOf(found, params) {
  const props = found.config_schema.properties;
  const tfProp = resolve(props.timeframe ?? {}, found.config_schema.$defs ?? {});
  const frames = found.space.timeframes;
  if ("const" in tfProp) params.timeframe = tfProp.const;
  else if (!frames.includes(params.timeframe)) params.timeframe = frames.includes(tfProp.default) ? tfProp.default : frames.at(-1);
  return { frames, fixed: "const" in tfProp };
}

function execution(state, allowed, ui, redraw) {
  const e = state.execution;
  if (!allowed) e.intraday = false;
  const set = (k) => (v) => ((e[k] = v), redraw());
  const body = h(
    "div",
    { className: "tf-details-body" },
    toggle("Commission", e.commission, set("commission")),
    e.commission && field("Commission ($/share)", number(e.commission_per_share, { min: 0, max: 1, step: 0.001 }, (v) => (e.commission_per_share = v))),
    toggle("Slippage", e.slippage, set("slippage")),
    e.slippage && field("Slippage (bps)", number(e.slippage_bps, { min: 0, max: 1000, step: 0.5 }, (v) => (e.slippage_bps = v))),
    field("Limit fill probability on touch", number(e.prob_fill_on_limit, { min: 0, max: 1, step: 0.05 }, (v) => (e.prob_fill_on_limit = v))),
    allowed && toggle("Intraday (flat at every close)", e.intraday, set("intraday")),
    e.intraday &&
      grid([
        field("Entry delay (min)", number(e.entry_delay_minutes, { min: 0, max: 390, step: 5 }, (v) => (e.entry_delay_minutes = Math.round(v)))),
        field("Square-off (min)", number(e.squareoff_minutes_before_close, { min: 1, max: 390, step: 1 }, (v) => (e.squareoff_minutes_before_close = Math.round(v)))),
      ]),
  );
  const details = h(
    "details",
    { className: "tf-details", ontoggle: (e) => (ui.executionOpen = e.currentTarget.open) },
    h("summary", {}, "Execution"),
    body,
  );
  details.open = ui.executionOpen;
  return details;
}

function submitButton(model, text, ready, payload) {
  const busy = model.get("busy");
  const message = model.get("message");
  const submit = () => {
    const previous = model.get("submission")?.n ?? 0;
    model.set("submission", { n: previous + 1, ...payload() });
    model.save_changes();
  };
  return [
    h("button", { type: "button", className: "tf-run", disabled: busy || !ready, onclick: submit }, busy ? "Running…" : text),
    message && h("div", { className: "tf-error" }, message),
  ];
}

function spaceCache(api, redraw) {
  const spaces = new Map();
  return (name, ticker, end) => {
    const key = `${name}|${ticker}|${end}`;
    if (!spaces.has(key)) {
      spaces.set(key, null);
      fetchJson(`${api}/strategies/${name}/space?ticker=${encodeURIComponent(ticker)}&end=${end}`)
        .then((found) => spaces.set(key, found))
        .catch((e) => spaces.set(key, { error: e.message }))
        .finally(redraw);
    }
    return spaces.get(key);
  };
}

function runForm({ model, api, catalog, strategies, redraw }) {
  const tickers = new Map(catalog.tickers.map((t) => [t.ticker, t]));
  const names = Object.keys(strategies);
  const seed = model.get("seed") ?? {};
  const state = {
    ticker: tickers.has(seed.ticker) ? seed.ticker : tickers.has("AAPL") ? "AAPL" : catalog.tickers[0].ticker,
    preset: seed.start ? null : "Max",
    start: seed.start ?? null,
    end: seed.end ?? null,
    compare: Boolean(seed.compare),
    legs: Object.fromEntries(
      LEGS.map((leg, i) => {
        const s = seed.legs?.[leg];
        return [leg, { strategy: s && strategies[s.strategy] ? s.strategy : names[i % names.length], params: { ...(s?.params ?? {}) } }];
      }),
    ),
    execution: { ...EXECUTION, ...(seed.execution ?? {}) },
  };
  const ui = { executionOpen: false };
  const space = spaceCache(api, redraw);
  const legs = () => (state.compare ? LEGS : LEGS.slice(0, 1));

  const legView = (name) => {
    const leg = state.legs[name];
    const p = leg.params;
    const head = field(
      state.compare ? `Strategy ${name}` : "Strategy",
      select(names.map((n) => [n, strategies[n].title]), leg.strategy, (v) => ((leg.strategy = v), (leg.params = {}), redraw())),
    );
    const found = space(leg.strategy, state.ticker, state.end);
    if (found === null) return h("div", { className: "tf-leg" }, head, h("div", { className: "tf-note" }, "Loading choices…"));
    if (found.error) return h("div", { className: "tf-leg" }, head, h("div", { className: "tf-error" }, found.error));
    if (!found.space.timeframes.length)
      return h("div", { className: "tf-leg" }, head, h("div", { className: "tf-note" }, "No precomputed window has enough history by the end date."));
    const tf = timeframeOf(found, p);
    const props = found.config_schema.properties;
    const choices = found.space.choices[p.timeframe] ?? {};
    const windows = [];
    const taken = {};
    for (const [w, domain] of Object.entries(choices)) {
      let options = [...domain];
      for (const o of found.space.ordered) {
        const holds = (lo, hi) => (o.strict ? lo < hi : lo <= hi);
        if (o.upper === w && o.lower in taken) options = options.filter((v) => holds(p[o.lower], v));
        if (o.lower === w && o.upper in taken) options = options.filter((v) => holds(v, p[o.upper]));
      }
      if (!options.includes(p[w])) p[w] = options.includes(props[w]?.default) ? props[w].default : options[0];
      taken[w] = true;
      windows.push(field(props[w]?.title ?? label(w), select(options.map((o) => [o, o]), p[w], (v) => ((p[w] = Number(v)), redraw()))));
    }
    const fields = fieldsOf(found, p, new Set(["timeframe", ...Object.keys(choices)])).map(([f, prop]) => input(p, f, prop, redraw));
    const frame = tf.fixed ? null : field("Timeframe", select(tf.frames.map((f) => [f, f]), p.timeframe, (v) => ((p.timeframe = v), redraw())));
    return h("div", { className: "tf-leg" }, head, frame, grid([...windows, ...fields]));
  };

  return () => {
    const t = tickers.get(state.ticker);
    const tickerInput = h("input", {
      className: "tf-input",
      list: "tf-tickers",
      value: state.ticker,
      spellcheck: "false",
      autocomplete: "off",
      onchange: (e) => {
        const v = e.target.value.trim().toUpperCase();
        if (tickers.has(v)) {
          state.ticker = v;
          redraw();
        } else e.target.value = state.ticker;
      },
      onfocus: (e) => e.target.select(),
    });
    const dateParts = dates(state, t.first_session, t.last_session, redraw);
    const legViews = legs().map(legView);
    const ready =
      legs().every((leg) => {
        const found = space(state.legs[leg].strategy, state.ticker, state.end);
        return found && !found.error && found.space.timeframes.length;
      }) && state.start <= state.end;
    const payload = () => ({
      ticker: state.ticker,
      start: state.start,
      end: state.end,
      legs: Object.fromEntries(legs().map((leg) => [leg, { strategy: state.legs[leg].strategy, params: { ...state.legs[leg].params } }])),
      execution: { ...state.execution },
    });
    return nodes(
      field("Ticker", tickerInput),
      h("datalist", { id: "tf-tickers" }, [...tickers.keys()].map((v) => h("option", { value: v }))),
      dateParts,
      toggle("Compare two strategies", state.compare, (v) => ((state.compare = v), redraw())),
      legViews,
      execution(state, legs().every((leg) => strategies[state.legs[leg].strategy]?.intraday), ui, redraw),
      submitButton(model, state.compare ? "Run comparison" : "Run single line", ready, payload),
    );
  };
}

function combinations(gridValues, ordered) {
  const names = Object.keys(gridValues);
  let count = 0;
  const walk = (i, picked) => {
    if (i === names.length) {
      const ok = ordered.every((o) => !(o.lower in picked && o.upper in picked) || (o.strict ? picked[o.lower] < picked[o.upper] : picked[o.lower] <= picked[o.upper]));
      count += ok ? 1 : 0;
      return;
    }
    for (const v of gridValues[names[i]]) walk(i + 1, { ...picked, [names[i]]: v });
  };
  walk(0, {});
  return names.length ? count : 1;
}

function simulationForm({ model, api, catalog, strategies, redraw }) {
  const names = Object.keys(strategies);
  const all = catalog.tickers.map((t) => t.ticker);
  const state = {
    universe: "all",
    custom: [],
    scheme: "GICS",
    sectors: [],
    industries: [],
    volatility: [],
    excluded: [],
    preset: "Max",
    start: null,
    end: null,
    strategy: names[0],
    timeframe: null,
    grid: {},
    params: {},
    execution: { ...EXECUTION },
  };
  const ui = { executionOpen: false, menus: new Set(), searches: new Map() };
  const space = spaceCache(api, redraw);
  let basket = null;
  let asked = "";

  const ask = () => {
    const query = {
      universe: state.universe,
      custom: state.custom,
      scheme: state.scheme,
      sectors: state.sectors,
      industries: state.industries,
      volatility: state.volatility,
      start: state.start,
    };
    const key = JSON.stringify(query);
    if (key === asked) return;
    asked = key;
    post(`${api}/universes/basket`, query)
      .then((found) => {
        if (asked === key) basket = found;
      })
      .catch((e) => (basket = { error: e.message }))
      .finally(redraw);
  };

  const basketView = () => {
    if (!basket) return [h("div", { className: "tf-note" }, "Loading the universe…")];
    if (basket.error) return [h("div", { className: "tf-error" }, basket.error)];
    const parts = [
      field(
        "Universe",
        select(basket.universes.map((u) => [u.key, u.title]), state.universe, (v) => {
          Object.assign(state, { universe: v, sectors: [], industries: [], volatility: [], excluded: [] });
          redraw();
        }),
      ),
    ];
    if (state.universe === "custom") {
      parts.push(
        multi(ui, "custom", "Tickers", all.map((t) => [t, t]), state.custom, (v) => ((state.custom = v), redraw()), {
          placeholder: "Choose tickers",
          searchable: true,
        }),
      );
    } else {
      parts.push(
        field("Classification", segmented(SCHEMES.map((s) => [s, s]), state.scheme, (s) => (Object.assign(state, { scheme: s, sectors: [], industries: [] }), redraw()))),
        multi(ui, `sector:${state.scheme}`, "Sector", basket.sectors.map((s) => [s, s]), state.sectors, (v) => (Object.assign(state, { sectors: v, industries: [] }), redraw()), {
          placeholder: "All sectors",
        }),
        multi(ui, `industry:${state.scheme}`, "Industry", basket.industries.map((s) => [s, s]), state.industries, (v) => ((state.industries = v), redraw()), {
          placeholder: "All industries",
          searchable: true,
        }),
      );
      if (basket.quintiles.length)
        parts.push(
          multi(ui, "vol", "Volatility quintile", basket.quintiles.map((q, i) => [i, q]), state.volatility, (v) => ((state.volatility = v.sort()), redraw()), {
            placeholder: "Any volatility",
          }),
        );
    }
    return parts;
  };

  const chosen = () => {
    if (!basket || basket.error) return [];
    const out = new Set(state.excluded);
    return basket.tickers.filter((t) => !out.has(t));
  };

  const tickersView = () => {
    if (!basket || basket.error || state.universe === "custom") return [];
    const list = chosen();
    const details = h(
      "details",
      { className: "tf-details", ontoggle: (e) => (e.currentTarget.open ? ui.menus.add("tickers") : ui.menus.delete("tickers")) },
      h("summary", {}, `Tickers · ${list.length.toLocaleString()}`),
      h(
        "div",
        { className: "tf-details-body" },
        h(
          "div",
          { className: "tf-tags" },
          basket.tickers.map((t) => {
            const out = state.excluded.includes(t);
            return h(
              "button",
              {
                type: "button",
                className: out ? "off" : "",
                title: out ? "Add back" : "Leave out",
                onclick: () => ((state.excluded = out ? state.excluded.filter((x) => x !== t) : [...state.excluded, t]), redraw()),
              },
              t,
            );
          }),
        ),
      ),
    );
    details.open = ui.menus.has("tickers");
    return [details];
  };

  const summaryView = () => {
    if (!basket || basket.error) return [];
    const list = chosen();
    const notes = [...basket.notes];
    if (state.excluded.length) notes.push(`${state.excluded.length} left out by hand`);
    return [h("div", { className: "tf-basket" }, h("b", {}, count(list.length, "ticker")), notes.map((n) => h("div", {}, n)))];
  };

  const strategyView = (tickers) => {
    const head = field(
      "Strategy",
      select(names.map((n) => [n, strategies[n].title]), state.strategy, (v) => (Object.assign(state, { strategy: v, grid: {}, params: {}, timeframe: null }), redraw())),
    );
    if (!tickers.length) return { parts: [head], runs: 0 };
    const found = space(state.strategy, tickers[0], state.end);
    if (found === null) return { parts: [head, h("div", { className: "tf-note" }, "Loading choices…")], runs: 0 };
    if (found.error) return { parts: [head, h("div", { className: "tf-error" }, found.error)], runs: 0 };
    if (!found.space.timeframes.length)
      return { parts: [head, h("div", { className: "tf-note" }, `No precomputed features cover ${strategies[state.strategy].title} for ${tickers[0]}.`)], runs: 0 };
    const p = { timeframe: state.timeframe };
    const tf = timeframeOf(found, p);
    if (state.timeframe !== p.timeframe) state.grid = {};
    state.timeframe = p.timeframe;
    const choices = found.space.choices[state.timeframe] ?? {};
    const props = found.config_schema.properties;
    const windows = [];
    const picked = {};
    for (const [w, domain] of Object.entries(choices)) {
      let options = [...domain];
      for (const o of found.space.ordered) {
        const holds = (lo, hi) => (o.strict ? lo < hi : lo <= hi);
        const partner = (n) => picked[n] ?? choices[n];
        if (o.upper === w) options = options.filter((v) => partner(o.lower).some((lo) => holds(lo, v)));
        if (o.lower === w) options = options.filter((v) => partner(o.upper).some((hi) => holds(v, hi)));
      }
      const saved = state.grid[w] ?? options;
      const current = saved.filter((v) => options.includes(v));
      const hidden = saved.filter((v) => !options.includes(v));
      picked[w] = current;
      windows.push(field(props[w]?.title ?? label(w), chips(options, current, (v) => ((state.grid[w] = [...v, ...hidden]), redraw()))));
    }
    const combos = combinations(picked, found.space.ordered);
    const runs = tickers.length * combos;
    const caption = h("div", { className: "tf-note" }, `${count(tickers.length, "ticker")} × ${count(combos, "valid combination")} = ${count(runs, "run")}`);
    const fields = fieldsOf(found, state.params, new Set(["timeframe", ...Object.keys(choices)])).map(([f, prop]) => input(state.params, f, prop, redraw));
    const frame = tf.fixed ? null : field("Timeframe", select(tf.frames.map((f) => [f, f]), state.timeframe, (v) => (Object.assign(state, { timeframe: v, grid: {} }), redraw())));
    return { parts: [head, frame, windows, caption, grid(fields)], runs, grid: picked };
  };

  return () => {
    const dateParts = dates(state, catalog.first_session, catalog.last_session, redraw);
    ask();
    const tickers = chosen();
    const strategy = strategyView(tickers);
    const payload = () => ({
      tickers,
      start: state.start,
      end: state.end,
      strategy: state.strategy,
      timeframe: state.timeframe,
      grid: { ...strategy.grid },
      params: { ...state.params },
      execution: { ...state.execution },
    });
    return nodes(
      basketView(),
      dateParts,
      tickersView(),
      summaryView(),
      strategy.parts,
      execution(state, Boolean(strategies[state.strategy]?.intraday), ui, redraw),
      submitButton(model, "Run simulation", strategy.runs > 0 && state.start <= state.end, payload),
    );
  };
}

function background(api, strategies, box) {
  let stopping = new Set();
  const draw = (sweeps, ledgers) => {
    if (!sweeps.length && !ledgers.length) return morphChildren(box, []);
    morphChildren(box, [
      h("div", { className: "tf-section" }, "Background"),
      ...sweeps.map((s) => {
        const total = Math.max(1, s.total);
        const name = strategies[s.strategy]?.title ?? s.strategy;
        return h(
          "div",
          { className: "tf-job" },
          h("div", { className: "tf-bar" }, h("i", { style: `width:${(100 * s.done) / total}%` })),
          h("div", { className: "tf-note" }, `Simulation ${s.key.slice(0, 8)} · ${name} ${s.timeframe} · ${count(s.tickers, "ticker")} · ${s.done.toLocaleString()}/${total.toLocaleString()}`),
          h(
            "button",
            {
              type: "button",
              className: "tf-stop",
              disabled: stopping.has(s.key),
              onclick: () => {
                stopping.add(s.key);
                post(`${api}/sweeps/${s.key}/stop`).catch(() => stopping.delete(s.key)).finally(tick);
              },
            },
            stopping.has(s.key) ? "Stopping…" : "Stop",
          ),
        );
      }),
      ...ledgers.map((k) => h("div", { className: "tf-note" }, `MTM ledger · run ${k.slice(0, 8)} · building`)),
    ]);
  };
  const tick = () =>
    Promise.all([fetchJson(`${api}/sweeps`), fetchJson(`${api}/ledgers`)])
      .then(([sweeps, ledgers]) => {
        stopping = new Set([...stopping].filter((k) => sweeps.some((s) => s.key === k)));
        draw(sweeps, ledgers);
      })
      .catch(() => {});
  tick();
  const timer = setInterval(tick, POLL_MS);
  return () => clearInterval(timer);
}

export default {
  async render({ model, el }) {
    const api = model.get("api");
    const page = model.get("page");
    const root = h("div", { className: "tf" });
    const jobs = h("div", { className: "tf-jobs" });
    el.append(root, jobs);

    const brand = h(
      "div",
      { className: "tf-head" },
      h("div", { className: "tf-brand" }, h("span", { className: "word" }, "tape"), h("span", { className: "tag" }, "US equity backtests")),
      h(
        "nav",
        { className: "tf-seg" },
        Object.entries(PAGES).map(([key, name]) =>
          key === page ? h("a", { className: "on", "aria-current": "page" }, name) : h("a", { href: model.get("pages")[key] ?? "#" }, name),
        ),
      ),
    );

    root.replaceChildren(brand, h("div", { className: "tf-note" }, "Loading…"));
    let catalog, strategies;
    try {
      [catalog, strategies] = await Promise.all([fetchJson(`${api}/catalog`), fetchJson(`${api}/strategies`)]);
    } catch (e) {
      root.replaceChildren(brand, h("div", { className: "tf-error" }, `The server could not be reached: ${e.message}`));
      return;
    }

    let view = () => [];
    const redraw = () => morphChildren(root, [brand, ...view()]);
    const ctx = { model, api, catalog, strategies, redraw };
    view = page === "simulation" ? simulationForm(ctx) : runForm(ctx);
    model.on("change:busy", redraw);
    model.on("change:message", redraw);
    redraw();
    return background(api, strategies, jobs);
  },
};
