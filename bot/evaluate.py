"""Autovalutazione: statistiche dal vivo, confronto con il backtest, pausa delle coppie che perdono."""
from datetime import timedelta

from .storage import iso, parse

MIN_TRADES_FOR_JUDGEMENT = 10


def stats(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": 0, "profit_factor": 0, "net_eur": 0, "net_r": 0,
                "avg_win_eur": 0, "avg_loss_eur": 0, "max_dd_r": 0, "expectancy_r": 0}
    wins = [t for t in trades if t["pnl_eur"] > 0]
    losses = [t for t in trades if t["pnl_eur"] <= 0]
    gw = sum(t["pnl_eur"] for t in wins)
    gl = -sum(t["pnl_eur"] for t in losses)
    cum = peak = dd = 0.0
    for t in trades:
        cum += t["r_multiple"]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return {
        "trades": n,
        "win_rate": round(len(wins) / n * 100, 1),
        "profit_factor": round(gw / gl, 2) if gl > 0 else (99.0 if gw > 0 else 0),
        "net_eur": round(gw - gl, 2),
        "net_r": round(sum(t["r_multiple"] for t in trades), 2),
        "avg_win_eur": round(gw / len(wins), 2) if wins else 0,
        "avg_loss_eur": round(-gl / len(losses), 2) if losses else 0,
        "max_dd_r": round(dd, 2),
        "expectancy_r": round(sum(t["r_multiple"] for t in trades) / n, 2),
    }


def review_pairs(state, history, calibration, now) -> list[str]:
    """Mette in pausa le coppie che dal vivo vanno molto peggio del previsto.
    Restituisce messaggi da mandare su Telegram."""
    msgs = []
    paused = state.setdefault("paused_pairs", {})
    for pair, cal in (calibration.get("pairs") or {}).items():
        if not cal.get("enabled") or pair in paused:
            continue
        live = [t for t in history if t["pair"] == pair][-20:]
        if len(live) < MIN_TRADES_FOR_JUDGEMENT:
            continue
        s = stats(live)
        exp_dd = (cal.get("out_sample") or {}).get("max_dd_r") or 6
        reason = None
        if s["profit_factor"] < 0.8 and s["net_r"] < -3:
            reason = f"ultimi {s['trades']} trade in perdita ({s['net_r']:+.1f}R, PF {s['profit_factor']})"
        elif s["max_dd_r"] > max(exp_dd * 1.5, 6):
            reason = f"calo di {s['max_dd_r']:.1f}R, oltre 1,5 volte il peggiore previsto ({exp_dd:.1f}R)"
        if reason:
            paused[pair] = {"since": iso(now), "reason": reason}
            msgs.append(f"⏸ <b>{pair} in pausa</b>: {reason}. "
                        "Verrà riconsiderata alla prossima calibrazione settimanale (o con /riprendi).")
    return msgs


def verdict(s: dict, expected: dict | None) -> str:
    if s["trades"] < 30:
        return (f"⏳ <b>Servono ancora dati</b>: {s['trades']} operazioni sono poche per giudicare "
                "(ne servono almeno 30). Per ora niente soldi veri.")
    exp_pf = (expected or {}).get("profit_factor") or 1.3
    if s["profit_factor"] >= max(1.2, exp_pf * 0.75) and s["net_r"] > 0:
        return ("✅ <b>Funziona</b>: i risultati dal vivo sono in linea o migliori del previsto. "
                "Se resta così, si può valutare un conto demo del broker e poi pochi soldi veri.")
    if s["profit_factor"] >= 1.0:
        return "🟡 <b>In equilibrio</b>: guadagna poco o pareggia, sotto le attese. Continuiamo a osservare."
    return "❌ <b>Non sta funzionando</b>: dal vivo perde. Meglio non passare ai soldi veri finché non migliora."


def weekly_report(state, history, calibration, backtest, now) -> str:
    week_ago = now - timedelta(days=7)
    week = [t for t in history if parse(t["closed_at"]) >= week_ago]
    sw = stats(week)
    sa = stats(history)
    start = state.get("start_balance", 0) or 1
    eq = state.get("equity", state.get("balance", 0))
    lines = [
        "📊 <b>Report settimanale</b>",
        "",
        f"Conto: <b>{eq:,.2f} €</b> ({(eq - start) / start * 100:+.2f}% dall'inizio)",
        f"Questa settimana: {sw['trades']} trade, {sw['net_eur']:+.2f} €, vincite {sw['win_rate']}%",
        f"Da sempre: {sa['trades']} trade, {sa['net_eur']:+.2f} €, vincite {sa['win_rate']}%, "
        f"PF {sa['profit_factor']}, calo massimo {sa['max_dd_r']}R",
        "",
    ]
    if any(p.get("strategy") == "prova" for p in state.get("open_positions", [])) or \
            not any((c or {}).get("enabled") for c in (calibration.get("pairs") or {}).values()):
        lines += ["🧪 Strategia usata: <b>breakout di Londra, in prova</b> (solo soldi finti).", ""]
    per = {}
    for t in history:
        per.setdefault(t["pair"], []).append(t)
    if per:
        lines.append("<b>Per coppia</b>")
        for pair, trs in sorted(per.items()):
            s = stats(trs)
            lines.append(f"• {pair}: {s['trades']} trade, {s['net_eur']:+.2f} €, vincite {s['win_rate']}%")
        lines.append("")
    if state.get("paused_pairs"):
        lines.append("<b>In pausa</b>: " + ", ".join(state["paused_pairs"].keys()))
    lines.append(verdict(sa, (backtest or {}).get("total")))
    return "\n".join(lines)
