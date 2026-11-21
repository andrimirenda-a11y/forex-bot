"""Prezzi forex da Dukascopy (banca svizzera, storico pubblico e gratuito, senza chiave).

Formato dei file (compressi LZMA, numeri big-endian):
- candele: record da 24 byte = secondi dall'inizio del periodo, open, close, low, high (interi
  in punti) e volume (float). Le candele orarie stanno in un file per MESE, quelle da 1 minuto
  in un file per GIORNO.
- tick: record da 20 byte = millisecondi dall'inizio dell'ora, ask, bid (punti), volumi. Un file per ORA.
I mesi negli indirizzi partono da 00 (gennaio = 00).

I file di periodi già conclusi non cambiano più: vengono salvati in una cache locale
(.cache/duka, conservata tra un giro e l'altro da GitHub Actions) così si scaricano una volta sola.
"""
from __future__ import annotations

import lzma
import struct
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE = "https://datafeed.dukascopy.com/datafeed"
CACHE = Path(__file__).resolve().parent.parent / ".cache" / "duka"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}

_session = requests.Session()
_session.headers.update(UA)


def point(symbol: str) -> float:
    return 1e3 if symbol.endswith("JPY") else 1e5


def _get(path: str, immutable: bool) -> bytes | None:
    """Scarica un file. None = non (ancora) disponibile. b'' = periodo senza dati."""
    cached = CACHE / path
    if immutable and cached.exists():
        return cached.read_bytes()
    last = None
    for attempt in range(4):
        try:
            r = _session.get(f"{BASE}/{path}", timeout=25)
            if r.status_code == 404:
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {r.status_code}")
            r.raise_for_status()
            data = r.content
            if immutable:
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_bytes(data)
            return data
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Dukascopy non raggiungibile per {path}: {last}")


def _decode_candles(raw: bytes, start: datetime, sym: str) -> pd.DataFrame:
    if not raw:
        return _empty()
    data = lzma.decompress(raw)
    n = len(data) // 24
    if n == 0:
        return _empty()
    arr = np.frombuffer(data[: n * 24], dtype=np.dtype([("t", ">i4"), ("o", ">i4"), ("c", ">i4"),
                                                        ("l", ">i4"), ("h", ">i4"), ("v", ">f4")]))
    p = point(sym)
    idx = pd.to_datetime(start) + pd.to_timedelta(arr["t"].astype(np.int64), unit="s")
    df = pd.DataFrame({"open": arr["o"] / p, "high": arr["h"] / p, "low": arr["l"] / p,
                       "close": arr["c"] / p, "volume": arr["v"].astype(float)}, index=idx)
    df = df[df["volume"] > 0]          # i periodi a mercato chiuso hanno volume 0
    return df.drop(columns="volume")


def _decode_ticks(raw: bytes, start: datetime, sym: str) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame(columns=["bid"], dtype=float)
    data = lzma.decompress(raw)
    n = len(data) // 20
    if n == 0:
        return pd.DataFrame(columns=["bid"], dtype=float)
    arr = np.frombuffer(data[: n * 20], dtype=np.dtype([("ms", ">i4"), ("ask", ">i4"), ("bid", ">i4"),
                                                        ("av", ">f4"), ("bv", ">f4")]))
    idx = pd.to_datetime(start) + pd.to_timedelta(arr["ms"].astype(np.int64), unit="ms")
    return pd.DataFrame({"bid": arr["bid"] / point(sym)}, index=idx)


def _empty():
    return pd.DataFrame(columns=["open", "high", "low", "close"], dtype=float,
                        index=pd.DatetimeIndex([], tz="UTC"))


def month_h1(sym: str, y: int, m: int, now: datetime) -> pd.DataFrame | None:
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    nxt = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=timezone.utc)
    if nxt > now:
        return None   # mese in corso: il file mensile non esiste ancora
    raw = _get(f"{sym}/{y:04d}/{m - 1:02d}/BID_candles_hour_1.bi5", immutable=True)
    return None if raw is None else _decode_candles(raw, start, sym)


def day_m1(sym: str, d: datetime, now: datetime) -> pd.DataFrame | None:
    start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    if start + timedelta(days=1) > now:
        return None
    if start.weekday() == 5:      # sabato: mercato chiuso
        return _empty()
    raw = _get(f"{sym}/{d.year:04d}/{d.month - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5", immutable=True)
    return None if raw is None else _decode_candles(raw, start, sym)


def hour_ticks(sym: str, h: datetime, now: datetime) -> pd.DataFrame | None:
    start = h.replace(minute=0, second=0, microsecond=0)
    if start + timedelta(hours=1) > now:
        return None
    if start.weekday() == 5 or (start.weekday() == 4 and start.hour >= 22) or (start.weekday() == 6 and start.hour < 21):
        return pd.DataFrame(columns=["bid"], dtype=float)
    # gli ultimi file possono comparire con qualche minuto di ritardo: non li mettiamo in cache se vuoti
    raw = _get(f"{sym}/{start.year:04d}/{start.month - 1:02d}/{start.day:02d}/{start.hour:02d}h_ticks.bi5",
               immutable=(now - start) > timedelta(hours=3))
    return None if raw is None else _decode_ticks(raw, start, sym)


def _ticks_to_ohlc(ticks: pd.DataFrame, rule: str) -> pd.DataFrame:
    if ticks.empty:
        return _empty()
    o = ticks["bid"].resample(rule, label="left", closed="left").ohlc().dropna()
    return o


def candles(sym: str, interval: str = "1h", days: int = 180, now: datetime | None = None) -> pd.DataFrame:
    """Candele OHLC (prezzo bid) indicizzate per ora di APERTURA UTC.
    interval: '1h' o '15min'. Include solo candele di ore già concluse e pubblicate."""
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    parts = []
    # 1) mesi completi (solo per l'orario)
    cur = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    month_cover_end = None
    if interval == "1h":
        while True:
            df = month_h1(sym, cur.year, cur.month, now)
            if df is None:
                break
            parts.append(df)
            cur = datetime(cur.year + (cur.month == 12), cur.month % 12 + 1, 1, tzinfo=timezone.utc)
            month_cover_end = cur
        day = month_cover_end or datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    else:
        day = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    # 2) giorni completi del mese in corso (candele da 1 minuto)
    rule = "1h" if interval == "1h" else "15min"
    while True:
        df = day_m1(sym, day, now)
        if df is None:
            break
        if not df.empty:
            parts.append(df.resample(rule, label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna())
        day += timedelta(days=1)
    # 3) ore complete di oggi (tick)
    h = day
    ticks = []
    while True:
        t = hour_ticks(sym, h, now)
        if t is None:
            break
        if not t.empty:
            ticks.append(t)
        h += timedelta(hours=1)
    if ticks:
        parts.append(_ticks_to_ohlc(pd.concat(ticks), rule))
    if not parts:
        raise RuntimeError(f"nessun dato Dukascopy per {sym}")
    out = pd.concat(parts).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out.index = out.index.tz_convert("UTC") if out.index.tz is not None else out.index.tz_localize("UTC")
    out.index.name = "time"
    return out[out.index >= pd.Timestamp(start)][["open", "high", "low", "close"]].astype(float)
