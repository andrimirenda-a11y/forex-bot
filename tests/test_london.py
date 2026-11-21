"""La strategia in prova gira dal vivo con i dati reali salvati: apre al segnale e chiude a fine giornata."""
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot.main as M  # noqa: E402
from bot import london  # noqa: E402
from bot.config import PAIRS  # noqa: E402

RES = Path(__file__).resolve().parent.parent / "research"


def load(pair):
    df = pd.read_csv(RES / f"h1_{pair}.csv.gz", index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    return df[["open", "high", "low", "close"]].sort_index()


def test_live_london():
    pair = "GBPUSD"
    h1_all = load(pair)
    sig = london.build(h1_all)
    t_sig = sig.index[sig["signal"] != 0][-5]          # una candela di segnale vera
    row = sig.loc[t_sig]
    tmp = Path(tempfile.mkdtemp())
    for name in ["SETTINGS_FILE", "STATE_FILE", "HISTORY_FILE", "SIGNALS_FILE", "EQUITY_FILE", "LOG_FILE",
                 "CALIBRATION_FILE", "BACKTEST_FILE"]:
        setattr(M, name, tmp / (name.lower().replace("_file", "") + ".json"))
    (tmp / "calibration.json").write_text(json.dumps(
        {"generated": "x", "pairs": {p: {"enabled": False, "note": "bocciata"} for p in PAIRS}}))
    (tmp / "settings.json").write_text(json.dumps({"coppie_attive": [pair]}))
    sent = []
    M.telegram.send = lambda chat, text: sent.append(text) or True
    M.telegram.enabled = lambda: False
    M.data.news_calendar = lambda: []
    clock = {}

    def candles(sym, interval="1h", rng="60d"):
        now = pd.Timestamp(clock["now"])
        if sym == "GBPUSD=X":
            h1 = h1_all
        else:
            h1 = pd.DataFrame({"open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1}, index=h1_all.index)
        if interval == "1h":
            return h1[h1.index < now]
        rows = []
        for t, r in h1[(h1.index < now) & (h1.index >= now - pd.Timedelta(days=3))].iterrows():
            for k in range(4):
                rows.append((t + pd.Timedelta(minutes=15 * k), r["open"], r["high"], r["low"], r["close"]))
        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"]).set_index("time")
        return df[df.index < now]

    M.data.candles = candles

    def run_at(ts):
        clock["now"] = ts
        M.now_utc = lambda: ts.to_pydatetime()
        b = M.Bot()
        b.now = ts.to_pydatetime()
        b.run(0)
        return b

    t1 = t_sig + pd.Timedelta(hours=1, minutes=5)
    b = run_at(t1)
    assert len(b.state["open_positions"]) == 1, (b.signals, b.log_items[-5:])
    pos = b.state["open_positions"][0]
    assert pos["strategy"] == "prova" and pos["exit_at"]
    assert abs(pos["sl"] - row["sl"]) < 1e-9
    assert any("strategia in prova" in s for s in sent)
    assert "Breakout di Londra" in b.state["status_line"] or "operazion" in b.state["status_line"]
    t2 = pd.Timestamp(pos["exit_at"]) + pd.Timedelta(hours=1, minutes=10)
    b = run_at(t2)
    assert not b.state["open_positions"]
    assert b.history[-1]["result"] in ("TP", "SL", "TEMPO")
    assert pd.Timestamp(b.history[-1]["closed_at"]) <= pd.Timestamp(pos["exit_at"])
    print(b.history[-1], sent[-1])
