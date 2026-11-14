// @ts-check
/** @typedef {import("../types/api").LedgerModel} Model */
/** @typedef {import("../types/api").LedgerStatus} LedgerStatus */

const POLL_MS = 3000;

/**
 * @param {string} text
 * @param {Record<string, string>} [attrs]
 * @returns {HTMLElement}
 */
export const button = (text, attrs = {}) => {
  const el = document.createElement(attrs.href ? "a" : "button");
  el.className = "tw-file";
  el.textContent = text;
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
};

/**
 * @param {number | null | undefined} bytes
 * @returns {string}
 */
export const megabytes = (bytes) =>
  ((bytes ?? 0) / 1e6).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** @type {import("@anywidget/types").AnyWidget<Model>} */
export default {
  render({ model, el }) {
    const base = `${model.get("api")}/runs/${model.get("key")}/ledger`;
    /** @type {ReturnType<typeof setTimeout> | undefined} */
    let timer;
    let stopped = false;
    /** @param {...Node} nodes */
    const show = (...nodes) => el.replaceChildren(...nodes);
    /** @param {string} text */
    const note = (text) => {
      const div = document.createElement("div");
      div.className = "tw-note";
      div.textContent = text;
      return div;
    };
    /** @param {RequestInit} [init] @returns {Promise<LedgerStatus>} */
    const status = (init) => fetch(base, init).then((r) => r.json());
    const tick = async () => {
      if (stopped) return;
      try {
        let found = await status();
        if (found.state === "absent") found = await status({ method: "POST" });
        if (found.state === "building") {
          show(note("Building MTM ledger…"));
          timer = setTimeout(tick, POLL_MS);
        } else if (found.state === "ready") {
          show(button(`⤓ MTM ledger · ${megabytes(found.size)} MB`, { href: `${base}.xlsx`, download: "" }));
        } else {
          const retry = button("Retry MTM ledger", { type: "button" });
          retry.addEventListener("click", () => status({ method: "POST" }).finally(tick));
          show(note(`MTM ledger failed: ${found.detail}`), retry);
        }
      } catch {
        show(note("MTM ledger status unavailable; retrying."));
        timer = setTimeout(tick, POLL_MS);
      }
    };
    tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  },
};
