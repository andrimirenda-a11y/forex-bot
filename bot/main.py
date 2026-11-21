"""Un "giro" del bot. Lanciato da GitHub Actions ogni 10 minuti.

1. legge impostazioni e comandi Telegram
2. scarica i prezzi, aggiorna le posizioni aperte (stop / target / pareggio)
3. controlla i freni di sicurezza (perdita giornaliera/settimanale)
4. cerca nuovi segnali sulle candele orarie appena chiuse e apre le posizioni virtuali
5. si autovaluta e manda il report settimanale
6. resta in ascolto dei comandi Telegram per qualche minuto, poi salva tutto
"""
import os
import time
import traceback
from datetime import timedelta

import pandas as pd

from . import broker, data, evaluate, london, strategy, telegram
from .config import (
    BACKTEST_FILE, CALIBRATION_FILE, DAILY_LOSS_LIMIT_PCT, DEFAULT_PARAMS, DEFAULT_SETTINGS,
    EQUITY_FILE, EUR_CROSSES, HISTORY_FILE, LOG_FILE, MAX_OPEN_POSITIONS, MAX_PER_CURRENCY,
    NEWS_BLOCK_MINUTES, PAIRS, SETTINGS_FILE, SIGNAL_VALID_MINUTES, SIGNALS_FILE, STATE_FILE,
    WEEKLY_LOSS_LIMIT_PCT,
)
from .risk import size_position
from .storage import iso, load, now_utc, parse, save

APP_URL = os.environ.get("APP_URL", "")


# ----------------------------------------------------------------- formattazione
def eur(x: float) -> str:
    s = f"{abs(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if x < 0 else "") + s + " €"


def signed_eur(x: float) -> str:
    return ("+" if x >= 0 else "") + eur(x)


def px(pair: str, v: float) -> str:
    return f"{v:.3f}" if PAIRS[pair]["pip"] == 0.01 else f"{v:.5f}"


def rome(ts) -> str:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.tz_convert("Europe/Rome").strftime("%d/%m %H:%M")


# ----------------------------------------------------------------- stato
def default_state(settings):
    cap = float(settings["capitale_iniziale"])
    return {
        "balance": cap, "equity": cap, "margin_used": 0.0, "start_balance": cap,
        "created": iso(now_utc()), "last_run": None, "open_positions": [], "last_prices": {},
        "paused_pairs": {}, "trading_halted_until": None, "halt_reason": None,
        "last_bar": {}, "tg_offset": 0, "tg_chat_id": None, "last_weekly_report": None,
        "last_error_notice": None, "last_calibration_seen": None,
    }


class Bot:
    def __init__(self):
        self.now = now_utc()
        self.settings = {**DEFAULT_SETTINGS, **load(SETTINGS_FILE, {})}
        self.settings_dirty = not SETTINGS_FILE.exists()
        self.state = {**default_state(self.settings), **load(STATE_FILE, {})}
        self.history = load(HISTORY_FILE, [])
        self.signals = load(SIGNALS_FILE, [])
        self.equity_curve = load(EQUITY_FILE, [])
        self.log_items = load(LOG_FILE, [])
        self.calibration = load(CALIBRATION_FILE, {})
        self.backtest = load(BACKTEST_FILE, {})
        env_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if env_chat and not self.state.get("tg_chat_id"):
            self.state["tg_chat_id"] = int(env_chat)
        self.rates = {}
        self.m15 = {}

    # ------------------------------------------------------------- utilità
    def log(self, msg, level="info"):
        print(f"[{level}] {msg}")
        self.log_items.append({"t": iso(now_utc()), "level": level, "msg": msg})
        self.log_items = self.log_items[-200:]

    def tell(self, text):
        telegram.send(self.state.get("tg_chat_id"), text)

    def params_for(self, pair):
        cal = (self.calibration.get("pairs") or {}).get(pair) or {}
        return {**DEFAULT_PARAMS, **(cal.get("params") or {})}

    def strategy_for(self, pair):
        """Strategia principale se ha superato l'esame su questa coppia, altrimenti quella in prova."""
        cal = (self.calibration.get("pairs") or {}).get(pair) or {}
        if cal.get("enabled"):
            return "main"
        if self.settings.get("strategia_prova", True):
            return "prova"
        return None

    def pair_enabled(self, pair):
        cal = (self.calibration.get("pairs") or {}).get(pair)
        if self.strategy_for(pair) is None:
            if cal is None:
                return False, "non ancora calibrata"
            return False, cal.get("note") or "disattivata dalla calibrazione"
        if pair not in self.settings.get("coppie_attive", []):
            return False, "disattivata nelle impostazioni"
        if pair in self.state.get("paused_pairs", {}):
            return False, "in pausa: " + self.state["paused_pairs"][pair]["reason"]
        return True, None

    # ------------------------------------------------------------- impostazioni / reset
    def apply_settings(self):
        s = self.settings
        if s.get("reset_conto"):
            cap = float(s["capitale_iniziale"])
            chat, offset = self.state.get("tg_chat_id"), self.state.get("tg_offset", 0)
            self.state = default_state(s)
            self.state["tg_chat_id"], self.state["tg_offset"] = chat, offset
            self.history, self.signals, self.equity_curve = [], [], []
            s["reset_conto"] = False
            self.settings_dirty = True
            self.log(f"Conto virtuale azzerato a {eur(cap)}")
            self.tell(f"🔄 Conto virtuale azzerato. Capitale di partenza: <b>{eur(cap)}</b>")
            return
        cap = float(s["capitale_iniziale"])
        if abs(cap - self.state["start_balance"]) > 0.005:
            diff = cap - self.state["start_balance"]
            self.state["balance"] = round(self.state["balance"] + diff, 2)
            self.state["start_balance"] = cap
            self.log(f"Capitale cambiato: {signed_eur(diff)} sul conto (nuovo capitale iniziale {eur(cap)})")

    # ------------------------------------------------------------- prezzi
    def load_prices(self):
        for cur, sym in EUR_CROSSES.items():
            try:
                df = data.candles(sym, "15m", "5d")
                self.rates[cur] = float(df["close"].iloc[-1])
            except Exception as e:  # noqa: BLE001
                self.log(f"Cambio EUR/{cur} non disponibile: {e}", "warn")
        for pair, info in PAIRS.items():
            try:
                df = data.candles(info["yahoo"], "15m", "5d")
                self.m15[pair] = df
                self.state["last_prices"][pair] = {"price": round(float(df["close"].iloc[-1]), 6),
                                                   "time": iso(df.index[-1])}
            except Exception as e:  # noqa: BLE001
                self.log(f"Prezzi {pair} non disponibili: {e}", "warn")
        # se manca qualche cambio lo ricaviamo dalle coppie
        if "USD" in self.rates:
            usd = self.rates["USD"]
            lp = {p: v["price"] for p, v in self.state["last_prices"].items()}
            for cur, pair, inv in (("JPY", "USDJPY", False), ("CHF", "USDCHF", False),
                                   ("CAD", "USDCAD", False), ("AUD", "AUDUSD", True), ("GBP", "GBPUSD", True)):
                if cur not in self.rates and pair in lp:
                    self.rates[cur] = usd / lp[pair] if inv else usd * lp[pair]
        return len(self.m15) > 0 and "USD" in self.rates

    # ------------------------------------------------------------- posizioni
    def manage_positions(self):
        if not self.state["open_positions"]:
            return
        closed = broker.manage(self.state, self.m15, self.rates, self.now)
        for tr in closed:
            self.history.append(tr)
            emoji = {"TP": "🎯", "SL": "🛑", "BE": "⚖️", "TEMPO": "⏰"}.get(tr["result"], "✖️")
            label = {"TP": "Take profit raggiunto", "SL": "Stop loss", "BE": "Chiuso a pareggio",
                     "TEMPO": "Chiuso a fine giornata"}.get(tr["result"], tr["result"])
            self.log(f"Chiuso {tr['pair']} {tr['side']}: {label}, {signed_eur(tr['pnl_eur'])}", "trade")
            if self.settings.get("notifiche_chiusura", True):
                self.tell(
                    f"{emoji} <b>{label} – {tr['pair']}</b>\n"
                    f"{tr['side']} {tr['lots']:.2f} lotti · ingresso {px(tr['pair'], tr['entry'])} → uscita {px(tr['pair'], tr['exit'])}\n"
                    f"Risultato: <b>{signed_eur(tr['pnl_eur'])}</b> ({tr['pnl_pips']:+.1f} pip, {tr['r_multiple']:+.2f}R)\n"
                    f"Conto: {eur(self.state['balance'])}"
                )
        for pos in self.state["open_positions"]:
            for ev in pos.pop("events", []):
                self.log(f"{pos['pair']}: {ev['msg']}")
                if self.settings.get("notifiche_chiusura", True):
                    self.tell(f"🔒 <b>{pos['pair']}</b>: il trade guadagna già 1R, stop spostato a pareggio. Ora non può più perdere.")

    # ------------------------------------------------------------- freni di sicurezza
    def check_limits(self):
        halted = parse(self.state.get("trading_halted_until"))
        if halted and halted > self.now:
            return False
        if halted:
            self.state["trading_halted_until"], self.state["halt_reason"] = None, None
            self.log("Blocco di sicurezza terminato, il bot riprende a operare")
        eq = broker.equity(self.state)
        day_start = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = day_start - timedelta(days=day_start.weekday())

        def pnl_since(t):
            return sum(x["pnl_eur"] for x in self.history if parse(x["closed_at"]) >= t) + \
                sum(p.get("unrealized_eur", 0) for p in self.state["open_positions"])

        base = max(eq, 1)
        d, w = pnl_since(day_start), pnl_since(week_start)
        reason, until = None, None
        if d <= -base * DAILY_LOSS_LIMIT_PCT / 100:
            reason, until = f"perdita giornaliera {eur(d)} oltre il limite del {DAILY_LOSS_LIMIT_PCT:.0f}%", day_start + timedelta(days=1)
        elif w <= -base * WEEKLY_LOSS_LIMIT_PCT / 100:
            reason, until = f"perdita settimanale {eur(w)} oltre il limite del {WEEKLY_LOSS_LIMIT_PCT:.0f}%", week_start + timedelta(days=7)
        if reason:
            self.state["trading_halted_until"], self.state["halt_reason"] = iso(until), reason
            self.log("Blocco di sicurezza: " + reason, "warn")
            self.tell(f"🧯 <b>Freno di sicurezza</b>: {reason}.\nNessun nuovo trade fino al {rome(until)}. "
                      "Le posizioni aperte restano protette da stop e target.")
            return False
        return True

    # ------------------------------------------------------------- segnali
    def exposure_ok(self, pair, side):
        sign = 1 if side == "BUY" else -1
        info = PAIRS[pair]
        new = {info["base"]: sign, info["quote"]: -sign}
        for cur, s in new.items():
            same = 0
            for p in self.state["open_positions"]:
                pi = PAIRS[p["pair"]]
                ps = 1 if p["side"] == "BUY" else -1
                if (pi["base"] == cur and ps == s) or (pi["quote"] == cur and -ps == s):
                    same += 1
            if same >= MAX_PER_CURRENCY:
                return False, f"già {same} posizioni esposte su {cur} nello stesso verso"
        return True, None

    def news_block(self, pair, events):
        info = PAIRS[pair]
        for ev in events:
            if ev["currency"] in (info["base"], info["quote"]):
                if abs((ev["time"] - pd.Timestamp(self.now)).total_seconds()) <= NEWS_BLOCK_MINUTES * 60:
                    return f"notizia importante: {ev['title']} ({ev['currency']}, {rome(ev['time'])})"
        return None

    def next_news(self, pair, events):
        info = PAIRS[pair]
        fut = [e for e in events if e["currency"] in (info["base"], info["quote"]) and e["time"] > pd.Timestamp(self.now)]
        return min(fut, key=lambda e: e["time"]) if fut else None

    def record_signal(self, pair, side, row, status, note=None):
        self.signals.append({
            "time": iso(self.now), "pair": pair, "side": side, "entry": round(float(row["entry"]), 6),
            "sl": round(float(row["sl"]), 6), "tp": round(float(row["tp"]), 6), "score": int(row["score"]),
            "status": status, "note": note,
        })
        self.signals = self.signals[-500:]

    def scan(self, can_trade: bool):
        try:
            events = data.news_calendar()
        except Exception:  # noqa: BLE001
            events = []
        for pair, info in PAIRS.items():
            ok, why = self.pair_enabled(pair)
            if not ok:
                continue
            try:
                h1 = data.candles(info["yahoo"], "1h", "180d")
            except Exception as e:  # noqa: BLE001
                self.log(f"Candele orarie {pair} non disponibili: {e}", "warn")
                continue
            h1 = data.closed_only(h1, pd.Timedelta(hours=1), self.now)
            if len(h1) < 900:
                self.log(f"{pair}: storico troppo corto ({len(h1)} ore)", "warn")
                continue
            last_bar = h1.index[-1]
            if self.state["last_bar"].get(pair) == iso(last_bar):
                continue  # candela già analizzata
            self.state["last_bar"][pair] = iso(last_bar)
            # la candela deve essere appena chiusa, non vecchia (es. weekend)
            if pd.Timestamp(self.now) - (last_bar + pd.Timedelta(hours=1)) > pd.Timedelta(minutes=SIGNAL_VALID_MINUTES):
                continue
            mode = self.strategy_for(pair)
            mod = london if mode == "prova" else strategy
            params = {"breakeven_r": 0, "rr": london.TP_R} if mode == "prova" else self.params_for(pair)
            built = mod.build(h1, params)
            row = built.iloc[-1]
            if row["signal"] == 0:
                continue
            side = "BUY" if row["signal"] == 1 else "SELL"
            # filtri
            note = None
            if not can_trade:
                note = self.state.get("halt_reason") or "bot in pausa"
            elif self.settings.get("pausa"):
                note = "bot in pausa dalle impostazioni"
            elif any(p["pair"] == pair for p in self.state["open_positions"]):
                note = "posizione già aperta su questa coppia"
            elif len(self.state["open_positions"]) >= MAX_OPEN_POSITIONS:
                note = f"già {MAX_OPEN_POSITIONS} posizioni aperte"
            else:
                nb = self.news_block(pair, events)
                if nb:
                    note = nb
                else:
                    ok_exp, why_exp = self.exposure_ok(pair, side)
                    if not ok_exp:
                        note = why_exp
            # il prezzo attuale non deve essere già scappato
            live = self.state["last_prices"].get(pair, {}).get("price")
            entry = float(row["entry"])
            sl, tp = float(row["sl"]), float(row["tp"])
            if note is None and live:
                risk = abs(entry - sl)
                moved = (live - entry) if side == "BUY" else (entry - live)
                if moved > 0.3 * risk:
                    note = "il prezzo si è già mosso troppo dall'ingresso"
                elif (side == "BUY" and live <= sl) or (side == "SELL" and live >= sl):
                    note = "il prezzo è già oltre lo stop"
                else:
                    # ricalcolo su prezzo attuale mantenendo stop e target del segnale
                    entry = live
            if note is None:
                eq = broker.equity(self.state)
                free = eq - broker.margin_used(self.state)
                sizing = size_position(pair, entry, sl, eq, free, self.settings, self.rates)
                if sizing.get("skip"):
                    note = sizing["skip"]
            if note:
                self.record_signal(pair, side, row, "scartato", note)
                self.log(f"Segnale {pair} {side} scartato: {note}")
                continue
            reason = mod.describe(row, 1 if side == "BUY" else -1)
            pos = broker.open_position(self.state, pair, side, entry, sl, tp, sizing, row["score"], reason, self.now, params)
            if mode == "prova":
                pos["strategy"] = "prova"
                pos["exit_at"] = iso(row["exit_at"])
            self.record_signal(pair, side, row, "aperto")
            self.log(f"Aperto {pair} {side} {pos['lots']:.2f} lotti a {px(pair, pos['entry'])}", "trade")
            if self.settings.get("notifiche_apertura", True):
                self.tell(self.open_message(pos, sizing, params, self.next_news(pair, events)))

    def open_message(self, pos, sizing, params, news):
        pair = pos["pair"]
        buy = pos["side"] == "BUY"
        tp_pips = abs(pos["tp"] - pos["entry"]) / PAIRS[pair]["pip"]
        sl_pips = abs(pos["entry"] - pos["sl"]) / PAIRS[pair]["pip"]
        gain = tp_pips * pos["pip_value_eur"]
        eq = broker.equity(self.state)
        test = pos.get("strategy") == "prova"
        head = (f"{'🟢 COMPRA' if buy else '🔴 VENDI'} <b>{pair[:3]}/{pair[3:]}</b>"
                + ("  🧪 <i>strategia in prova</i>" if test else f"  (affidabilità {pos['score']}/100)"))
        lines = [
            head,
            "",
            f"Ingresso: <b>{px(pair, pos['entry'])}</b>",
            f"Stop loss: <b>{px(pair, pos['sl'])}</b>  ({sl_pips:.1f} pip)",
            f"Take profit: <b>{px(pair, pos['tp'])}</b>  ({tp_pips:.1f} pip)",
            "",
            f"Lotti: <b>{pos['lots']:.2f}</b>  ·  1 pip = {eur(pos['pip_value_eur'])}",
            f"Se tocca lo stop: <b>-{eur(pos['risk_eur'])}</b> ({pos['risk_eur'] / eq * 100:.1f}% del conto)",
            f"Se tocca il target: <b>+{eur(gain)}</b>  (rapporto 1:{params['rr']:g})",
            f"Margine bloccato (leva 1:{self.settings['leva']}): {eur(pos['margin_eur'])}",
            "",
            f"📈 {pos['reason']}",
        ]
        if test:
            lines.append(f"⏰ Se non tocca stop o target, la chiudo da solo alle {rome(pos['exit_at'])} (ora italiana).")
        else:
            lines.append(f"⏱ Valido {SIGNAL_VALID_MINUTES} min: se il prezzo è già oltre "
                         f"{px(pair, pos['entry'] + (1 if buy else -1) * 0.3 * abs(pos['entry'] - pos['sl']))}, non entrare.")
        if sizing.get("note"):
            lines.append("⚠️ " + sizing["note"])
        if news:
            lines.append(f"📰 Prossima notizia importante: {news['title']} ({news['currency']}) alle {rome(news['time'])}")
        lines.append("")
        lines.append("✅ Aperto in automatico sul <b>conto virtuale</b> (soldi finti): non devi fare nulla.")
        return "\n".join(lines)

    # ------------------------------------------------------------- comandi telegram
    def handle_commands(self):
        if not telegram.enabled():
            return
        ups = telegram.updates(self.state.get("tg_offset", 0))
        for u in ups:
            self.state["tg_offset"] = u["update_id"] + 1
            msg = u.get("message") or {}
            chat = (msg.get("chat") or {}).get("id")
            text = (msg.get("text") or "").strip()
            if not chat or not text.startswith("/"):
                continue
            owner = self.state.get("tg_chat_id")
            if owner is None and text.split()[0].lower().startswith("/start"):
                self.state["tg_chat_id"] = chat
                telegram.set_commands()
                self.tell(
                    "👋 Ciao Drew! Sono il tuo bot forex, collegato a questa chat.\n\n"
                    "<b>Cosa faccio:</b> controllo il mercato ogni 10 minuti e faccio trading da solo "
                    "con <b>soldi finti</b> (conto virtuale da " + eur(self.state['start_balance']) + ").\n\n"
                    "<b>Cosa ti scrivo:</b>\n"
                    "🟢/🔴 quando apro un'operazione\n🎯/🛑 quando la chiudo, con quanto ho guadagnato o perso\n"
                    "📅 ogni sera alle 22 un riepilogo della giornata\n📊 ogni domenica il resoconto della settimana\n\n"
                    "<b>Adesso:</b> " + self.status_line() + "\n\nTu non devi fare niente. "
                    "Se vuoi sapere com'è la situazione scrivimi /stato.\n\n" + self.help_text()
                )
                self.log("Chat Telegram collegata")
                continue
            if chat != owner:
                telegram.send(chat, "Questo bot è privato.")
                continue
            try:
                self.command(text)
            except Exception as e:  # noqa: BLE001
                self.tell(f"Non ho capito il comando ({e}). Scrivi /aiuto per l'elenco.")

    def help_text(self):
        return (
            "<b>Comandi</b>\n"
            "/stato – conto e situazione\n/posizioni – posizioni aperte\n/statistiche – risultati\n"
            "/impostazioni – impostazioni attuali\n/capitale 2000 – cambia capitale\n/leva 30 – cambia leva\n"
            "/rischio 1 – rischio per trade in %\n/pausa – niente nuovi trade\n/riprendi – riattiva tutto\n"
            "/stop – chiude tutte le posizioni e mette in pausa\n/aiuto – questo elenco"
            + (f"\n\n📱 App: {APP_URL}" if APP_URL else "")
        )

    def command(self, text):
        parts = text.split()
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1].replace(",", ".") if len(parts) > 1 else None
        s = self.settings
        if cmd in ("/aiuto", "/help", "/start"):
            self.tell(self.help_text())
        elif cmd == "/stato":
            self.tell(self.status_text())
        elif cmd == "/posizioni":
            self.tell(self.positions_text())
        elif cmd == "/statistiche":
            self.tell(evaluate.weekly_report(self.state, self.history, self.calibration, self.backtest, self.now)
                      .replace("Report settimanale", "Statistiche"))
        elif cmd == "/impostazioni":
            self.tell(self.settings_text())
        elif cmd == "/capitale" and arg:
            v = float(arg)
            if not 10 <= v <= 10_000_000:
                raise ValueError("capitale tra 10 e 10.000.000 €")
            s["capitale_iniziale"] = v
            self.settings_dirty = True
            self.apply_settings()
            self.tell(f"💶 Capitale impostato a <b>{eur(v)}</b>. Conto attuale: {eur(self.state['balance'])}")
        elif cmd == "/leva" and arg:
            v = int(float(arg.replace("1:", "")))
            if not 1 <= v <= 1000:
                raise ValueError("leva tra 1 e 1000")
            s["leva"] = v
            self.settings_dirty = True
            self.tell(f"⚙️ Leva impostata a <b>1:{v}</b>. Vale dai prossimi trade.")
        elif cmd == "/rischio" and arg:
            v = float(arg.replace("%", ""))
            if not 0.1 <= v <= 10:
                raise ValueError("rischio tra 0,1% e 10%")
            s["rischio_pct"] = v
            self.settings_dirty = True
            warn = "\n⚠️ Sopra il 2% per trade una serie di stop può far calare il conto molto in fretta." if v > 2 else ""
            self.tell(f"⚙️ Rischio per trade impostato a <b>{v:g}%</b>.{warn}")
        elif cmd == "/pausa":
            s["pausa"] = True
            self.settings_dirty = True
            self.tell("⏸ Bot in pausa: niente nuovi trade. Le posizioni aperte restano gestite. /riprendi per ripartire.")
        elif cmd == "/riprendi":
            s["pausa"] = False
            self.settings_dirty = True
            self.state["paused_pairs"] = {}
            self.state["trading_halted_until"], self.state["halt_reason"] = None, None
            self.tell("▶️ Bot riattivato, coppie in pausa comprese.")
        elif cmd == "/stop":
            prices = {p: v["price"] for p, v in self.state["last_prices"].items()}
            closed = broker.close_all(self.state, prices, self.rates or {"USD": 1.08, "GBP": 0.85, "JPY": 165, "CHF": 0.95, "CAD": 1.5, "AUD": 1.65}, self.now)
            self.history.extend(closed)
            s["pausa"] = True
            self.settings_dirty = True
            tot = sum(t["pnl_eur"] for t in closed)
            self.tell(f"🛑 STOP: chiuse {len(closed)} posizioni ({signed_eur(tot)}). Bot in pausa. /riprendi per ripartire.")
        else:
            self.tell("Comando non riconosciuto.\n\n" + self.help_text())

    def status_text(self):
        st = self.state
        eq = broker.equity(st)
        start = st["start_balance"] or 1
        today = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        d = sum(t["pnl_eur"] for t in self.history if parse(t["closed_at"]) >= today)
        return (
            f"💼 <b>Conto virtuale: {eur(eq)}</b> ({(eq - start) / start * 100:+.2f}% dall'inizio)\n"
            f"Oggi: {signed_eur(d)} · Operazioni aperte: {len(st['open_positions'])}\n\n"
            f"ℹ️ {self.status_line()}"
        )

    def positions_text(self):
        ps = self.state["open_positions"]
        if not ps:
            return "Nessuna posizione aperta."
        out = []
        for p in ps:
            out.append(
                f"<b>{p['pair']}</b> {p['side']} {p['lots']:.2f} lotti\n"
                f"Ingresso {px(p['pair'], p['entry'])} · SL {px(p['pair'], p['sl'])} · TP {px(p['pair'], p['tp'])}\n"
                f"Ora {px(p['pair'], p['last_price'])}: {signed_eur(p['unrealized_eur'])} ({p['unrealized_pips']:+.1f} pip)"
                + (f"\n⏰ Chiusura automatica alle {rome(p['exit_at'])}" if p.get("exit_at") else "")
            )
        return "\n\n".join(out)

    def settings_text(self):
        s = self.settings
        return (
            f"⚙️ <b>Impostazioni</b>\nCapitale: {eur(s['capitale_iniziale'])}\nLeva: 1:{s['leva']}\n"
            f"Rischio per trade: {s['rischio_pct']:g}%\nPausa: {'sì' if s['pausa'] else 'no'}\n"
            f"Coppie: {', '.join(s['coppie_attive'])}"
        )

    # ------------------------------------------------------------- ciclo
    def snapshot(self):
        st = self.state
        st["equity"] = broker.equity(st)
        st["margin_used"] = broker.margin_used(st)
        st["last_run"] = iso(now_utc())
        try:
            st["status_line"] = self.status_line()
            st["market_open"] = self.market_open()
        except Exception:  # noqa: BLE001
            pass
        last_t = parse(self.equity_curve[-1]["t"]) if self.equity_curve else None
        if not last_t or (now_utc() - last_t) >= timedelta(minutes=55) or self.state["open_positions"]:
            self.equity_curve.append({"t": iso(now_utc()), "equity": st["equity"], "balance": st["balance"]})
            self.equity_curve = self.equity_curve[-3000:]

    def save_all(self):
        self.snapshot()
        save(STATE_FILE, self.state)
        save(HISTORY_FILE, self.history)
        save(SIGNALS_FILE, self.signals)
        save(EQUITY_FILE, self.equity_curve)
        save(LOG_FILE, self.log_items)
        if self.settings_dirty:
            save(SETTINGS_FILE, self.settings)

    def maybe_weekly(self):
        if not self.settings.get("report_settimanale", True):
            return
        rome_now = pd.Timestamp(self.now).tz_convert("Europe/Rome")
        last = parse(self.state.get("last_weekly_report"))
        if rome_now.day_of_week == 6 and rome_now.hour >= 18 and (not last or self.now - last > timedelta(days=5)):
            self.tell(evaluate.weekly_report(self.state, self.history, self.calibration, self.backtest, self.now))
            self.state["last_weekly_report"] = iso(self.now)

    def calibration_news(self):
        gen = self.calibration.get("generated")
        if gen and gen != self.state.get("last_calibration_seen"):
            self.state["last_calibration_seen"] = gen
            pairs = self.calibration.get("pairs") or {}
            on = [p for p, c in pairs.items() if c.get("enabled")]
            off = [f"{p} ({c.get('note', '')})" for p, c in pairs.items() if not c.get("enabled")]
            tot = (self.backtest or {}).get("total") or {}
            # le coppie in pausa vengono rivalutate a ogni nuova calibrazione
            self.state["paused_pairs"] = {}
            if on:
                msg = ["🧪 <b>Esame settimanale fatto</b>",
                       "Ho riprovato la strategia sugli ultimi 2 anni di mercato.",
                       f"✅ Promosse (ci faccio trading): <b>{', '.join(on)}</b>"]
                if off:
                    msg.append(f"⛔ Bocciate (le lascio stare): {', '.join(p for p, c in pairs.items() if not c.get('enabled'))}")
                if tot:
                    msg.append(f"Nel test su dati mai visti: {tot.get('trades', 0)} trade, "
                               f"{tot.get('win_rate', 0)}% vinti, risultato {tot.get('net_r', 0):+} volte il rischio.")
            else:
                msg = ["🧪 <b>Esame settimanale fatto</b>",
                       "Ho riprovato la strategia sugli ultimi 2 anni di mercato e <b>non è stata promossa su nessuna coppia</b>.",
                       (f"Intanto continuo a provare la strategia «{london.NAME}» <b>solo con soldi finti</b>, "
                        "per vedere se regge sul mercato di adesso." if self.settings.get("strategia_prova", True)
                        else "Per sicurezza non apro operazioni finché una strategia non supera l'esame.")]
            self.tell("\n".join(msg))

    # ------------------------------------------------------------- frasi semplici per app e Telegram
    def market_open(self) -> bool:
        t = pd.Timestamp(self.now)
        wd, h = t.day_of_week, t.hour
        return not (wd == 5 or (wd == 4 and h >= 21) or (wd == 6 and h < 22))

    def status_line(self) -> str:
        st = self.state
        active = [p for p in PAIRS if self.pair_enabled(p)[0]]
        ops = st["open_positions"]
        if ops:
            u = sum(p.get("unrealized_eur", 0) for p in ops)
            names = ", ".join(f"{p['pair'][:3]}/{p['pair'][3:]}" for p in ops)
            return f"Ha {len(ops)} operazion{'e' if len(ops) == 1 else 'i'} apert{'a' if len(ops) == 1 else 'e'} ({names}): in questo momento {signed_eur(u)}."
        if self.settings.get("pausa"):
            return "In pausa: non apre nuove operazioni finché non la riattivi."
        if st.get("trading_halted_until"):
            return f"Fermo per sicurezza fino al {rome(st['trading_halted_until'])}: {st.get('halt_reason')}."
        if not self.market_open():
            return "Mercato forex chiuso nel weekend: si riparte domenica alle 23:00."
        if not active:
            return "Acceso, ma non fa trading: nessuna strategia ha ancora superato l'esame sui dati passati."
        names = ', '.join(a[:3] + '/' + a[3:] for a in active)
        if all(self.strategy_for(a) == "prova" for a in active):
            return (f"Sta provando la strategia «{london.NAME}» con soldi finti su {names}. "
                    "Cerca occasioni ogni mattina tra le 9 e le 12 italiane (mercato di Londra).")
        return f"Sta osservando {names}: entra solo quando trova un'occasione valida."

    def maybe_daily(self):
        """Riepilogo serale semplice (lun-ven verso le 22 italiane)."""
        rome_now = pd.Timestamp(self.now).tz_convert("Europe/Rome")
        last = parse(self.state.get("last_daily_report"))
        if rome_now.day_of_week >= 5 or rome_now.hour < 22:
            return
        if last and pd.Timestamp(last).tz_convert("Europe/Rome").date() == rome_now.date():
            return
        self.state["last_daily_report"] = iso(self.now)
        day_start = rome_now.normalize().tz_convert("UTC")
        today = [t for t in self.history if pd.Timestamp(parse(t["closed_at"])) >= day_start]
        opened = [s for s in self.signals if s["status"] == "aperto" and pd.Timestamp(parse(s["time"])) >= day_start]
        eq = broker.equity(self.state)
        start = self.state["start_balance"] or 1
        lines = [f"📅 <b>Riepilogo di oggi</b>"]
        if not today and not opened:
            lines.append("😴 Oggi nessuna occasione buona: non ho aperto operazioni.")
        else:
            pnl = sum(t["pnl_eur"] for t in today)
            lines.append(f"Operazioni aperte oggi: {len(opened)} · chiuse: {len(today)} ({signed_eur(pnl)})")
        lines.append(f"💼 Conto: <b>{eur(eq)}</b> ({(eq - start) / start * 100:+.2f}% dall'inizio)")
        lines.append("ℹ️ " + self.status_line())
        self.tell("\n".join(lines))

    def run(self, listen_minutes: float = 0):
        self.handle_commands()
        self.apply_settings()
        self.calibration_news()
        try:
            got = self.load_prices()
            if not got:
                raise RuntimeError("nessun prezzo scaricato")
            self.manage_positions()
            can_trade = self.check_limits()
            self.scan(can_trade)
            for m in evaluate.review_pairs(self.state, self.history, self.calibration, self.now):
                self.log(m, "warn")
                self.tell(m)
            self.maybe_weekly()
            self.maybe_daily()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.log(f"Errore nel giro: {e}", "warn")
            last = parse(self.state.get("last_error_notice"))
            if not last or self.now - last > timedelta(hours=6):
                self.state["last_error_notice"] = iso(self.now)
                self.tell(f"⚠️ Problema tecnico: {e}. Riprovo da solo al prossimo giro.")
        self.save_all()
        # resta in ascolto dei comandi per avere risposte quasi immediate
        deadline = time.time() + listen_minutes * 60
        while time.time() < deadline:
            before = self.state.get("tg_offset")
            res = telegram.call("getUpdates", offset=self.state.get("tg_offset", 0), timeout=25,
                                allowed_updates=["message"])
            if res:
                # rimetti in coda e gestisci con la logica standard
                self.state["tg_offset"] = before
                self.handle_commands()
                self.save_all()
            elif res is None:
                time.sleep(5)


def main():
    listen = float(os.environ.get("LISTEN_MINUTES", "0"))
    Bot().run(listen)


if __name__ == "__main__":
    main()
