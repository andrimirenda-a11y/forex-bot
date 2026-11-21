"""Download dei prezzi e del calendario notizie.

Fonte principale: Yahoo Finance (gratuita, aggiornata ogni minuto, serve un cookie + "crumb"
come fa il sito). Se Yahoo rifiuta (es. troppe richieste), si passa in automatico a Dukascopy
(storico ufficiale di una banca svizzera, gratuito, con qualche minuto di ritardo).
"""
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from . import duka

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]

# simbolo Yahoo -> simbolo Dukascopy
YAHOO_TO_DUKA = {
    "EURUSD=X": "EURUSD", "GBPUSD=X": "GBPUSD", "EURGBP=X": "EURGBP", "JPY=X": "USDJPY",
    "CHF=X": "USDCHF", "AUDUSD=X": "AUDUSD", "CAD=X": "USDCAD", "EURJPY=X": "EURJPY",
    "EURCHF=X": "EURCHF", "EURCAD=X": "EURCAD", "EURAUD=X": "EURAUD",
}
RANGE_DAYS = {"1d": 1, "5d": 5, "60d": 60, "180d": 180, "365d": 365, "729d": 729, "730d": 730}

_yahoo = {"session": None, "crumb": None, "blocked_until": 0.0}
SOURCE_USED = {}   # simbolo -> "yahoo" | "dukascopy" (per i log)


def _yahoo_session():
    if _yahoo["session"] is None:
        s = requests.Session()
        s.headers.update(UA)
        try:
            s.get("https://fc.yahoo.com", timeout=15)          # imposta il cookie (risponde 404, è normale)
        except Exception:  # noqa: BLE001
            pass
        crumb = None
        try:
            r = s.get(f"{HOSTS[0]}/v1/test/getcrumb", timeout=15)
            if r.ok and r.text and "<" not in r.text:
                crumb = r.text.strip()
        except Exception:  # noqa: BLE001
            pass
        _yahoo["session"], _yahoo["crumb"] = s, crumb
    return _yahoo["session"], _yahoo["crumb"]


def candles(symbol: str, interval: str = "1h", rng: str = "60d") -> pd.DataFrame:
    """Candele OHLC indicizzate per ora di apertura UTC (con Yahoo l'ultima riga è la candela in corso)."""
    try:
        if time.time() < _yahoo["blocked_until"]:
            raise RuntimeError("Yahoo temporaneamente evitato dopo un rifiuto")
        df = _yahoo_candles(symbol, interval, rng)
        SOURCE_USED[symbol] = "yahoo"
        return df
    except Exception as e:  # noqa: BLE001
        if "429" in str(e):
            _yahoo["blocked_until"] = time.time() + 600   # per 10 minuti non insistere
        dsym = YAHOO_TO_DUKA.get(symbol)
        if not dsym or interval not in ("1h", "15m"):
            raise
        df = duka.candles(dsym, "1h" if interval == "1h" else "15min", days=RANGE_DAYS.get(rng, 60))
        if len(df) < 10:
            raise RuntimeError(f"Yahoo e Dukascopy senza dati per {symbol}: {e}") from e
        SOURCE_USED[symbol] = "dukascopy"
        return df


def _yahoo_candles(symbol: str, interval: str, rng: str) -> pd.DataFrame:
    last_err = None
    for attempt in range(2):
        for host in HOSTS:
            try:
                s, crumb = _yahoo_session()
                params = {"interval": interval, "range": rng, "includePrePost": "false"}
                if crumb:
                    params["crumb"] = crumb
                r = s.get(f"{host}/v8/finance/chart/{symbol}", params=params, timeout=25)
                if r.status_code == 429:
                    raise RuntimeError(f"429 Too Many Requests ({symbol})")
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
                if "429" in str(e):
                    raise
        _yahoo["session"] = None      # riprova con un cookie nuovo
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
