"""Download dei prezzi (Yahoo Finance, gratuito, senza chiave) e del calendario notizie."""
import time
from datetime import datetime, timezone

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]


def candles(symbol: str, interval: str = "1h", rng: str = "60d") -> pd.DataFrame:
    """Candele OHLC indicizzate per ora di apertura UTC. Ultima riga = candela in corso."""
    last_err = None
    for attempt in range(3):
        for host in HOSTS:
            try:
                r = requests.get(
                    f"{host}/v8/finance/chart/{symbol}",
                    params={"interval": interval, "range": rng, "includePrePost": "false"},
                    headers=UA, timeout=20,
                )
                r.raise_for_status()
                res = r.json()["chart"]["result"][0]
                q = res["indicators"]["quote"][0]
                df = pd.DataFrame(
                    {"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"]},
                    index=pd.to_datetime(res["timestamp"], unit="s", utc=True),
                ).dropna()
                df.index.name = "time"
                df = df[~df.index.duplicated(keep="last")].sort_index()
                if interval == "1h":
                    # Yahoo a volte allinea alle :00, a volte no: normalizziamo all'ora
                    df.index = df.index.floor("h")
                    df = df.groupby(level=0).agg({"open": "first", "high": "max", "low": "min", "close": "last"})
                if len(df) < 10:
                    raise ValueError(f"pochi dati per {symbol}")
                return df
            except Exception as e:  # noqa: BLE001
                last_err = e
        time.sleep(2 + attempt * 3)
    raise RuntimeError(f"Impossibile scaricare {symbol}: {last_err}")


def closed_only(df: pd.DataFrame, bar: pd.Timedelta, now: datetime | None = None) -> pd.DataFrame:
    """Tiene solo le candele già chiuse."""
    now = now or datetime.now(timezone.utc)
    return df[df.index + bar <= pd.Timestamp(now)]


def last_price(symbol: str) -> tuple[float, pd.Timestamp]:
    df = candles(symbol, "5m", "1d")
    return float(df["close"].iloc[-1]), df.index[-1]


def news_calendar() -> list[dict]:
    """Calendario economico della settimana (ForexFactory, feed pubblico gratuito).
    Restituisce eventi ad alto impatto: [{time: Timestamp UTC, currency, title}]"""
    urls = [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json",
    ]
    for u in urls:
        try:
            r = requests.get(u, headers=UA, timeout=20)
            r.raise_for_status()
            out = []
            for ev in r.json():
                if str(ev.get("impact", "")).lower() != "high":
                    continue
                try:
                    t = pd.Timestamp(ev["date"]).tz_convert("UTC")
                except Exception:  # noqa: BLE001
                    continue
                out.append({"time": t, "currency": ev.get("country", "").upper(), "title": ev.get("title", "")})
            return out
        except Exception:  # noqa: BLE001
            continue
    return []
