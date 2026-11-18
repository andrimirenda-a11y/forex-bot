"""Backtest della strategia: simula i trade candela per candela sui dati storici.

Regole (prudenti, per non illudersi):
- una sola posizione alla volta per coppia; i segnali mentre si è dentro vengono ignorati;
- ingresso alla chiusura della candela del segnale, pagando lo spread (metà in entrata,
  metà in uscita = spread intero per ogni trade);
- dalle candele successive si controllano stop e target con massimi/minimi;
  se nella stessa candela vengono toccati entrambi si assume lo STOP;
- se la candela apre già oltre lo stop (gap) si esce all'apertura, che è peggio;
- pareggio: quando il prezzo arriva a +breakeven_r volte il rischio, lo stop va
  al prezzo di ingresso (valido dalla candela successiva);
- dopo 120 candele il trade ancora aperto si chiude alla chiusura (stop a tempo).

I risultati sono in multipli di R: guadagno / rischio iniziale, spread incluso.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import PAIRS

MAX_BARS = 120          # stop a tempo (candele da 1 ora)


def _iso(ts) -> str:
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC")
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_ts(x):
    if x is None:
        return None
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t


def simulate(df_built: pd.DataFrame, pair: str, params: dict, start=None, end=None,
             max_bars: int = MAX_BARS) -> list[dict]:
    """Simula i trade sui segnali di `strategy.build`.

    df_built: output di build() (indice = ora di apertura UTC, colonne open/high/low/close,
    signal, entry, sl, score). Il take profit viene ricalcolato da params["rr"], così la
    calibrazione può provare diversi rr senza rifare build().
    Si considerano solo i segnali con ora di apertura della candela in [start, end).
    I trade che non si chiudono entro la fine dei dati vengono scartati (incompleti).
    """
    if df_built is None or len(df_built) == 0:
        return []
    pip = PAIRS[pair]["pip"]
    spread = PAIRS[pair]["spread"] * pip
    half = spread / 2.0
    rr = float(params.get("rr", 2.0))
    be_r = params.get("breakeven_r")
    be_r = float(be_r) if be_r else 0.0
    score_min = params.get("score_min")

    idx = df_built.index
    o = df_built["open"].to_numpy(float)
    h = df_built["high"].to_numpy(float)
    lo = df_built["low"].to_numpy(float)
    c = df_built["close"].to_numpy(float)
    sig = df_built["signal"].to_numpy()
    ent = df_built["entry"].to_numpy(float)
    slv = df_built["sl"].to_numpy(float)

    mask = sig != 0
    if score_min is not None and "score" in df_built:
        mask &= df_built["score"].to_numpy(float) >= float(score_min)
    st, en = _to_ts(start), _to_ts(end)
    if st is not None:
        mask &= np.asarray(idx >= st)
    if en is not None:
        mask &= np.asarray(idx < en)
    cand = np.flatnonzero(mask)
    n = len(df_built)

    trades: list[dict] = []
    free_from = 0          # primo indice di candela in cui si può aprire un nuovo trade
    for i in cand:
        if i < free_from or i >= n - 1:
            continue
        side = int(sig[i])
        entry = ent[i]
        sl0 = slv[i]
        risk = abs(entry - sl0)
        if not (risk > 0) or math.isnan(risk):
            continue
        tp = entry + side * rr * risk
        sl = sl0
        be_level = entry + side * be_r * risk if be_r > 0 else None
        be_moved = False
        exit_px = None
        result = None
        last = min(n - 1, i + max_bars)
        j = i + 1
        while j <= last:
            if side == 1:
                if o[j] <= sl:                       # gap oltre lo stop
                    exit_px, result = o[j], "SL"
                elif lo[j] <= sl:
                    exit_px, result = sl, "SL"
                elif h[j] >= tp:
                    exit_px, result = tp, "TP"
            else:
                if o[j] >= sl:
                    exit_px, result = o[j], "SL"
                elif h[j] >= sl:
                    exit_px, result = sl, "SL"
                elif lo[j] <= tp:
                    exit_px, result = tp, "TP"
            if result is not None:
                if result == "SL" and be_moved:
                    result = "BE"
                break
            if be_level is not None and not be_moved:
                if (side == 1 and h[j] >= be_level) or (side == -1 and lo[j] <= be_level):
                    sl = entry
                    be_moved = True
            if j == i + max_bars:
                exit_px, result = c[j], "TIME"
                break
            j += 1
        if result is None:          # dati finiti con il trade ancora aperto: scartato
            break
        entry_fill = entry + side * half
        exit_fill = exit_px - side * half
        r = (exit_fill - entry_fill) * side / risk
        trades.append({
            "pair": pair,
            "side": "BUY" if side == 1 else "SELL",
            "entry_time": _iso(idx[i] + pd.Timedelta(hours=1)),   # ingresso alla chiusura
            "exit_time": _iso(idx[j] + pd.Timedelta(hours=1)),   # chiusura della candela di uscita
            "entry": float(entry),
            "exit": float(exit_px),
            "sl": float(sl0),
            "tp": float(tp),
            "r": round(float(r), 4),
            "result": result,
        })
        # la candela di uscita può già dare un nuovo segnale alla sua chiusura
        free_from = j
    return trades


def stats(trades: list[dict]) -> dict:
    """Statistiche nel formato STATS di SCHEMA.md."""
    rs = np.array([t["r"] for t in trades], dtype=float)
    n = len(rs)
    if n == 0:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "net_r": 0.0,
                "expectancy_r": 0.0, "max_dd_r": 0.0, "avg_win_r": 0.0, "avg_loss_r": 0.0}
    wins = rs[rs > 0]
    losses = rs[rs <= 0]
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())
    if gross_loss > 0:
        pf = min(gross_win / gross_loss, 99.0)
    else:
        pf = 99.0 if gross_win > 0 else 0.0
    cum = np.concatenate([[0.0], np.cumsum(rs)])
    dd = float((np.maximum.accumulate(cum) - cum).max())
    return {
        "trades": int(n),
        "win_rate": round(100.0 * len(wins) / n, 1),
        "profit_factor": round(pf, 2),
        "net_r": round(float(rs.sum()), 2),
        "expectancy_r": round(float(rs.mean()), 3),
        "max_dd_r": round(dd, 2),
        "avg_win_r": round(float(wins.mean()), 2) if len(wins) else 0.0,
        "avg_loss_r": round(float(losses.mean()), 2) if len(losses) else 0.0,
    }
