"""Strategia IN PROVA: breakout di Londra con filtro di trend giornaliero.

Regole (orari di Londra, con ora legale):
1. RANGE NOTTURNO: massimo e minimo delle candele orarie dalle 00:00 alle 06:59
   (servono almeno 6 candele su 7).
2. FILTRO AMPIEZZA: se il range è più largo di metà del movimento medio giornaliero
   (ATR giornaliero a 14 giorni) il giorno si salta: la notte si è già mossa troppo.
3. TREND DEL GIORNO: segno di (chiusura di ieri - media delle ultime 20 chiusure giornaliere).
4. SEGNALE: la PRIMA candela oraria tra le 07:00 e le 10:59 che chiude fuori dal range.
   Se esce nel verso del trend si entra all'apertura successiva; se esce contro trend
   quel giorno non si fa nulla. Massimo un'operazione al giorno per coppia.
5. STOP dall'altro lato del range, TARGET a 3 volte il rischio, uscita a tempo alle 20:00 di Londra.

Testata sugli ultimi 2 anni (7 coppie): periodo di sviluppo PF 1.00, verifica PF 1.31.
NON è un vantaggio dimostrato: per questo gira solo sul conto virtuale.
"""
import numpy as np
import pandas as pd

TZ = "Europe/London"
RANGE_START, RANGE_END, TRADE_END, EXIT_HOUR = 0, 7, 11, 20
WMAX = 0.5
TP_R = 3.0
NAME = "Breakout di Londra (in prova)"


def build(h1: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """h1: candele orarie indicizzate per ora di APERTURA (UTC). Restituisce h1 con le colonne
    signal, entry, sl, tp, score, exit_at, range_hi, range_lo, dtrend (nessuno sguardo al futuro)."""
    df = h1.copy()
    # giornaliero UTC: solo giorni già chiusi
    d = df.resample("1D").agg({"high": "max", "low": "min", "close": "last"}).dropna()
    dpc = d["close"].shift()
    dtr = pd.concat([d["high"] - d["low"], (d["high"] - dpc).abs(), (d["low"] - dpc).abs()], axis=1).max(axis=1)
    datr = dtr.rolling(14).mean().shift(1)
    dtrend = np.sign(d["close"] - d["close"].rolling(20).mean()).shift(1)
    dstrength = ((d["close"] - d["close"].rolling(20).mean()).abs() / dtr.rolling(14).mean()).shift(1)
    key = df.index.floor("1D")
    df["datr"] = datr.reindex(key).values
    df["dtrend"] = dtrend.reindex(key).values
    df["dstrength"] = dstrength.reindex(key).values   # forza del trend in "giorni di movimento medio"

    loc = df.index.tz_convert(TZ)
    day = loc.normalize().tz_localize(None)
    hour = loc.hour
    frame = pd.DataFrame({"day": day, "hour": hour}, index=df.index)
    in_rng = (hour >= RANGE_START) & (hour < RANGE_END)
    g = df[in_rng].groupby(frame["day"][in_rng]).agg(rh=("high", "max"), rl=("low", "min"), cnt=("close", "count"))
    g = g[g["cnt"] >= (RANGE_END - RANGE_START) - 1]
    df["range_hi"] = frame["day"].map(g["rh"]).values
    df["range_lo"] = frame["day"].map(g["rl"]).values

    win = (hour >= RANGE_END) & (hour < TRADE_END) & np.isfinite(df["range_hi"].values)
    brk = np.where(df["close"] > df["range_hi"], 1, np.where(df["close"] < df["range_lo"], -1, 0))
    brk = np.where(win, brk, 0)
    # prima rottura del giorno
    s = pd.Series(brk, index=df.index)
    cum = (s != 0).astype(int).groupby(frame["day"]).cumsum()
    first = (s != 0) & (cum == 1)
    width = df["range_hi"] - df["range_lo"]
    ok_w = (df["datr"] > 0) & (width <= WMAX * df["datr"])
    sig = np.where(first & ok_w & (s == df["dtrend"]), s, 0)
    df["signal"] = sig.astype(int)
    df["entry"] = df["close"]
    df["sl"] = np.where(sig == 1, df["range_lo"], np.where(sig == -1, df["range_hi"], np.nan))
    risk = (df["entry"] - df["sl"]).abs()
    df["tp"] = np.where(sig == 1, df["entry"] + TP_R * risk, np.where(sig == -1, df["entry"] - TP_R * risk, np.nan))
    df["score"] = np.where(sig != 0, 50, 0)
    # uscita a tempo: le 20:00 di Londra dello stesso giorno
    exit_local = pd.DatetimeIndex(day).tz_localize(TZ, ambiguous="NaT", nonexistent="shift_forward") + pd.Timedelta(hours=EXIT_HOUR)
    df["exit_at"] = exit_local.tz_convert("UTC")
    df["adx_h4"] = np.nan
    df["rsi"] = np.nan
    return df


def describe(row: pd.Series, side: int) -> str:
    verso = "sopra il massimo" if side == 1 else "sotto il minimo"
    trend = "in salita" if side == 1 else "in discesa"
    return (f"All'apertura di Londra il prezzo è uscito {verso} della notte e il trend dei giorni scorsi è {trend}. "
            f"Stop dall'altro lato del range notturno; se non tocca stop o obiettivo, si chiude da sola in serata.")
