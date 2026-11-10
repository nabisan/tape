from __future__ import annotations

from typing import Any

import streamlit as st


def _resolve(prop: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    ref = prop.get("$ref") or next((p.get("$ref") for p in prop.get("allOf", []) if "$ref" in p), None)
    if ref is None:
        return prop
    return {**defs[ref.rsplit("/", 1)[-1]], **{k: v for k, v in prop.items() if k not in {"$ref", "allOf"}}}


def params(schema: dict[str, Any], key: str) -> dict[str, Any]:
    defs = schema.get("$defs", {})
    out: dict[str, Any] = {}
    cols = st.columns(2)
    for i, (name, raw) in enumerate(schema["properties"].items()):
        prop = _resolve(raw, defs)
        label = prop.get("title", name)
        col = cols[i % 2]
        wkey = f"{key}:{name}"
        default = prop.get("default")
        if "enum" in prop:
            options = prop["enum"]
            out[name] = col.selectbox(
                label, options, index=options.index(default) if default in options else 0, key=wkey
            )
        elif prop.get("type") == "boolean":
            out[name] = col.toggle(label, value=bool(default), key=wkey)
        elif prop.get("type") == "integer":
            out[name] = col.number_input(
                label,
                min_value=prop.get("minimum"),
                max_value=prop.get("maximum"),
                value=int(default if default is not None else prop.get("minimum", 0)),
                step=1,
                key=wkey,
            )
        elif prop.get("type") == "number":
            out[name] = col.number_input(
                label,
                min_value=prop.get("minimum"),
                max_value=prop.get("maximum"),
                value=float(default if default is not None else 0.0),
                key=wkey,
            )
    return out
