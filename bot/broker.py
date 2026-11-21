"""Conto virtuale: apre, gestisce e chiude le posizioni con prezzi reali.

Tutti i calcoli imitano un broker vero: lo spread viene pagato all'ingresso,
lo stop si sposta a pareggio quando il trade guadagna breakeven_r, e se in una
stessa candela vengono toccati sia stop sia target si assume il caso peggiore (stop).
"""
import pandas as pd

from .config import PAIRS
from .risk import pip_value_eur
from .storage import iso, parse


def _pips(pair, a, b):
    return (a - b) / PAIRS[pair]["pip"]


def open_position(state, pair, side, entry, sl, tp, sizing, score, reason, when, params):
    spread = PAIRS[pair]["spread"] * PAIRS[pair]["pip"]
    # il prezzo di ingresso include metà spread (bid/ask), l'altra metà si paga all'uscita
    fill = entry + spread / 2 if side == "BUY" else entry - spread / 2
    pos = {
        "id": f"{pair}-{pd.Timestamp(when).strftime('%Y%m%dT%H%M')}",
        "pair": pair, "side": side,
        "entry": round(fill, 6), "signal_price": round(entry, 6),
        "sl": round(sl, 6), "tp": round(tp, 6), "sl_initial": round(sl, 6),
        "lots": sizing["lots"], "risk_eur": sizing["risk_eur"],
        "pip_value_eur": sizing["pip_value_eur"], "margin_eur": sizing["margin_eur"],
        "opened_at": iso(when), "last_check": iso(when),
        "score": int(score), "reason": reason, "be_moved": False,
        "breakeven_r": params.get("breakeven_r", 1.0),
        "last_price": round(entry, 6), "unrealized_eur": 0.0, "unrealized_pips": 0.0,
    }
    state["open_positions"].append(pos)
    return pos


def _close(pos, price, when, result, eur_rates):
    pair = pos["pair"]
    spread = PAIRS[pair]["spread"] * PAIRS[pair]["pip"]
    exit_fill = price - spread / 2 if pos["side"] == "BUY" else price + spread / 2
    direction = 1 if pos["side"] == "BUY" else -1
    pips = _pips(pair, exit_fill, pos["entry"]) * direction
    pv = pip_value_eur(pair, eur_rates, pos["lots"])
    pnl = pips * pv
    risk_pips = abs(_pips(pair, pos["entry"], pos["sl_initial"]))
    return {
        "id": pos["id"], "pair": pair, "side": pos["side"],
        "entry": pos["entry"], "exit": round(exit_fill, 6),
        "sl_initial": pos["sl_initial"], "tp": pos["tp"], "lots": pos["lots"],
        "opened_at": pos["opened_at"], "closed_at": iso(when), "result": result,
        "pnl_eur": round(pnl, 2), "pnl_pips": round(pips, 1),
        "r_multiple": round(pips / risk_pips, 2) if risk_pips else 0.0,
        "score": pos["score"],
    }


def manage(state, candles_by_pair: dict, eur_rates: dict, now) -> list[dict]:
    """Controlla ogni posizione aperta sulle candele (15 min) arrivate dall'ultimo controllo.
    Restituisce la lista dei trade chiusi (e aggiorna state)."""
    closed, still_open = [], []
    for pos in state["open_positions"]:
        pair = pos["pair"]
        df = candles_by_pair.get(pair)
        if df is None or df.empty:
            still_open.append(pos)
            continue
        since = pd.Timestamp(parse(pos["last_check"]))
        opened = pd.Timestamp(parse(pos["opened_at"]))
        # candele dalla quella in cui si è entrati in poi (ricontrolliamo sempre l'ultima, che può essere in corso)
        new = df[(df.index >= since.floor("15min")) & (df.index >= opened.floor("15min"))]
        long = pos["side"] == "BUY"
        risk = abs(pos["entry"] - pos["sl_initial"])
        result = None
        exit_at = pd.Timestamp(parse(pos["exit_at"])) if pos.get("exit_at") else None
        for t, c in new.iterrows():
            if exit_at is not None and t >= exit_at:  # uscita a tempo all'apertura della candela
                result = ("TEMPO", float(c["open"]), t)
                break
            hit_sl = c["low"] <= pos["sl"] if long else c["high"] >= pos["sl"]
            hit_tp = c["high"] >= pos["tp"] if long else c["low"] <= pos["tp"]
            if hit_sl:  # caso peggiore se tocca entrambi
                result = ("BE" if pos["be_moved"] else "SL", pos["sl"], t + pd.Timedelta(minutes=15))
                break
            if hit_tp:
                result = ("TP", pos["tp"], t + pd.Timedelta(minutes=15))
                break
            # sposta lo stop a pareggio
            be = pos.get("breakeven_r") or 0
            if be and not pos["be_moved"]:
                fav = (c["high"] - pos["entry"]) if long else (pos["entry"] - c["low"])
                if fav >= be * risk:
                    pos["sl"] = pos["entry"]
                    pos["be_moved"] = True
                    pos.setdefault("events", []).append({"t": iso(t), "msg": "Stop spostato a pareggio"})
        last = float(df["close"].iloc[-1])
        if result:
            res, price, when = result
            closed.append(_close(pos, price, min(when, pd.Timestamp(now)), res, eur_rates))
        else:
            direction = 1 if long else -1
            pv = pip_value_eur(pair, eur_rates, pos["lots"])
            spread = PAIRS[pair]["spread"] * PAIRS[pair]["pip"]
            exit_now = last - spread / 2 if long else last + spread / 2
            pips = _pips(pair, exit_now, pos["entry"]) * direction
            pos["last_price"] = round(last, 6)
            pos["unrealized_pips"] = round(pips, 1)
            pos["unrealized_eur"] = round(pips * pv, 2)
            if len(df):
                pos["last_check"] = iso(df.index[-1])
            still_open.append(pos)
    state["open_positions"] = still_open
    for tr in closed:
        state["balance"] = round(state["balance"] + tr["pnl_eur"], 2)
    return closed


def close_all(state, prices: dict, eur_rates: dict, now, reason="STOP_MANUALE") -> list[dict]:
    closed = []
    for pos in state["open_positions"]:
        price = prices.get(pos["pair"], pos["last_price"])
        tr = _close(pos, price, now, reason, eur_rates)
        state["balance"] = round(state["balance"] + tr["pnl_eur"], 2)
        closed.append(tr)
    state["open_positions"] = []
    return closed


def equity(state) -> float:
    return round(state["balance"] + sum(p.get("unrealized_eur", 0) for p in state["open_positions"]), 2)


def margin_used(state) -> float:
    return round(sum(p.get("margin_eur", 0) for p in state["open_positions"]), 2)
