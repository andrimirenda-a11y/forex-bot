# Formato dei file in data/ (contratto tra bot e app)

Tutti JSON, UTF-8, orari ISO 8601 in UTC (es. "2026-11-18T09:00:00Z"). Importi in EUR.

## settings.json  (scritto dall'app e dai comandi Telegram, letto dal bot)
{
  "capitale_iniziale": 10000.0,   // capitale del conto virtuale
  "leva": 30,                      // 1:30
  "rischio_pct": 1.0,              // % del conto rischiata per trade
  "valuta": "EUR",
  "pausa": false,                  // true = nessun nuovo trade
  "coppie_attive": ["EURUSD","GBPUSD","EURGBP","USDJPY","USDCHF","AUDUSD","USDCAD"],
  "notifiche_apertura": true, "notifiche_chiusura": true, "report_settimanale": true,
  "reset_conto": false             // true = il bot azzera il conto virtuale al prossimo giro e rimette false
}

## state.json  (scritto dal bot)
{
  "balance": 10000.0, "equity": 10012.5, "margin_used": 120.3, "start_balance": 10000.0,
  "created": "...", "last_run": "...",
  "open_positions": [
    {"id": "EURUSD-20261118T0900", "pair": "EURUSD", "side": "BUY", "entry": 1.08500, "sl": 1.08200, "tp": 1.09100,
     "sl_initial": 1.08200, "lots": 0.33, "risk_eur": 100.0, "pip_value_eur": 3.05, "margin_eur": 1100.0,
     "opened_at": "...", "score": 78, "reason": "testo", "be_moved": false,
     "last_price": 1.08600, "unrealized_eur": 30.5, "unrealized_pips": 10.0}
  ],
  "last_prices": {"EURUSD": {"price": 1.086, "time": "..."}},
  "paused_pairs": {"USDCAD": {"since": "...", "reason": "testo"}},
  "trading_halted_until": null, "halt_reason": null,
  "last_bar": {"EURUSD": "..."}, "tg_offset": 0, "tg_chat_id": null, "last_weekly_report": null
}

## history.json  (lista dei trade chiusi, più recente in fondo)
[{"id": "...", "pair": "EURUSD", "side": "BUY", "entry": 1.085, "exit": 1.091, "sl_initial": 1.082, "tp": 1.091,
  "lots": 0.33, "opened_at": "...", "closed_at": "...", "result": "TP" | "SL" | "BE" | "STOP_MANUALE",
  "pnl_eur": 198.0, "pnl_pips": 60.0, "r_multiple": 1.98, "score": 78}]

## equity.json  (curva del conto, un punto per giro, max ~3000 punti)
[{"t": "...", "equity": 10012.5, "balance": 10000.0}]

## signals.json  (tutti i segnali, max 500, più recente in fondo)
[{"time": "...", "pair": "EURUSD", "side": "BUY", "entry": 1.085, "sl": 1.082, "tp": 1.091, "score": 78,
  "status": "aperto" | "scartato", "note": "motivo se scartato"}]

## calibration.json  (scritto dalla calibrazione/backtest)
{"generated": "...", "data_from": "...", "data_to": "...",
 "pairs": {"EURUSD": {"enabled": true, "params": {...}, "in_sample": STATS, "out_sample": STATS, "note": "testo"}}}

## backtest.json
{"generated": "...", "data_from": "...", "data_to": "...",
 "total": STATS, "per_pair": {"EURUSD": STATS},
 "equity_r": [{"t": "...", "r": 12.5}]}       // R cumulati nel tempo, solo coppie abilitate, out-of-sample

STATS = {"trades": 42, "win_rate": 47.6, "profit_factor": 1.62, "net_r": 18.4,
         "expectancy_r": 0.44, "max_dd_r": 6.0, "avg_win_r": 1.9, "avg_loss_r": -1.0}

## log.json  (ultimi 200 eventi)
[{"t": "...", "level": "info" | "warn" | "trade", "msg": "testo in italiano"}]
