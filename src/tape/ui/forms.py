from __future__ import annotations

from typing import Any

import streamlit as st


def label(value: object) -> str:
    text = str(value).replace("_", " ")
    return text[:1].upper() + text[1:]


def resolve(prop: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    ref = prop.get("$ref") or next((p.get("$ref") for p in prop.get("allOf", []) if "$ref" in p), None)
    if ref is None:
        return prop
    return {**defs[ref.rsplit("/", 1)[-1]], **{k: v for k, v in prop.items() if k not in {"$ref", "allOf"}}}


def _holds(rule: dict[str, Any], lower: int, upper: int) -> bool:
    return lower < upper if rule["strict"] else lower <= upper


def _window_options(
    name: str, options: list[int], chosen: dict[str, Any], ordered: list[dict[str, Any]]
) -> list[int]:
    for rule in ordered:
        if rule["upper"] == name and rule["lower"] in chosen:
            options = [w for w in options if _holds(rule, chosen[rule["lower"]], w)]
        if rule["lower"] == name and rule["upper"] in chosen:
            options = [w for w in options if _holds(rule, w, chosen[rule["upper"]])]
    return options


def visible(prop: dict[str, Any], chosen: dict[str, Any]) -> bool:
    return all(chosen.get(field) == value for field, value in prop.get("when", {}).items())


def scalar(col: Any, name: str, prop: dict[str, Any], key: str) -> Any:
    title = prop.get("title") or label(name)
    default = prop.get("default")
    step = prop.get("step")
    if "enum" in prop:
        options = prop["enum"]
        index = options.index(default) if default in options else 0
        return col.selectbox(title, options, index=index, format_func=label, key=key)
    if prop.get("type") == "boolean":
        return col.toggle(title, value=bool(default), key=key)
    if prop.get("type") == "integer":
        return col.number_input(
            title,
            min_value=prop.get("minimum"),
            max_value=prop.get("maximum"),
            value=int(default if default is not None else 0),
            step=int(step or 1),
            key=key,
        )
    low = prop.get("exclusiveMinimum", prop.get("minimum"))
    high = prop.get("maximum")
    return col.number_input(
        title,
        min_value=None if low is None else float(low),
        max_value=None if high is None else float(high),
        value=float(default if default is not None else 0.0),
        step=float(step) if step else None,
        format="%.0f" if step and float(step) >= 1 else "%.2f",
        key=key,
    )


def strategy(schema: dict[str, Any], space: dict[str, Any], key: str) -> dict[str, Any] | None:
    timeframes: list[str] = space["timeframes"]
    if not timeframes:
        st.warning("No precomputed features cover this strategy for this ticker.")
        return None
    defs = schema.get("$defs", {})
    props = schema["properties"]
    default_tf = props.get("timeframe", {}).get("default")
    tf = st.selectbox(
        "Timeframe",
        timeframes,
        index=timeframes.index(default_tf) if default_tf in timeframes else len(timeframes) - 1,
        key=f"{key}:timeframe",
    )
    choices: dict[str, list[int]] = space["choices"][tf]
    out: dict[str, Any] = {"timeframe": tf}
    cols = st.columns(2)
    for i, (name, domain) in enumerate(choices.items()):
        options = _window_options(name, domain, out, space["ordered"])
        if not options:
            st.warning(f"No precomputed {label(name).lower()} fits the other choices.")
            return None
        default = props[name].get("default")
        index = options.index(default) if default in options else 0
        out[name] = cols[i % 2].selectbox(
            props[name].get("title") or label(name), options, index=index, key=f"{key}:{tf}:{name}"
        )
    out.update(fields(props, defs, f"{key}", skip={"timeframe", *choices}, chosen=out))
    return out


def fields(
    props: dict[str, Any],
    defs: dict[str, Any],
    key: str,
    skip: set[str],
    chosen: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    context = dict(chosen or {})
    names = [n for n in props if n not in skip]
    cols = st.columns(2)
    shown = 0
    for name in names:
        prop = resolve(props[name], defs)
        if not visible(prop, context):
            continue
        out[name] = context[name] = scalar(cols[shown % 2], name, prop, f"{key}:{name}")
        shown += 1
    return out
