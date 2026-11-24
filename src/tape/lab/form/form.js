// @ts-check
/** @typedef {import("../types/api").FormModel} Model */
/** @typedef {import("../types/api").Catalog} Catalog */
/** @typedef {import("../types/api").TickerInfo} TickerInfo */
/** @typedef {import("../types/api").StrategyInfo} StrategyInfo */
/** @typedef {import("../types/api").StrategySpace} StrategySpace */
/** @typedef {import("../types/api").ConfigSchema} ConfigSchema */
/** @typedef {import("../types/api").FieldSchema} FieldSchema */
/** @typedef {import("../types/api").Basket} Basket */
/** @typedef {import("../types/api").BasketQuery} BasketQuery */
/** @typedef {import("../types/api").SweepStatus} SweepStatus */
/** @typedef {import("../types/api").Ordered} Ordered */
/** @typedef {import("../types/api").ExecutionChoice} ExecutionChoice */
/** @typedef {import("../types/api").Timeframe} Timeframe */
/** @typedef {import("../types/api").JsonInput} Json */
/** @typedef {import("../types/api").RunRequestInput} RunRequest */
/** @typedef {import("../types/api").SweepRequestInput} SweepRequest */
/** @typedef {import("@anywidget/types").AnyModel<Model>} AnyModel */

/** @typedef {Record<string, Json>} Params */
/** @typedef {Required<ExecutionChoice>} Execution */
/** @typedef {Omit<RunRequest, "n"> | Omit<SweepRequest, "n">} Request */
/** @typedef {{ error: string }} Failure */
/** @template T @typedef {[T, ...T[]]} NonEmpty */
/** @typedef {{ preset: string | null, start: string | null, end: string | null }} Range */
/** @typedef {{ preset: string | null, start: string, end: string, offered: string[] }} Span */
/** @typedef {{ executionOpen: boolean, menus: Set<string>, searches: Map<string, string> }} Ui */
/** @typedef {(e: Event) => unknown} Handler */
/** @typedef {string | number | boolean | null | undefined | Handler} Attr */
/** @typedef {Node | string | number | boolean | null | undefined | Child[]} Child */
/** @typedef {{ name: string, options: number[], current: number[], hidden: number[] }} GridRow */

/** @type {readonly (readonly [string, number | null])[]} */
export const PRESETS = [["1Y", 1], ["3Y", 3], ["5Y", 5], ["Max", null]];
/** @type {Execution} */
export const EXECUTION = {
  commission: false,
  commission_per_share: 0.005,
  slippage: false,
  slippage_bps: 1.0,
  prob_fill_on_limit: 1.0,
  intraday: false,
  entry_delay_minutes: 0,
  squareoff_minutes_before_close: 5,
  minute_fills: false,
  margin: false,
  leverage: 30,
  maintenance: 0.5,
};
const PAGES = { run: "Single line", simulation: "Simulation", portfolios: "Portfolios" };
const SCHEMES = /** @type {const} */ (["GICS", "SIC"]);
const POLL_MS = 3000;

/** @param {unknown} v */
export const label = (v) => {
  const t = String(v).replaceAll("_", " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
};

/**
 * @param {string} iso
 * @param {number} years
 */
export const yearsBefore = (iso, years) => {
  const [y = 0, m = 1, d = 1] = iso.split("-").map(Number);
  const day = new Date(Date.UTC(y - years, m - 1, d));
  const fixed = day.getUTCMonth() === m - 1 ? day : new Date(Date.UTC(y - years, m - 1, 28));
  return fixed.toISOString().slice(0, 10);
};

/**
 * @param {number} n
 * @param {string} word
 */
export const count = (n, word) => `${n.toLocaleString("en-US")} ${word}${n === 1 ? "" : "s"}`;

/**
 * @param {string} iso
 * @param {string} lo
 * @param {string} hi
 */
export const clamp = (iso, lo, hi) => (iso < lo ? lo : iso > hi ? hi : iso);

/**
 * @template T
 * @param {readonly T[]} xs
 * @returns {xs is NonEmpty<T>}
 */
export const nonEmpty = (xs) => xs.length > 0;

/**
 * @param {Range} range
 * @param {string} first
 * @param {string} last
 * @returns {Span}
 */
export function span(range, first, last) {
  const offered = PRESETS.filter(([, y]) => y === null || yearsBefore(last, y) >= first).map(([n]) => n);
  const preset = range.preset && !offered.includes(range.preset) ? "Max" : range.preset;
  let start = range.start ?? first;
  let end = range.end ?? last;
  if (preset) {
    const years = PRESETS.find(([n]) => n === preset)?.[1] ?? null;
    start = years === null ? first : clamp(yearsBefore(last, years), first, last);
    end = last;
  }
  start = clamp(start, first, last);
  return { preset, start, end: clamp(end, start, last), offered };
}

/**
 * @param {FieldSchema} prop
 * @param {Record<string, FieldSchema>} defs
 * @returns {FieldSchema}
 */
export const resolve = (prop, defs) => {
  const ref = prop.$ref ?? (prop.allOf ?? []).map((p) => p.$ref).find(Boolean);
  if (!ref) return prop;
  const base = defs[ref.split("/").pop() ?? ""] ?? {};
  const rest = Object.fromEntries(Object.entries(prop).filter(([k]) => k !== "$ref" && k !== "allOf"));
  return { ...base, ...rest };
};

/**
 * @param {FieldSchema} prop
 * @param {Params} chosen
 */
export const visible = (prop, chosen) => Object.entries(prop.when ?? {}).every(([k, v]) => chosen[k] === v);

/**
 * @param {Ordered} o
 * @param {number} lo
 * @param {number} hi
 */
const holds = (o, lo, hi) => (o.strict ? lo < hi : lo <= hi);

/**
 * @param {{ config_schema: Record<string, unknown> }} found
 * @returns {ConfigSchema}
 */
export const schemaOf = (found) => /** @type {ConfigSchema} */ (found.config_schema);

/**
 * @param {ConfigSchema} schema
 * @param {Params} params
 * @param {ReadonlySet<string>} skip
 * @returns {[string, FieldSchema][]}
 */
export function fieldsOf(schema, params, skip) {
  const defs = schema.$defs ?? {};
  /** @type {[string, FieldSchema][]} */
  const out = [];
  for (const [name, raw] of Object.entries(schema.properties)) {
    if (skip.has(name)) continue;
    const prop = resolve(raw, defs);
    if ("const" in prop) {
      params[name] = prop.const ?? null;
      continue;
    }
    if (!(name in params)) params[name] = prop.default ?? null;
    if (prop.when && !visible(prop, params)) {
      delete params[name];
      continue;
    }
    out.push([name, prop]);
  }
  return out;
}

/**
 * @param {ConfigSchema} schema
 * @param {NonEmpty<Timeframe>} frames
 * @param {Json | undefined} current
 * @returns {{ timeframe: Timeframe, fixed: boolean }}
 */
export function timeframeOf(schema, frames, current) {
  const prop = resolve(schema.properties["timeframe"] ?? {}, schema.$defs ?? {});
  if ("const" in prop) return { timeframe: /** @type {Timeframe} */ (prop.const), fixed: true };
  const offered = frames.find((f) => f === current) ?? frames.find((f) => f === prop.default) ?? frames[frames.length - 1] ?? frames[0];
  return { timeframe: offered, fixed: false };
}

/**
 * @param {Record<string, number[]>} choices
 * @param {readonly Ordered[]} ordered
 * @param {Params} params
 * @param {Record<string, FieldSchema>} props
 * @returns {[string, number[]][]}
 */
export function windows(choices, ordered, params, props) {
  const taken = new Set();
  return Object.entries(choices).map(([w, domain]) => {
    let options = [...domain];
    for (const o of ordered) {
      const lo = params[o.lower];
      const hi = params[o.upper];
      if (o.upper === w && taken.has(o.lower) && typeof lo === "number") options = options.filter((v) => holds(o, lo, v));
      if (o.lower === w && taken.has(o.upper) && typeof hi === "number") options = options.filter((v) => holds(o, v, hi));
    }
    const current = params[w];
    if (typeof current !== "number" || !options.includes(current)) {
      const fallback = props[w]?.default;
      params[w] = typeof fallback === "number" && options.includes(fallback) ? fallback : (options[0] ?? null);
    }
    taken.add(w);
    return [w, options];
  });
}

/**
 * @param {Record<string, number[]>} choices
 * @param {readonly Ordered[]} ordered
 * @param {Record<string, number[]>} saved
 * @returns {{ rows: GridRow[], picked: Record<string, number[]> }}
 */
export function gridOf(choices, ordered, saved) {
  /** @type {Record<string, number[]>} */
  const picked = {};
  /** @param {string} n */
  const partner = (n) => picked[n] ?? choices[n] ?? [];
  const rows = Object.entries(choices).map(([name, domain]) => {
    const options = domain.filter((v) =>
      ordered.every(
        (o) =>
          (o.upper !== name || partner(o.lower).some((lo) => holds(o, lo, v))) &&
          (o.lower !== name || partner(o.upper).some((hi) => holds(o, v, hi))),
      ),
    );
    const kept = saved[name] ?? options;
    const current = kept.filter((v) => options.includes(v));
    picked[name] = current;
    return { name, options, current, hidden: kept.filter((v) => !options.includes(v)) };
  });
  return { rows, picked };
}

/**
 * @param {Record<string, readonly number[]>} grid
 * @param {readonly Ordered[]} ordered
 */
export function combinations(grid, ordered) {
  const names = Object.keys(grid);
  let n = 0;
  /**
   * @param {number} i
   * @param {Record<string, number>} picked
   */
  const walk = (i, picked) => {
    const name = names[i];
    if (name === undefined) {
      const ok = ordered.every((o) => {
        const lo = picked[o.lower];
        const hi = picked[o.upper];
        return lo === undefined || hi === undefined || holds(o, lo, hi);
      });
      n += ok ? 1 : 0;
      return;
    }
    for (const v of grid[name] ?? []) walk(i + 1, { ...picked, [name]: v });
  };
  walk(0, {});
  return names.length ? n : 1;
}

/** @type {WeakMap<EventTarget, Record<string, Handler>>} */
const handlers = new WeakMap();
/** @type {WeakMap<EventTarget, Set<string>>} */
const bound = new WeakMap();

/** @param {Event} e */
const dispatch = (e) => {
  if (e.currentTarget) handlers.get(e.currentTarget)?.[e.type]?.(e);
};

/**
 * @param {Element} el
 * @param {string} type
 */
const bind = (el, type) => {
  const types = bound.get(el) ?? new Set();
  bound.set(el, types);
  if (!types.has(type)) {
    types.add(type);
    el.addEventListener(type, dispatch);
  }
};

/**
 * @param {readonly Child[]} children
 * @returns {(Node | string)[]}
 */
const flatten = (children) =>
  children.flatMap((c) =>
    Array.isArray(c) ? flatten(c) : c instanceof Node || (typeof c === "string" && c) ? [c] : typeof c === "number" ? [String(c)] : [],
  );

/**
 * @template {keyof HTMLElementTagNameMap} K
 * @param {K} tag
 * @param {Record<string, Attr>} [attrs]
 * @param {...Child} children
 * @returns {HTMLElementTagNameMap[K]}
 */
const h = (tag, attrs = {}, ...children) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (typeof v === "function") {
      const type = k.slice(2);
      handlers.set(el, { ...handlers.get(el), [type]: v });
      bind(el, type);
    } else if (k === "className") el.className = String(v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  el.append(...flatten(children));
  return el;
};

/**
 * @param {ChildNode} from
 * @param {ChildNode} to
 */
function morph(from, to) {
  if (from === to) return;
  if (from.nodeType !== to.nodeType || from.nodeName !== to.nodeName) {
    from.replaceWith(to);
    return;
  }
  if (!(from instanceof Element) || !(to instanceof Element)) {
    if (from.textContent !== to.textContent) from.textContent = to.textContent;
    return;
  }
  for (const a of [...from.attributes]) if (!to.hasAttribute(a.name)) from.removeAttribute(a.name);
  for (const a of [...to.attributes]) if (from.getAttribute(a.name) !== a.value) from.setAttribute(a.name, a.value);
  const on = handlers.get(to);
  if (on) {
    handlers.set(from, on);
    for (const type of Object.keys(on)) bind(from, type);
  } else handlers.delete(from);
  morphChildren(from, [...to.childNodes]);
  if (from instanceof HTMLInputElement && to instanceof HTMLInputElement) {
    if (from.type === "checkbox") from.checked = to.checked;
    else if (from.value !== to.value) from.value = to.value;
  } else if (from instanceof HTMLSelectElement && to instanceof HTMLSelectElement && from.value !== to.value) from.value = to.value;
  else if (from instanceof HTMLDetailsElement && to instanceof HTMLDetailsElement && from.open !== to.open) from.open = to.open;
}

/**
 * @param {Element} parent
 * @param {readonly ChildNode[]} next
 */
function morphChildren(parent, next) {
  const current = [...parent.childNodes];
  next.forEach((node, i) => {
    const old = current[i];
    if (old) morph(old, node);
    else parent.append(node);
  });
  current.slice(next.length).forEach((node) => node.remove());
}

/**
 * @param {...Child} parts
 * @returns {ChildNode[]}
 */
const nodes = (...parts) => flatten(parts).filter((p) => p instanceof Element);

/** @param {Event} e */
const target = (e) => /** @type {HTMLInputElement} */ (e.currentTarget);

/**
 * @param {string} title
 * @param {Child} control
 * @param {string} [hint]
 */
const field = (title, control, hint) =>
  h("label", { className: "tf-field" }, h("span", {}, title, hint && h("em", {}, ` · ${hint}`)), control);

/**
 * @template V
 * @param {readonly (readonly [V, string])[]} options
 * @param {unknown} value
 * @param {(v: V) => void} onchange
 */
const select = (options, value, onchange) =>
  h(
    "select",
    {
      className: "tf-input",
      onchange: (e) => {
        const picked = options.find(([v]) => String(v) === target(e).value);
        if (picked) onchange(picked[0]);
      },
    },
    options.map(([v, text]) => {
      const o = h("option", { value: String(v) }, text);
      o.selected = String(v) === String(value);
      return o;
    }),
  );

/**
 * @param {string} title
 * @param {boolean} checked
 * @param {(v: boolean) => void} onchange
 * @param {boolean} [disabled]
 */
const toggle = (title, checked, onchange, disabled = false) => {
  const box = h("input", { type: "checkbox", disabled, onchange: (e) => onchange(target(e).checked) });
  box.checked = checked;
  return h("label", { className: "tf-toggle" }, box, h("span", {}, title));
};

/**
 * @param {Json | undefined} value
 * @param {{ min?: number | null, max?: number | null, step?: number | null }} limits
 * @param {(v: number) => void} oninput
 */
const number = (value, { min, max, step }, oninput) =>
  h("input", {
    className: "tf-input",
    type: "number",
    value: String(value ?? ""),
    min,
    max,
    step: step ?? "any",
    oninput: (e) => target(e).value !== "" && oninput(Number(target(e).value)),
  });

/** @param {Child[]} items */
const grid = (items) => h("div", { className: "tf-grid" }, items);

/**
 * @template V
 * @param {readonly (readonly [V, string])[]} options
 * @param {unknown} value
 * @param {(v: V) => void} onpick
 */
const segmented = (options, value, onpick) =>
  h(
    "div",
    { className: "tf-seg" },
    options.map(([v, text]) =>
      h("button", { type: "button", className: String(v) === String(value) ? "on" : "", onclick: () => onpick(v) }, text),
    ),
  );

/**
 * @param {readonly number[]} options
 * @param {readonly number[]} chosen
 * @param {(v: number[]) => void} onchange
 */
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

/** @param {unknown} e */
const reason = (e) => (e instanceof Error ? e.message : String(e));

/**
 * @param {string} url
 * @param {RequestInit} [init]
 */
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

/**
 * @param {string} url
 * @param {unknown} [body]
 */
const post = (url, body) =>
  fetchJson(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body ?? {}) });

/** @param {string} api */
export const client = (api) => ({
  /** @returns {Promise<Catalog>} */
  catalog: () => fetchJson(`${api}/catalog`),
  /** @returns {Promise<Record<string, StrategyInfo>>} */
  strategies: () => fetchJson(`${api}/strategies`),
  /**
   * @param {string} name
   * @param {string} ticker
   * @param {string} end
   * @returns {Promise<StrategySpace>}
   */
  space: (name, ticker, end) => fetchJson(`${api}/strategies/${name}/space?ticker=${encodeURIComponent(ticker)}&end=${end}`),
  /**
   * @param {BasketQuery} query
   * @returns {Promise<Basket>}
   */
  basket: (query) => post(`${api}/universes/basket`, query),
  /** @returns {Promise<SweepStatus[]>} */
  sweeps: () => fetchJson(`${api}/sweeps`),
  /** @returns {Promise<string[]>} */
  ledgers: () => fetchJson(`${api}/ledgers`),
  /** @param {string} key */
  stop: (key) => post(`${api}/sweeps/${key}/stop`),
});

/** @typedef {ReturnType<typeof client>} Client */

/**
 * @template V
 * @param {Ui} ui
 * @param {string} key
 * @param {string} title
 * @param {readonly (readonly [V, string])[]} options
 * @param {readonly V[]} chosen
 * @param {(v: V[]) => void} onchange
 * @param {{ placeholder: string, searchable?: boolean }} opts
 */
function multi(ui, key, title, options, chosen, onchange, { placeholder, searchable = false }) {
  const summary = chosen.length === 0 ? placeholder : chosen.length <= 2 ? chosen.join(", ") : `${chosen.length} selected`;
  const details = h(
    "details",
    { className: "tf-multi", ontoggle: (e) => (/** @type {HTMLDetailsElement} */ (e.currentTarget).open ? ui.menus.add(key) : ui.menus.delete(key)) },
    h("summary", { className: "tf-input" }, summary),
  );
  details.open = ui.menus.has(key);
  const list = h("div", { className: "tf-multi-list" });
  /** @param {string} filter */
  const items = (filter) =>
    options
      .filter(([, text]) => !filter || text.toLowerCase().includes(filter.toLowerCase()))
      .map(([v, text]) => {
        const box = h("input", {
          type: "checkbox",
          onchange: (e) => onchange(target(e).checked ? [...chosen, v] : chosen.filter((x) => x !== v)),
        });
        box.checked = chosen.includes(v);
        return h("label", { className: "tf-multi-item" }, box, h("span", {}, text));
      });
  list.append(...items(ui.searches.get(key) ?? ""));
  /** @type {HTMLElement[]} */
  const tools = [];
  if (searchable)
    tools.push(
      h("input", {
        className: "tf-input tf-search",
        placeholder: "Search",
        value: ui.searches.get(key) ?? "",
        oninput: (e) => {
          ui.searches.set(key, target(e).value);
          morphChildren(list, items(target(e).value));
        },
      }),
    );
  if (chosen.length) tools.push(h("button", { type: "button", className: "tf-clear", onclick: () => onchange([]) }, "Clear"));
  details.append(h("div", { className: "tf-multi-body" }, tools, list));
  return field(title, details);
}

/**
 * @param {Range} state
 * @param {string} first
 * @param {string} last
 * @param {() => void} redraw
 */
function dates(state, first, last, redraw) {
  const found = span(state, first, last);
  Object.assign(state, { preset: found.preset, start: found.start, end: found.end });
  /**
   * @param {"start" | "end"} key
   * @param {string} lo
   * @param {string} hi
   */
  const input = (key, lo, hi) =>
    h("input", {
      className: "tf-input",
      type: "date",
      value: found[key],
      min: lo,
      max: hi,
      onchange: (e) => {
        if (!target(e).value) return;
        state[key] = target(e).value;
        state.preset = null;
        redraw();
      },
    });
  return {
    span: found,
    parts: [
      segmented(found.offered.map((n) => [n, n]), found.preset, (n) => ((state.preset = n), redraw())),
      grid([field("From", input("start", first, found.end)), field("To", input("end", found.start, last))]),
    ],
  };
}

/**
 * @param {Params} params
 * @param {string} name
 * @param {FieldSchema} prop
 * @param {() => void} redraw
 */
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

/**
 * @param {{ execution: Execution }} state
 * @param {boolean} allowed
 * @param {Ui} ui
 * @param {() => void} redraw
 * @param {boolean} [marginable]
 */
function execution(state, allowed, ui, redraw, marginable = false) {
  const e = state.execution;
  if (!allowed) e.intraday = false;
  if (!marginable) e.margin = false;
  /** @param {"commission" | "slippage" | "intraday" | "minute_fills" | "margin"} k */
  const set = (k) => /** @param {boolean} v */ (v) => ((e[k] = v), redraw());
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
    toggle("Fill on minute bars", Boolean(e.minute_fills || e.margin), set("minute_fills"), Boolean(e.margin)),
    marginable && toggle("Shared margin account", Boolean(e.margin), set("margin")),
    e.margin &&
      grid([
        field("Leverage", number(e.leverage ?? 30, { min: 1.5, max: 100, step: 1 }, (v) => (e.leverage = v))),
        field("Maintenance (share of initial)", number(e.maintenance ?? 0.5, { min: 0.05, max: 1, step: 0.05 }, (v) => (e.maintenance = v))),
      ]),
  );
  const details = h(
    "details",
    { className: "tf-details", ontoggle: (ev) => (ui.executionOpen = /** @type {HTMLDetailsElement} */ (ev.currentTarget).open) },
    h("summary", {}, "Execution"),
    body,
  );
  details.open = ui.executionOpen;
  return details;
}

/**
 * @param {AnyModel} model
 * @param {string} text
 * @param {(() => Request) | null} request
 */
function submitButton(model, text, request) {
  const busy = model.get("busy");
  const message = model.get("message");
  const submit = () => {
    if (!request) return;
    model.set("submission", { n: (model.get("submission")?.n ?? 0) + 1, ...request() });
    model.save_changes();
  };
  return [
    h("button", { type: "button", className: "tf-run", disabled: busy || !request, onclick: submit }, busy ? "Running…" : text),
    message && h("div", { className: "tf-error" }, message),
  ];
}

/**
 * @param {Client} api
 * @param {() => void} redraw
 */
function spaceCache(api, redraw) {
  /** @type {Map<string, StrategySpace | Failure | null>} */
  const spaces = new Map();
  /**
   * @param {string} name
   * @param {string} ticker
   * @param {string} end
   */
  return (name, ticker, end) => {
    const key = `${name}|${ticker}|${end}`;
    if (!spaces.has(key)) {
      spaces.set(key, null);
      api
        .space(name, ticker, end)
        .then((found) => spaces.set(key, found))
        .catch((e) => spaces.set(key, { error: reason(e) }))
        .finally(redraw);
    }
    return spaces.get(key) ?? null;
  };
}

/**
 * @typedef Context
 * @prop {AnyModel} model
 * @prop {Client} api
 * @prop {Catalog} catalog
 * @prop {NonEmpty<TickerInfo>} tickers
 * @prop {Record<string, StrategyInfo>} strategies
 * @prop {NonEmpty<string>} names
 * @prop {() => void} redraw
 */

/** @param {Context} ctx */
function runForm({ model, api, tickers: listed, strategies, names, redraw }) {
  const tickers = new Map(listed.map((t) => [t.ticker, t]));
  const seed = model.get("seed");
  /** @param {string | null | undefined} t */
  const known = (t) => (t && tickers.has(t) ? t : null);
  /**
   * @param {"A" | "B"} leg
   * @param {number} i
   * @returns {{ strategy: string, params: Params }}
   */
  const seeded = (leg, i) => {
    const s = seed?.legs?.[leg];
    return { strategy: s && strategies[s.strategy] ? s.strategy : (names[i % names.length] ?? names[0]), params: { ...s?.params } };
  };
  const state = {
    ticker: known(seed?.ticker) ?? known("AAPL") ?? listed[0].ticker,
    /** @type {string | null} */
    preset: seed?.start ? null : "Max",
    start: seed?.start ?? null,
    end: seed?.end ?? null,
    compare: Boolean(seed?.compare),
    legs: { A: seeded("A", 0), B: seeded("B", 1) },
    /** @type {Execution} */
    execution: { ...EXECUTION, ...seed?.execution },
  };
  /** @type {Ui} */
  const ui = { executionOpen: false, menus: new Set(), searches: new Map() };
  const space = spaceCache(api, redraw);
  /** @returns {("A" | "B")[]} */
  const legs = () => (state.compare ? ["A", "B"] : ["A"]);

  /**
   * @param {"A" | "B"} name
   * @param {string} end
   */
  const legView = (name, end) => {
    const leg = state.legs[name];
    const p = leg.params;
    const head = field(
      state.compare ? `Strategy ${name}` : "Strategy",
      select(names.map((n) => [n, strategies[n]?.title ?? n]), leg.strategy, (v) => ((leg.strategy = v), (leg.params = {}), redraw())),
    );
    const found = space(leg.strategy, state.ticker, end);
    if (found === null) return { ready: false, view: h("div", { className: "tf-leg" }, head, h("div", { className: "tf-note" }, "Loading choices…")) };
    if ("error" in found) return { ready: false, view: h("div", { className: "tf-leg" }, head, h("div", { className: "tf-error" }, found.error)) };
    const frames = found.space.timeframes;
    if (!nonEmpty(frames))
      return { ready: false, view: h("div", { className: "tf-leg" }, head, h("div", { className: "tf-note" }, "No precomputed window has enough history by the end date.")) };
    const schema = schemaOf(found);
    const tf = timeframeOf(schema, frames, p["timeframe"]);
    p["timeframe"] = tf.timeframe;
    const choices = found.space.choices[tf.timeframe] ?? {};
    const picks = windows(choices, found.space.ordered, p, schema.properties).map(([w, options]) =>
      field(schema.properties[w]?.title ?? label(w), select(options.map((o) => [o, String(o)]), p[w], (v) => ((p[w] = v), redraw()))),
    );
    const fields = fieldsOf(schema, p, new Set(["timeframe", ...Object.keys(choices)])).map(([f, prop]) => input(p, f, prop, redraw));
    const frame = tf.fixed ? null : field("Timeframe", select(frames.map((f) => [f, f]), tf.timeframe, (v) => ((p["timeframe"] = v), redraw())));
    return { ready: true, view: h("div", { className: "tf-leg" }, head, frame, grid([...picks, ...fields])) };
  };

  return () => {
    const t = tickers.get(state.ticker) ?? listed[0];
    const tickerInput = h("input", {
      className: "tf-input",
      list: "tf-tickers",
      value: state.ticker,
      spellcheck: "false",
      autocomplete: "off",
      onchange: (e) => {
        const v = target(e).value.trim().toUpperCase();
        if (tickers.has(v)) {
          state.ticker = v;
          redraw();
        } else target(e).value = state.ticker;
      },
      onfocus: (e) => target(e).select(),
    });
    const range = dates(state, t.first_session, t.last_session, redraw);
    const { start, end } = range.span;
    const views = legs().map((leg) => legView(leg, end));
    const ready = views.every((v) => v.ready) && start <= end;
    /** @returns {Omit<RunRequest, "n">} */
    const request = () => ({
      ticker: state.ticker,
      start,
      end,
      legs: Object.fromEntries(legs().map((leg) => [leg, { strategy: state.legs[leg].strategy, params: { ...state.legs[leg].params } }])),
      execution: { ...state.execution },
    });
    return nodes(
      field("Ticker", tickerInput),
      h("datalist", { id: "tf-tickers" }, [...tickers.keys()].map((v) => h("option", { value: v }))),
      range.parts,
      toggle("Compare two strategies", state.compare, (v) => ((state.compare = v), redraw())),
      views.map((v) => v.view),
      execution(state, legs().every((leg) => strategies[state.legs[leg].strategy]?.intraday), ui, redraw),
      submitButton(model, state.compare ? "Run comparison" : "Run single line", ready ? request : null),
    );
  };
}

/** @param {Context} ctx */
function simulationForm({ model, api, catalog, tickers: listed, strategies, names, redraw }) {
  const state = {
    venue: catalog.venues[0]?.venue ?? "XNYS",
    universe: "all",
    /** @type {string[]} */
    custom: [],
    /** @type {"GICS" | "SIC"} */
    scheme: "GICS",
    /** @type {string[]} */
    sectors: [],
    /** @type {string[]} */
    industries: [],
    /** @type {number[]} */
    volatility: [],
    /** @type {string[]} */
    excluded: [],
    /** @type {string | null} */
    preset: "Max",
    /** @type {string | null} */
    start: null,
    /** @type {string | null} */
    end: null,
    strategy: names[0],
    /** @type {Timeframe | null} */
    timeframe: null,
    /** @type {Record<string, number[]>} */
    grid: {},
    /** @type {Params} */
    params: {},
    /** @type {Execution} */
    execution: { ...EXECUTION },
  };
  /** @type {Ui} */
  const ui = { executionOpen: false, menus: new Set(), searches: new Map() };
  const space = spaceCache(api, redraw);
  /** @type {Basket | Failure | null} */
  let basket = null;
  let asked = "";

  /** @param {string} start */
  const ask = (start) => {
    /** @type {BasketQuery} */
    const query = {
      venue: state.venue,
      universe: state.universe,
      custom: state.custom,
      scheme: state.scheme,
      sectors: state.sectors,
      industries: state.industries,
      volatility: state.volatility,
      start,
    };
    const key = JSON.stringify(query);
    if (key === asked) return;
    asked = key;
    api
      .basket(query)
      .then((found) => {
        if (asked === key) basket = found;
      })
      .catch((e) => (basket = { error: reason(e) }))
      .finally(redraw);
  };

  const market = () => catalog.venues.find((v) => v.venue === state.venue) ?? catalog;

  /** @param {Basket} b */
  const basketView = (b) => {
    const all = listed.filter((t) => t.venue === state.venue).map((t) => t.ticker);
    const venues =
      catalog.venues.length > 1
        ? field(
            "Market",
            segmented(
              catalog.venues.map((v) => [v.venue, v.label]),
              state.venue,
              (v) => {
                Object.assign(state, { venue: v, universe: "all", custom: [], sectors: [], industries: [], volatility: [], excluded: [], start: null, end: null });
                redraw();
              },
            ),
          )
        : null;
    const parts = [
      venues,
      field(
        "Universe",
        select(b.universes.map((u) => [u.key, u.title]), state.universe, (v) => {
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
      return parts;
    }
    if (!(b.sectors ?? []).length) return parts;
    parts.push(
      field("Classification", segmented(SCHEMES.map((s) => [s, s]), state.scheme, (s) => (Object.assign(state, { scheme: s, sectors: [], industries: [] }), redraw()))),
      multi(ui, `sector:${state.scheme}`, "Sector", (b.sectors ?? []).map((s) => [s, s]), state.sectors, (v) => (Object.assign(state, { sectors: v, industries: [] }), redraw()), {
        placeholder: "All sectors",
      }),
      multi(ui, `industry:${state.scheme}`, "Industry", (b.industries ?? []).map((s) => [s, s]), state.industries, (v) => ((state.industries = v), redraw()), {
        placeholder: "All industries",
        searchable: true,
      }),
    );
    const quintiles = b.quintiles ?? [];
    if (quintiles.length)
      parts.push(
        multi(ui, "vol", "Volatility quintile", quintiles.map((q, i) => [i, q]), state.volatility, (v) => ((state.volatility = v.sort((x, y) => x - y)), redraw()), {
          placeholder: "Any volatility",
        }),
      );
    return parts;
  };

  /** @param {Basket} b */
  const chosen = (b) => {
    const out = new Set(state.excluded);
    return b.tickers.filter((t) => !out.has(t));
  };

  /**
   * @param {Basket} b
   * @param {string[]} list
   */
  const tickersView = (b, list) => {
    if (state.universe === "custom") return [];
    const details = h(
      "details",
      { className: "tf-details", ontoggle: (e) => (/** @type {HTMLDetailsElement} */ (e.currentTarget).open ? ui.menus.add("tickers") : ui.menus.delete("tickers")) },
      h("summary", {}, `Tickers · ${list.length.toLocaleString("en-US")}`),
      h(
        "div",
        { className: "tf-details-body" },
        h(
          "div",
          { className: "tf-tags" },
          b.tickers.map((t) => {
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

  /**
   * @param {Basket} b
   * @param {string[]} list
   */
  const summaryView = (b, list) => {
    const notes = [...(b.notes ?? [])];
    if (state.excluded.length) notes.push(`${state.excluded.length} left out by hand`);
    return [h("div", { className: "tf-basket" }, h("b", {}, count(list.length, "ticker")), notes.map((n) => h("div", {}, n)))];
  };

  /**
   * @param {string[]} tickers
   * @param {string} end
   * @returns {{ parts: Child[], runs: number, grid: Record<string, number[]> }}
   */
  const strategyView = (tickers, end) => {
    const head = field(
      "Strategy",
      select(names.map((n) => [n, strategies[n]?.title ?? n]), state.strategy, (v) => (Object.assign(state, { strategy: v, grid: {}, params: {}, timeframe: null }), redraw())),
    );
    const none = { runs: 0, grid: {} };
    const first = tickers[0];
    if (first === undefined) return { parts: [head], ...none };
    const found = space(state.strategy, first, end);
    if (found === null) return { parts: [head, h("div", { className: "tf-note" }, "Loading choices…")], ...none };
    if ("error" in found) return { parts: [head, h("div", { className: "tf-error" }, found.error)], ...none };
    const frames = found.space.timeframes;
    if (!nonEmpty(frames))
      return { parts: [head, h("div", { className: "tf-note" }, `No precomputed features cover ${strategies[state.strategy]?.title ?? state.strategy} for ${first}.`)], ...none };
    const schema = schemaOf(found);
    const tf = timeframeOf(schema, frames, state.timeframe);
    if (state.timeframe !== tf.timeframe) state.grid = {};
    state.timeframe = tf.timeframe;
    const choices = found.space.choices[tf.timeframe] ?? {};
    const { rows, picked } = gridOf(choices, found.space.ordered, state.grid);
    const pickers = rows.map((r) =>
      field(schema.properties[r.name]?.title ?? label(r.name), chips(r.options, r.current, (v) => ((state.grid[r.name] = [...v, ...r.hidden]), redraw()))),
    );
    const combos = combinations(picked, found.space.ordered);
    const runs = tickers.length * combos;
    const caption = h("div", { className: "tf-note" }, `${count(tickers.length, "ticker")} × ${count(combos, "valid combination")} = ${count(runs, "run")}`);
    const fields = fieldsOf(schema, state.params, new Set(["timeframe", ...Object.keys(choices)])).map(([f, prop]) => input(state.params, f, prop, redraw));
    const frame = tf.fixed ? null : field("Timeframe", select(frames.map((f) => [f, f]), tf.timeframe, (v) => (Object.assign(state, { timeframe: v, grid: {} }), redraw())));
    return { parts: [head, frame, pickers, caption, grid(fields)], runs, grid: picked };
  };

  return () => {
    const span = market();
    const range = dates(state, span.first_session, span.last_session, redraw);
    const { start, end } = range.span;
    ask(start);
    const b = basket;
    if (b === null || "error" in b) {
      const status = b === null ? h("div", { className: "tf-note" }, "Loading the universe…") : h("div", { className: "tf-error" }, b.error);
      return nodes(status, range.parts, strategyView([], end).parts, execution(state, Boolean(strategies[state.strategy]?.intraday), ui, redraw, state.venue === "FX"), submitButton(model, "Run simulation", null));
    }
    const tickers = chosen(b);
    const strategy = strategyView(tickers, end);
    const timeframe = state.timeframe;
    /** @type {(() => Omit<SweepRequest, "n">) | null} */
    const request =
      strategy.runs > 0 && start <= end && timeframe && nonEmpty(tickers)
        ? () => ({
            tickers,
            start,
            end,
            strategy: state.strategy,
            timeframe,
            grid: { ...strategy.grid },
            params: { ...state.params },
            execution: { ...state.execution },
          })
        : null;
    return nodes(
      basketView(b),
      range.parts,
      tickersView(b, tickers),
      summaryView(b, tickers),
      strategy.parts,
      execution(state, Boolean(strategies[state.strategy]?.intraday), ui, redraw, state.venue === "FX"),
      submitButton(model, "Run simulation", request),
    );
  };
}

/**
 * @param {Client} api
 * @param {Record<string, StrategyInfo>} strategies
 * @param {HTMLElement} box
 */
function background(api, strategies, box) {
  /** @type {Set<string>} */
  let stopping = new Set();
  /**
   * @param {SweepStatus[]} sweeps
   * @param {string[]} ledgers
   */
  const draw = (sweeps, ledgers) => {
    if (!sweeps.length && !ledgers.length) return morphChildren(box, []);
    morphChildren(box, [
      h("div", { className: "tf-section" }, "Background"),
      ...sweeps.map((s) => {
        const total = Math.max(1, s.total);
        const name = (s.strategy && strategies[s.strategy]?.title) ?? s.strategy ?? "";
        return h(
          "div",
          { className: "tf-job" },
          h("div", { className: "tf-bar" }, h("i", { style: `width:${(100 * s.done) / total}%` })),
          h(
            "div",
            { className: "tf-note" },
            `Simulation ${s.key.slice(0, 8)} · ${name} ${s.timeframe ?? ""} · ${count(s.tickers ?? 0, "ticker")} · ${s.done.toLocaleString("en-US")}/${total.toLocaleString("en-US")}`,
          ),
          h(
            "button",
            {
              type: "button",
              className: "tf-stop",
              disabled: stopping.has(s.key),
              onclick: () => {
                stopping.add(s.key);
                api
                  .stop(s.key)
                  .catch(() => stopping.delete(s.key))
                  .finally(tick);
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
    Promise.all([api.sweeps(), api.ledgers()])
      .then(([sweeps, ledgers]) => {
        stopping = new Set([...stopping].filter((k) => sweeps.some((s) => s.key === k)));
        draw(sweeps, ledgers);
      })
      .catch(() => {});
  tick();
  const timer = setInterval(tick, POLL_MS);
  return () => clearInterval(timer);
}

/** @type {import("@anywidget/types").AnyWidget<Model>} */
export default {
  async render({ model, el }) {
    const api = client(model.get("api"));
    const page = model.get("page");
    const root = h("div", { className: "tf" });
    const jobs = h("div", { className: "tf-jobs" });
    el.append(root, jobs);
    const links = model.get("pages");

    const brand = h(
      "div",
      { className: "tf-head" },
      h("div", { className: "tf-brand" }, h("span", { className: "word" }, "tape"), h("span", { className: "tag" }, "Equity and FX backtests")),
      h(
        "nav",
        { className: "tf-seg" },
        Object.entries(PAGES).map(([key, name]) =>
          key === page ? h("a", { className: "on", "aria-current": "page" }, name) : h("a", { href: links[key] ?? "#" }, name),
        ),
      ),
    );

    root.replaceChildren(brand, h("div", { className: "tf-note" }, "Loading…"));
    /** @param {string} text */
    const fail = (text) => root.replaceChildren(brand, h("div", { className: "tf-error" }, text));
    let catalog, strategies;
    try {
      [catalog, strategies] = await Promise.all([api.catalog(), api.strategies()]);
    } catch (e) {
      fail(`The server could not be reached: ${reason(e)}`);
      return;
    }
    const tickers = catalog.tickers;
    const names = Object.keys(strategies);
    if (!nonEmpty(tickers) || !nonEmpty(names)) {
      fail("The catalog has no tickers or no strategies yet.");
      return;
    }

    /** @type {() => ChildNode[]} */
    let view = () => [];
    const redraw = () => morphChildren(root, [brand, ...view()]);
    /** @type {Context} */
    const ctx = { model, api, catalog, tickers, strategies, names, redraw };
    view = page === "simulation" ? simulationForm(ctx) : page === "portfolios" ? () => [] : runForm(ctx);
    model.on("change:busy", redraw);
    model.on("change:message", redraw);
    redraw();
    return background(api, strategies, jobs);
  },
};
