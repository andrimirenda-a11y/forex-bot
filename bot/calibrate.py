"""Calibrazione automatica: per ogni coppia prova varie combinazioni di parametri sul
passato e attiva solo le coppie in cui la strategia regge anche su dati "mai visti".

Uso:  python -m bot.calibrate

Come evita di illudersi (overfitting):
1. scarica ~2 anni di candele a 1 ora;
2. sceglie i parametri SOLO sul primo 70% dei dati (in-sample);
3. verifica quei parametri sull'ultimo 30% (out-of-sample), che non ha mai visto;
4. attiva la coppia solo se guadagna in entrambi i periodi.

Scrive data/calibration.json e data/backtest.json (formato in SCHEMA.md).
"""
from __future__ import annotations

import itertools
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from . import backtest, strategy
from .config import BACKTEST_FILE, CALIBRATION_FILE, DEFAULT_PARAMS, PAIRS

# --- impostazioni della calibrazione ---
GRID = {
    "adx_min": [18, 22, 26],
    "score_min": [55, 60, 70],
    "pullback_atr": [0.4, 0.6, 1.0],
    "rr": [1.5, 2.0, 2.5, 3.0],     # cambia solo il target: non serve rifare build()
}
# 730 giorni è il massimo di Yahoo per le candele a 1 ora; a volte "730d" viene rifiutato
# per arrotondamenti sull'ora di inizio, quindi si riprova con 729d prima di scendere.
RANGES = ["730d", "729d", "365d", "180d"]
IN_SAMPLE_FRAC = 0.70
MIN_BARS = 1500                     # servono ~800 candele solo per scaldare la EMA200 a 4 ore

# selezione in-sample
IS_MIN_TRADES = 15
IS_MIN_PF_SELECT = 1.1
DD_PENALTY = 0.5
# attivazione
IS_MIN_PF_ENABLE = 1.2
OOS_MIN_PF = 1.1
OOS_MIN_TRADES = 8
MAX_EQUITY_POINTS = 1500

Fetcher = Callable[[str, str], pd.DataFrame]   # (simbolo yahoo, range) -> candele 1h


def _iso(t) -> str:
    t = pd.Timestamp(t)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(x: float) -> str:
    """Numero in stile italiano (virgola decimale)."""
    return f"{x:.2f}".replace(".", ",")


def yahoo_fetch(symbol: str, rng: str) -> pd.DataFrame:
    """Downloader reale (Yahoo). Tiene solo le candele già chiuse."""
    from . import data
    df = data.candles(symbol, "1h", rng)
    return data.closed_only(df, pd.Timedelta(hours=1))


NETWORK_ERRORS = ("Max retries exceeded", "ConnectionError", "ConnectTimeout", "timed out",
                  "NameResolution", "ProxyError", "Failed to establish", "Network is unreachable")


class NetworkDown(RuntimeError):
    """Yahoo non raggiungibile (rete), inutile provare range più corti."""


def download(pair: str, fetch: Fetcher) -> tuple[pd.DataFrame, str]:
    """Prova i range dal più lungo al più corto. Solleva RuntimeError se nessuno funziona
    (NetworkDown se il problema è la rete e non il range richiesto)."""
    errors = []
    for rng in RANGES:
        try:
            df = fetch(PAIRS[pair]["yahoo"], rng)
            if df is None or len(df) == 0:
                raise ValueError("nessun dato")
            df = df[["open", "high", "low", "close"]].astype(float).dropna().sort_index()
            df = df[~df.index.duplicated(keep="last")]
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            df.index.name = "time"
            return df, rng
        except Exception as e:  # noqa: BLE001
            errors.append(f"{rng}: {e}")
            if any(k in str(e) for k in NETWORK_ERRORS):
                raise NetworkDown(f"rete non raggiungibile: {e}") from e
    raise RuntimeError("; ".join(errors))


def objective(s: dict) -> float:
    return s["net_r"] - DD_PENALTY * s["max_dd_r"]


def qualifies(s: dict) -> bool:
    return s["trades"] >= IS_MIN_TRADES and s["profit_factor"] > IS_MIN_PF_SELECT


def optimize(h1: pd.DataFrame, pair: str, base: dict | None = None, grid: dict | None = None) -> dict:
    """Grid search sul primo 70% delle candele, poi verifica sul 30% finale.
    Restituisce {params, in_sample, out_sample, oos_trades, qualified, split, tested}."""
    base = dict(base or DEFAULT_PARAMS)
    grid = grid or GRID
    split = h1.index[int(len(h1) * IN_SAMPLE_FRAC)]
    build_keys = [k for k in grid if k != "rr"]
    rr_list = grid.get("rr", [base["rr"]])

    best = None          # (qualified, score, params, stats, built)
    tested = 0
    for combo in itertools.product(*(grid[k] for k in build_keys)):
        p = dict(base)
        p.update(dict(zip(build_keys, combo)))
        built = strategy.build(h1, p)
        for rr in rr_list:
            p_rr = dict(p, rr=rr)
            trades = backtest.simulate(built, pair, p_rr, end=split)
            s = backtest.stats(trades)
            tested += 1
            q = qualifies(s)
            # ordine: prima le combinazioni valide, poi il punteggio, poi più trade
            key = (q, s["trades"] >= IS_MIN_TRADES, objective(s), s["trades"])
            if best is None or key > best[0]:
                best = (key, p_rr, s, built)

    key, params, is_stats, built = best
    oos_trades = backtest.simulate(built, pair, params, start=split)
    return {
        "params": params,
        "in_sample": is_stats,
        "out_sample": backtest.stats(oos_trades),
        "oos_trades": oos_trades,
        "qualified": bool(key[0]),
        "split": split,
        "tested": tested,
    }


def decide(res: dict) -> tuple[bool, str]:
    """Decide se attivare la coppia. Restituisce (attiva, nota in italiano)."""
    i, o = res["in_sample"], res["out_sample"]
    if not res.get("qualified", True):
        if i["trades"] < IS_MIN_TRADES:
            return False, (f"Disattivata: troppo pochi segnali nel periodo di prova "
                           f"({i['trades']} trade, ne servono almeno {IS_MIN_TRADES}).")
        return False, (f"Disattivata: nessuna combinazione di parametri guadagna nel periodo di prova "
                       f"(migliore PF {_num(i['profit_factor'])}).")
    if i["profit_factor"] < IS_MIN_PF_ENABLE:
        return False, (f"Disattivata: nel periodo di prova il vantaggio è troppo piccolo "
                       f"(PF {_num(i['profit_factor'])}, serve almeno {_num(IS_MIN_PF_ENABLE)}).")
    if o["trades"] < OOS_MIN_TRADES:
        return False, (f"Disattivata: troppo pochi trade fuori campione per fidarsi "
                       f"({o['trades']}, ne servono almeno {OOS_MIN_TRADES}).")
    if o["net_r"] <= 0:
        return False, (f"Disattivata: fuori campione perde (PF {_num(o['profit_factor'])}, "
                       f"{_num(o['net_r'])} R).")
    if o["profit_factor"] < OOS_MIN_PF:
        return False, (f"Disattivata: fuori campione guadagna troppo poco "
                       f"(PF {_num(o['profit_factor'])}, serve almeno {_num(OOS_MIN_PF)}).")
    return True, (f"Attiva: PF {_num(i['profit_factor'])} nel periodo di prova e "
                  f"{_num(o['profit_factor'])} fuori campione ({o['trades']} trade, "
                  f"+{_num(o['net_r'])} R).")


def equity_curve(trades: list[dict], start=None) -> list[dict]:
    """R cumulati ordinati per ora di uscita, al massimo MAX_EQUITY_POINTS punti."""
    trades = sorted(trades, key=lambda t: t["exit_time"])
    pts = []
    if trades:
        t0 = _iso(start) if start is not None else trades[0]["entry_time"]
        pts.append({"t": t0, "r": 0.0})
    cum = 0.0
    for t in trades:
        cum += t["r"]
        pts.append({"t": t["exit_time"], "r": round(cum, 2)})
    if len(pts) > MAX_EQUITY_POINTS:
        keep = np.unique(np.linspace(0, len(pts) - 1, MAX_EQUITY_POINTS).round().astype(int))
        pts = [pts[k] for k in keep]
    return pts


def _empty_pair(note: str) -> dict:
    return {"enabled": False, "params": dict(DEFAULT_PARAMS),
            "in_sample": backtest.stats([]), "out_sample": backtest.stats([]), "note": note}


def run(pairs: list[str] | None = None, fetch: Fetcher | None = None,
        calibration_file: Path | None = None, backtest_file: Path | None = None,
        grid: dict | None = None, verbose: bool = True, write: bool = True) -> tuple[dict, dict]:
    """Esegue la calibrazione completa. `fetch` è iniettabile (test senza internet)."""
    pairs = pairs or list(PAIRS)
    fetch = fetch or yahoo_fetch
    calibration_file = calibration_file or CALIBRATION_FILE
    backtest_file = backtest_file or BACKTEST_FILE
    say = print if verbose else (lambda *a, **k: None)
    generated = _iso(datetime.now(timezone.utc))

    cal_pairs: dict[str, dict] = {}
    per_pair: dict[str, dict] = {}
    enabled_trades: list[dict] = []
    first_split = None
    data_from, data_to = None, None

    net_fail = 0          # coppie consecutive fallite per problemi di rete
    for pair in pairs:
        t0 = time.time()
        if net_fail >= 2:
            cal_pairs[pair] = _empty_pair("Disattivata: Yahoo non raggiungibile durante la calibrazione.")
            say(f"  {pair}: saltata (Yahoo non raggiungibile)")
            continue
        try:
            if fetch is yahoo_fetch:
                time.sleep(2)          # gentilezza verso la fonte dei prezzi
            h1, rng = download(pair, fetch)
            net_fail = 0
        except Exception as e:  # noqa: BLE001
            net_fail = net_fail + 1 if isinstance(e, NetworkDown) else 0
            cal_pairs[pair] = _empty_pair(f"Disattivata: download dei prezzi non riuscito ({str(e)[:160]}).")
            say(f"  {pair}: download non riuscito -> {str(e)[:200]}")
            continue
        if len(h1) < MIN_BARS:
            cal_pairs[pair] = _empty_pair(
                f"Disattivata: dati insufficienti ({len(h1)} candele da 1 ora, ne servono almeno {MIN_BARS}).")
            say(f"  {pair}: solo {len(h1)} candele, salto")
            continue
        data_from = min(data_from, h1.index[0]) if data_from is not None else h1.index[0]
        data_to = max(data_to, h1.index[-1]) if data_to is not None else h1.index[-1]
        try:
            res = optimize(h1, pair, grid=grid)
            enabled, note = decide(res)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            cal_pairs[pair] = _empty_pair(f"Disattivata: errore durante il calcolo ({str(e)[:160]}).")
            continue
        cal_pairs[pair] = {
            "enabled": enabled,
            "params": res["params"],
            "in_sample": res["in_sample"],
            "out_sample": res["out_sample"],
            "note": note,
        }
        per_pair[pair] = res["out_sample"]
        if enabled:
            enabled_trades.extend(res["oos_trades"])
            first_split = min(first_split, res["split"]) if first_split is not None else res["split"]
        say(f"  {pair}: {len(h1)} candele ({rng}), {res['tested']} combinazioni in {time.time() - t0:.1f}s")

    enabled_trades.sort(key=lambda t: t["exit_time"])
    calibration = {
        "generated": generated,
        "data_from": _iso(data_from) if data_from is not None else None,
        "data_to": _iso(data_to) if data_to is not None else None,
        "pairs": cal_pairs,
    }
    bt = {
        "generated": generated,
        "data_from": calibration["data_from"],
        "data_to": calibration["data_to"],
        "total": backtest.stats(enabled_trades),
        "per_pair": per_pair,
        "equity_r": equity_curve(enabled_trades, first_split),
    }
    if write and data_from is None:
        # nessuna coppia scaricata (es. Yahoo giù): non sovrascrivere la calibrazione buona precedente
        say("ATTENZIONE: nessun dato scaricato, file di calibrazione NON aggiornati.")
        write = False
    if write:
        from . import storage
        storage.save(Path(calibration_file), calibration)
        storage.save(Path(backtest_file), bt)
    if verbose:
        print_summary(calibration, bt)
    return calibration, bt


def print_summary(calibration: dict, bt: dict) -> None:
    print()
    print(f"CALIBRAZIONE  dati {calibration['data_from']} -> {calibration['data_to']}")
    print("IS = periodo di prova (70%), OOS = fuori campione (30%, mai visto durante la scelta)")
    head = (f"{'Coppia':<7} {'Stato':<6} {'rr':>4} {'adx':>4} {'score':>5} {'pb':>4} | "
            f"{'IS tr':>5} {'IS PF':>6} {'IS R':>7} | {'OOS tr':>6} {'OOS PF':>6} {'OOS R':>7} {'OOS DD':>6} {'win%':>5}")
    print(head)
    print("-" * len(head))
    for pair, c in calibration["pairs"].items():
        p, i, o = c["params"], c["in_sample"], c["out_sample"]
        print(f"{pair:<7} {'ON' if c['enabled'] else 'off':<6} {p['rr']:>4} {p['adx_min']:>4} {p['score_min']:>5} "
              f"{p['pullback_atr']:>4} | {i['trades']:>5} {i['profit_factor']:>6.2f} {i['net_r']:>7.1f} | "
              f"{o['trades']:>6} {o['profit_factor']:>6.2f} {o['net_r']:>7.1f} {o['max_dd_r']:>6.1f} {o['win_rate']:>5.1f}")
    print()
    for pair, c in calibration["pairs"].items():
        print(f"  {pair}: {c['note']}")
    t = bt["total"]
    on = [k for k, v in calibration["pairs"].items() if v["enabled"]]
    print()
    print(f"Coppie attive: {', '.join(on) if on else 'nessuna'}")
    print(f"Totale fuori campione (coppie attive): {t['trades']} trade, win rate {t['win_rate']}%, "
          f"PF {t['profit_factor']}, {t['net_r']:+.1f} R, drawdown max {t['max_dd_r']} R")


def main() -> int:
    if "--if-missing" in sys.argv:
        # usato dal giro del bot: calibra solo se non c'è ancora una calibrazione valida,
        # e non più di una volta ogni 3 ore (per non martellare le fonti dei prezzi)
        import json
        from .config import DATA
        try:
            cal = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
            if cal.get("data_from"):
                print("Calibrazione già presente, niente da fare.")
                return 0
        except Exception:  # noqa: BLE001
            pass
        marker = DATA / "calibration_attempt.json"
        try:
            last = json.loads(marker.read_text(encoding="utf-8")).get("t", 0)
        except Exception:  # noqa: BLE001
            last = 0
        if time.time() - last < 3 * 3600:
            print("Ultimo tentativo di calibrazione meno di 3 ore fa: riprovo più tardi.")
            return 0
        DATA.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"t": time.time()}), encoding="utf-8")
    t0 = time.time()
    print("Calibrazione in corso (download ~2 anni di candele a 1 ora per coppia)...")
    calibration, _ = run()
    print(f"\nFatto in {time.time() - t0:.0f} secondi.")
    return 0 if calibration["data_from"] is not None else 1


if __name__ == "__main__":
    sys.exit(main())
