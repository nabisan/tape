"""One place for how dates look everywhere: UI tables, charts, CSV and Excel.

Change these constants to switch the whole app to another format.
"""
from __future__ import annotations

import pandas as pd

DATE_FMT = "%d/%m/%Y"              # Python / pandas / Plotly
DATETIME_FMT = "%d/%m/%Y %H:%M"
DATE_JS = "DD/MM/YYYY"             # Streamlit widgets & table columns (moment.js style)
DATETIME_JS = "DD/MM/YYYY HH:mm"
DATE_XL = "dd/mm/yyyy"             # Excel number formats
DATETIME_XL = "dd/mm/yyyy hh:mm"


def is_date_only(values) -> bool:
    """True if every timestamp is at midnight (i.e. a plain date)."""
    idx = pd.DatetimeIndex(pd.Series(values).dropna())
    return len(idx) == 0 or bool((idx == idx.normalize()).all())


def fmt_date(d) -> str:
    return pd.Timestamp(d).strftime(DATE_FMT)


def _is_dt(x) -> bool:
    return pd.api.types.is_datetime64_any_dtype(x)


def for_csv(df: pd.DataFrame, index: bool = False) -> str:
    """CSV text with every date/datetime written as DD/MM/YYYY [HH:MM]."""
    df = df.copy()
    if index:
        df = df.reset_index()
    for col in df.columns:
        s = df[col]
        if _is_dt(s):
            df[col] = s.dt.strftime(DATE_FMT if is_date_only(s) else DATETIME_FMT)
        elif pd.api.types.is_timedelta64_dtype(s):
            df[col] = s.astype(str)
    return _round(df).to_csv(index=False)


def for_excel(df: pd.DataFrame) -> pd.DataFrame:
    """Keep real Excel dates (sortable); date-only values become dates so they
    pick up DATE_XL instead of DATETIME_XL."""
    df = df.copy()
    for col in df.columns:
        s = df[col]
        if _is_dt(s) and is_date_only(s):
            df[col] = s.dt.date
        elif pd.api.types.is_timedelta64_dtype(s):
            df[col] = s.astype(str)
    if _is_dt(df.index) and is_date_only(df.index):
        df.index = pd.Index(df.index.date, name=df.index.name)
    return _round(df)


def _round(df: pd.DataFrame) -> pd.DataFrame:
    num = df.select_dtypes("float").columns
    df[num] = df[num].round(4)
    return df


def style_time_axis(fig, intraday: bool) -> None:
    """Plotly x-axes: DD/MM/YYYY ticks (with HH:MM when zoomed in) and hover."""
    fig.update_xaxes(
        hoverformat=DATETIME_FMT if intraday else DATE_FMT,
        tickformatstops=[
            dict(dtickrange=[None, 86_400_000], value="%H:%M<br>" + DATE_FMT),
            dict(dtickrange=[86_400_000, None], value=DATE_FMT),
        ],
    )
