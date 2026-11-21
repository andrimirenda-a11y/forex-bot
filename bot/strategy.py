"""Strategia: trend sul 4 ore + ingresso sul ritracciamento a 1 ora.

Logica (per un acquisto; la vendita è speculare):
1. TREND 4H: EMA50 > EMA200, prezzo sopra EMA50, ADX abbastanza alto (trend vero, non laterale).
2. RITRACCIAMENTO 1H: nelle ultime ore il prezzo è tornato vicino alla EMA50 oraria.
3. RIPARTENZA: l'RSI era sceso (ritracciamento) e ora risale sopra 50, candela verde
   che chiude sopra la EMA20 → il trend sta ripartendo.
4. ORARIO: solo durante Londra / New York, quando il mercato è liquido.
5. STOP sotto il minimo recente (limitato tra 1 e 3 ATR), TAKE PROFIT = rr x stop.

Ogni segnale riceve un punteggio 0-100: passa solo sopra score_min.
La funzione `build` calcola tutto senza guardare il futuro, quindi è la stessa
usata sia dal bot dal vivo (ultima candela chiusa) sia dal backtest.
"""
import numpy as np
import pandas as pd

from . import indicators as ind


def build(h1: pd.DataFrame, params: dict) -> pd.DataFrame:
    """h1: DataFrame indicizzato per ora di APERTURA (UTC) con open/high/low/close.
    Restituisce h1 con colonne: signal (1/-1/0), entry, sl, tp, score, atr, reason."""
    p = params
    df = h1.copy()
    df["ema20"] = ind.ema(df["close"], 20)
    df["ema_pb"] = ind.ema(df["close"], p["ema_pullback"])
    df["ema200"] = ind.ema(df["close"], 200)
    df["rsi"] = ind.rsi(df["close"], 14)
    df["atr"] = ind.atr(df, 14)

    # --- 4 ore, allineato SOLO su candele già chiuse ---
    h4 = ind.resample_h4(h1)
    h4["ef"] = ind.ema(h4["close"], p["ema_fast_h4"])
    h4["es"] = ind.ema(h4["close"], p["ema_slow_h4"])
    h4["adx"] = ind.adx(h4, 14)
    h4["atr4"] = ind.atr(h4, 14)
    h4 = h4.dropna()
    h4_end = h4.copy()
    h4_end.index = h4_end.index + pd.Timedelta(hours=4)      # ora di chiusura della candela 4h
    h4_end = h4_end[["close", "ef", "es", "adx", "atr4"]].add_prefix("h4_")

    df["close_time"] = df.index + pd.Timedelta(hours=1)
    df = pd.merge_asof(
        df.reset_index().rename(columns={df.index.name or "index": "time"}).sort_values("close_time"),
        h4_end.reset_index().rename(columns={"index": "h4_time", h4_end.index.name or "index": "h4_time"}).sort_values("h4_time"),
        left_on="close_time", right_on="h4_time", direction="backward",
    ).set_index("time")

    up_trend = (df["h4_ef"] > df["h4_es"]) & (df["h4_close"] > df["h4_ef"]) & (df["h4_adx"] >= p["adx_min"])
    dn_trend = (df["h4_ef"] < df["h4_es"]) & (df["h4_close"] < df["h4_ef"]) & (df["h4_adx"] >= p["adx_min"])

    look = 5
    near_long = (df["low"].rolling(look).min() <= df["ema_pb"] + p["pullback_atr"] * df["atr"])
    near_short = (df["high"].rolling(look).max() >= df["ema_pb"] - p["pullback_atr"] * df["atr"])
    rsi_dip = df["rsi"].rolling(6).min() <= p["rsi_reset"]
    rsi_pop = df["rsi"].rolling(6).max() >= 100 - p["rsi_reset"]

    long_trigger = (df["rsi"] > 50) & (df["rsi"].shift() <= 50) & (df["close"] > df["open"]) & (df["close"] > df["ema20"]) & (df["close"] > df["ema_pb"])
    short_trigger = (df["rsi"] < 50) & (df["rsi"].shift() >= 50) & (df["close"] < df["open"]) & (df["close"] < df["ema20"]) & (df["close"] < df["ema_pb"])

    hour = df["close_time"].dt.hour
    session = (hour >= p["session_start"]) & (hour < p["session_end"]) & (df["close_time"].dt.day_of_week < 5)

    long_ok = up_trend & near_long & rsi_dip & long_trigger & session
    short_ok = dn_trend & near_short & rsi_pop & short_trigger & session

    swing_low = df["low"].rolling(10).min() - 0.2 * df["atr"]
    swing_high = df["high"].rolling(10).max() + 0.2 * df["atr"]
    dist_long = (df["close"] - swing_low).clip(lower=p["sl_atr_min"] * df["atr"])
    dist_short = (swing_high - df["close"]).clip(lower=p["sl_atr_min"] * df["atr"])
    too_wide_long = dist_long > p["sl_atr_max"] * df["atr"]
    too_wide_short = dist_short > p["sl_atr_max"] * df["atr"]
    long_ok &= ~too_wide_long
    short_ok &= ~too_wide_short

    # --- punteggio 0-100 ---
    adx_pts = ((df["h4_adx"] - p["adx_min"]) / 20).clip(0, 1) * 20
    sep = ((df["h4_ef"] - df["h4_es"]).abs() / df["h4_atr4"]).clip(0, 3) / 3 * 15
    mom_long = ((df["rsi"] - 50) / 15).clip(0, 1) * 15
    mom_short = ((50 - df["rsi"]) / 15).clip(0, 1) * 15
    align_long = np.where(df["ema_pb"] > df["ema200"], 10, 0)
    align_short = np.where(df["ema_pb"] < df["ema200"], 10, 0)
    score_long = 40 + adx_pts + sep + mom_long + align_long
    score_short = 40 + adx_pts + sep + mom_short + align_short

    df["signal"] = 0
    df.loc[long_ok & (score_long >= p["score_min"]), "signal"] = 1
    df.loc[short_ok & (score_short >= p["score_min"]), "signal"] = -1
    df["score"] = np.where(df["signal"] == 1, score_long, np.where(df["signal"] == -1, score_short, 0)).round(0)
    df["entry"] = df["close"]
    df["sl"] = np.where(df["signal"] == 1, df["close"] - dist_long, np.where(df["signal"] == -1, df["close"] + dist_short, np.nan))
    risk = (df["entry"] - df["sl"]).abs()
    df["tp"] = np.where(df["signal"] == 1, df["entry"] + p["rr"] * risk, np.where(df["signal"] == -1, df["entry"] - p["rr"] * risk, np.nan))
    df["adx_h4"] = df["h4_adx"]
    return df


def describe(row: pd.Series, side: int) -> str:
    """Spiegazione in italiano del perché c'è il segnale."""
    trend = "rialzista" if side == 1 else "ribassista"
    return (
        f"Trend {trend} sul 4 ore (ADX {row['adx_h4']:.0f}), ritracciamento sulla media a 1 ora "
        f"e ripartenza confermata (RSI {row['rsi']:.0f})."
    )
