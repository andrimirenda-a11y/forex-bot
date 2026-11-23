"""Il bot impara dai suoi errori, ma solo con le prove.

1. Ogni operazione salva il suo "contesto" (giorno, ora, quanto si era mossa la notte, forza del trend...).
2. Quando un'operazione va in stop, `diary()` spiega in parole semplici cosa era diverso dal solito.
3. Ogni settimana `weekly_lessons()` rigioca la strategia sullo storico disponibile (2 anni + dati nuovi),
   aggiunge le operazioni vere e di studio, e cerca gruppi di situazioni che perdono IN MODO COSTANTE.
   Un filtro ("lezione") si attiva solo se:
     - nello storico ci sono almeno MIN_HIST operazioni in quella situazione,
     - in media perdono (≤ MAX_MEAN_R) e in modo statisticamente chiaro (t ≤ T_MAX),
     - perdono in ENTRAMBE le metà dello storico (non è una fase passeggera),
     - dal vivo non c'è evidenza contraria.
   Le situazioni bloccate continuano a essere seguite "sulla carta" (operazioni di studio): se dal vivo
   avrebbero guadagnato, la lezione viene tolta da sola.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import london
from .config import PAIRS

MIN_HIST = 40
MAX_MEAN_R = -0.15
T_MAX = -2.5
MIN_LIVE_UNBLOCK = 15
UNBLOCK_MEAN_R = 0.3
WEEKDAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


# ------------------------------------------------------------------ contesto di un'operazione
def width_bucket(w):
    if w is None or not np.isfinite(w):
        return None
    return "notte calma" if w < 0.2 else ("notte normale" if w < 0.35 else "notte agitata")


def trend_bucket(s):
    if s is None or not np.isfinite(s):
        return None
    return "trend debole" if s < 0.5 else ("trend medio" if s < 1.5 else "trend forte")


def context(row, pair: str, entry_time) -> dict:
    """Contesto di un segnale della strategia in prova (riga di london.build)."""
    t = pd.Timestamp(entry_time).tz_convert(london.TZ)
    datr = float(row.get("datr", np.nan))
    width = float(row["range_hi"] - row["range_lo"]) / datr if datr and np.isfinite(datr) else np.nan
    strength = float(row.get("dstrength", np.nan))
    return {
        "pair": pair,
        "weekday": int(t.dayofweek),
        "hour": int(t.hour),
        "width": round(width, 3) if np.isfinite(width) else None,
        "strength": round(strength, 3) if np.isfinite(strength) else None,
    }


def buckets(ctx: dict) -> list[tuple[str, str]]:
    """Le 'situazioni' a cui appartiene un'operazione (chiave, valore leggibile)."""
    out = [("giorno", WEEKDAYS[ctx["weekday"]]), ("ora", f"ingresso alle {ctx['hour'] + 1}:00 italiane"),
           ("strumento", ctx["pair"])]
    wb, tb = width_bucket(ctx.get("width")), trend_bucket(ctx.get("strength"))
    if wb:
        out.append(("notte", wb))
    if tb:
        out.append(("trend", tb))
    if wb and tb:
        out.append(("notte+trend", f"{wb} e {tb}"))
    return out


def blocked_by(ctx: dict, lessons: dict) -> dict | None:
    active = {(f["key"], f["value"]) for f in lessons.get("filters", [])}
    for b in buckets(ctx):
        if b in active:
            return next(f for f in lessons["filters"] if (f["key"], f["value"]) == b)
    return None


# ------------------------------------------------------------------ diario degli errori
def diary(trade: dict) -> str | None:
    ctx = trade.get("ctx")
    if not ctx or trade.get("result") != "SL":
        return None
    mins = None
    try:
        mins = (pd.Timestamp(trade["closed_at"]) - pd.Timestamp(trade["opened_at"])).total_seconds() / 60
    except Exception:  # noqa: BLE001
        pass
    parts = []
    if mins is not None:
        parts.append(f"lo stop è arrivato dopo {int(mins)} minuti" if mins < 120 else f"lo stop è arrivato dopo {mins / 60:.1f} ore")
        if mins < 45:
            parts.append("il prezzo è tornato subito indietro: falsa rottura")
    wb, tb = width_bucket(ctx.get("width")), trend_bucket(ctx.get("strength"))
    if wb == "notte agitata":
        parts.append("la notte si era già mossa parecchio")
    if tb == "trend debole":
        parts.append("il trend dei giorni prima era debole")
    if ctx.get("weekday") == 4:
        parts.append("era venerdì")
    if not parts:
        return None
    return "🔎 <b>Cosa è andato storto</b>: " + "; ".join(parts) + ". Lo segno: se succede spesso, cambio le regole."


# ------------------------------------------------------------------ storico
def replay(h1: pd.DataFrame, pair: str) -> list[dict]:
    """Rigioca la strategia in prova su candele orarie: lista di operazioni con R e contesto."""
    info = PAIRS[pair]
    spread = info["spread"] * info["pip"]
    b = london.build(h1)
    idx = b.index
    out = []
    for t in idx[b["signal"].values != 0]:
        row = b.loc[t]
        s = int(row["signal"])
        i = idx.get_loc(t) + 1
        if i >= len(idx):
            break
        entry = float(b["open"].iloc[i]) + s * spread / 2
        stop = float(row["sl"])
        risk = abs(entry - stop)
        if risk <= 0 or (s == 1 and entry <= stop) or (s == -1 and entry >= stop):
            continue
        tp = entry + s * london.TP_R * risk
        px = None
        for j in range(i, len(idx)):
            if idx[j] >= row["exit_at"]:
                px = float(b["open"].iloc[j]); break
            lo, hi, op = float(b["low"].iloc[j]), float(b["high"].iloc[j]), float(b["open"].iloc[j])
            if (s == 1 and lo <= stop) or (s == -1 and hi >= stop):
                px = min(op, stop) if s == 1 else max(op, stop); break
            if (s == 1 and hi >= tp) or (s == -1 and lo <= tp):
                px = tp; break
        if px is None:
            continue
        out.append({"time": idx[i], "r": ((px - entry) * s - spread / 2) / risk, "ctx": context(row, pair, idx[i])})
    return out


def _stats(rs):
    n = len(rs)
    if n < 2:
        return n, (rs[0] if rs else 0.0), 0.0
    m = float(np.mean(rs))
    sd = float(np.std(rs, ddof=1)) or 1e-9
    return n, m, m / (sd / math.sqrt(n))


def weekly_lessons(hist_trades: list[dict], live_trades: list[dict], blocked_live: list[dict],
                   old: dict | None = None) -> tuple[dict, list[str]]:
    """Restituisce (nuove lezioni, messaggi per l'utente)."""
    old = old or {"filters": []}
    groups: dict[tuple, list] = {}
    for tr in hist_trades:
        for b in buckets(tr["ctx"]):
            groups.setdefault(b, []).append(tr)
    if hist_trades:
        mid = sorted(t["time"] for t in hist_trades)[len(hist_trades) // 2]
    live_groups: dict[tuple, list] = {}
    for tr in live_trades:
        if tr.get("ctx"):
            for b in buckets(tr["ctx"]):
                live_groups.setdefault(b, []).append(tr["r_multiple"])
    blocked_groups: dict[tuple, list] = {}
    for tr in blocked_live:
        if tr.get("ctx"):
            for b in buckets(tr["ctx"]):
                blocked_groups.setdefault(b, []).append(tr["r_multiple"])

    filters = []
    for b, trs in groups.items():
        rs = [t["r"] for t in trs]
        n, m, tstat = _stats(rs)
        if n < MIN_HIST or m > MAX_MEAN_R or tstat > T_MAX:
            continue
        first = [t["r"] for t in trs if t["time"] < mid]
        second = [t["r"] for t in trs if t["time"] >= mid]
        if not first or not second or np.mean(first) >= 0 or np.mean(second) >= 0:
            continue
        live = live_groups.get(b, [])
        if len(live) >= 8 and np.mean(live) > 0.2:
            continue                       # dal vivo va bene: niente filtro
        blk = blocked_groups.get(b, [])
        if len(blk) >= MIN_LIVE_UNBLOCK and np.mean(blk) > UNBLOCK_MEAN_R:
            continue                       # bloccate ma avrebbero guadagnato: lezione sbagliata
        filters.append({"key": b[0], "value": b[1], "n": n, "mean_r": round(m, 2), "t": round(tstat, 1),
                        "desc": f"evito {b[1]}" if b[0] != "strumento" else f"evito {b[1]}"})
    # se due lezioni coprono la stessa cosa, tengo la più forte (max 4 lezioni attive)
    filters.sort(key=lambda f: f["t"])
    filters = filters[:4]
    new = {"filters": filters}
    msgs = []
    old_keys = {(f["key"], f["value"]) for f in old.get("filters", [])}
    new_keys = {(f["key"], f["value"]) for f in filters}
    for f in filters:
        if (f["key"], f["value"]) not in old_keys:
            msgs.append(f"📚 <b>Ho imparato</b>: con {f['value']} questa strategia perde in modo costante "
                        f"({f['n']} casi nello storico, in media {f['mean_r']:+.2f}R a operazione). "
                        "D'ora in poi in quella situazione non apro, ma continuo a seguirla sulla carta per verificare.")
    for f in old.get("filters", []):
        if (f["key"], f["value"]) not in new_keys:
            msgs.append(f"📚 <b>Lezione rivista</b>: tolgo il blocco su «{f['value']}», le prove non sono più abbastanza forti.")
    return new, msgs
