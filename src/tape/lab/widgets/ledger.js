const POLL_MS = 3000;

const button = (text, attrs = {}) => {
  const el = document.createElement(attrs.href ? "a" : "button");
  el.className = "tw-file";
  el.textContent = text;
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
};

export default {
  render({ model, el }) {
    const base = `${model.get("api")}/runs/${model.get("key")}/ledger`;
    let timer = 0;
    let stopped = false;
    const show = (...nodes) => el.replaceChildren(...nodes);
    const note = (text) => {
      const div = document.createElement("div");
      div.className = "tw-note";
      div.textContent = text;
      return div;
    };
    const tick = async () => {
      if (stopped) return;
      try {
        let status = await fetch(base).then((r) => r.json());
        if (status.state === "absent") status = await fetch(base, { method: "POST" }).then((r) => r.json());
        if (status.state === "building") {
          show(note("Building MTM ledger…"));
          timer = setTimeout(tick, POLL_MS);
        } else if (status.state === "ready") {
          const size = ((status.size ?? 0) / 1e6).toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
          show(button(`⤓ MTM ledger · ${size} MB`, { href: `${base}.xlsx`, download: "" }));
        } else {
          const retry = button("Retry MTM ledger", { type: "button" });
          retry.addEventListener("click", () => fetch(base, { method: "POST" }).finally(tick));
          show(note(`MTM ledger failed: ${status.detail ?? ""}`), retry);
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
