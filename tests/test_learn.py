"""Il bot impara solo da schemi costanti, non da una singola perdita."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import learn  # noqa: E402


def fake(n, seed=0):
    rng = np.random.default_rng(seed)
    out = []
    t0 = pd.Timestamp("2025-01-06 08:00", tz="UTC")
    for i in range(n):
        wd = i % 5
        # il venerdì perde sempre un po', gli altri giorni guadagnano in media
        r = rng.choice([-1.0, 3.0], p=[0.85, 0.15]) if wd == 4 else rng.choice([-1.0, 3.0], p=[0.6, 0.4])
        out.append({"time": t0 + pd.Timedelta(days=i), "r": float(r),
                    "ctx": {"pair": "EURUSD", "weekday": wd, "hour": 8, "width": 0.25, "strength": 1.0}})
    return out


def test_learns_consistent_pattern():
    new, msgs = learn.weekly_lessons(fake(600), [], [])
    assert any(f["value"] == "venerdì" for f in new["filters"]), new
    assert any("Ho imparato" in m for m in msgs)
    assert learn.blocked_by({"pair": "EURUSD", "weekday": 4, "hour": 8, "width": 0.25, "strength": 1.0}, new)
    assert not learn.blocked_by({"pair": "EURUSD", "weekday": 1, "hour": 8, "width": 0.25, "strength": 1.0}, new)


def test_no_lesson_from_few_losses():
    new, _ = learn.weekly_lessons(fake(60), [], [])
    assert not new["filters"]


def test_unblock_when_blocked_trades_would_win():
    old, _ = learn.weekly_lessons(fake(600), [], [])
    blocked = [{"r_multiple": 1.0, "ctx": {"pair": "EURUSD", "weekday": 4, "hour": 8, "width": 0.25, "strength": 1.0}}] * 20
    new, msgs = learn.weekly_lessons(fake(600), [], blocked, old)
    assert not any(f["value"] == "venerdì" for f in new["filters"])
    assert any("Lezione rivista" in m for m in msgs)


def test_diary():
    tr = {"result": "SL", "opened_at": "2026-11-23T08:00:00Z", "closed_at": "2026-11-23T08:30:00Z",
          "ctx": {"pair": "EURGBP", "weekday": 4, "hour": 7, "width": 0.45, "strength": 0.2}}
    d = learn.diary(tr)
    assert "falsa rottura" in d and "venerdì" in d and "agitata" not in d and "mossa parecchio" in d
