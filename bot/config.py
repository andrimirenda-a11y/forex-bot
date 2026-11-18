"""Configurazione fissa del bot e percorsi dei file dati.

Le impostazioni che Drew può cambiare (capitale, leva, rischio...) stanno in
data/settings.json e vengono rilette a ogni giro. Qui ci sono solo le costanti.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

SETTINGS_FILE = DATA / "settings.json"
STATE_FILE = DATA / "state.json"          # conto virtuale, posizioni aperte, stato interno
HISTORY_FILE = DATA / "history.json"      # tutti i trade chiusi
SIGNALS_FILE = DATA / "signals.json"      # tutti i segnali generati (anche scartati)
CALIBRATION_FILE = DATA / "calibration.json"  # parametri scelti dal backtest per ogni coppia
BACKTEST_FILE = DATA / "backtest.json"    # riassunto dell'ultimo backtest
EQUITY_FILE = DATA / "equity.json"        # curva del conto nel tempo
LOG_FILE = DATA / "log.json"              # ultimi eventi (per l'app)

# Coppie seguite. "yahoo" è il simbolo usato per scaricare i prezzi.
PAIRS = {
    "EURUSD": {"yahoo": "EURUSD=X", "base": "EUR", "quote": "USD", "pip": 0.0001, "spread": 1.0},
    "GBPUSD": {"yahoo": "GBPUSD=X", "base": "GBP", "quote": "USD", "pip": 0.0001, "spread": 1.3},
    "EURGBP": {"yahoo": "EURGBP=X", "base": "EUR", "quote": "GBP", "pip": 0.0001, "spread": 1.3},
    "USDJPY": {"yahoo": "JPY=X",    "base": "USD", "quote": "JPY", "pip": 0.01,   "spread": 1.2},
    "USDCHF": {"yahoo": "CHF=X",    "base": "USD", "quote": "CHF", "pip": 0.0001, "spread": 1.5},
    "AUDUSD": {"yahoo": "AUDUSD=X", "base": "AUD", "quote": "USD", "pip": 0.0001, "spread": 1.3},
    "USDCAD": {"yahoo": "CAD=X",    "base": "USD", "quote": "CAD", "pip": 0.0001, "spread": 1.6},
}

# Simboli per convertire qualsiasi valuta in EUR (prezzo = quanti X per 1 EUR)
EUR_CROSSES = {
    "USD": "EURUSD=X", "GBP": "EURGBP=X", "JPY": "EURJPY=X",
    "CHF": "EURCHF=X", "CAD": "EURCAD=X", "AUD": "EURAUD=X",
}

CONTRACT_SIZE = 100_000   # 1 lotto standard
MIN_LOT = 0.01
LOT_STEP = 0.01

# Parametri della strategia di default (la calibrazione può sovrascriverli per coppia)
DEFAULT_PARAMS = {
    "ema_fast_h4": 50,
    "ema_slow_h4": 200,
    "adx_min": 22,          # forza minima del trend sul 4 ore
    "ema_pullback": 50,     # media su cui aspettiamo il ritracciamento (1h)
    "pullback_atr": 0.6,    # quanto vicino alla media deve arrivare il prezzo (in ATR)
    "rsi_reset": 45,        # l'RSI deve essere sceso sotto (long) / salito sopra (short) questo livello
    "sl_atr_min": 1.0,
    "sl_atr_max": 3.0,
    "rr": 2.0,              # take profit = rr volte lo stop
    "breakeven_r": 1.0,     # sposta lo stop a pareggio quando il trade guadagna 1R
    "score_min": 60,
    "session_start": 7,     # ore UTC in cui si aprono nuovi trade (Londra + New York)
    "session_end": 19,
}

# Freni di sicurezza
MAX_OPEN_POSITIONS = 3
MAX_PER_CURRENCY = 2         # es. non più di 2 posizioni che dipendono dal dollaro nello stesso verso
DAILY_LOSS_LIMIT_PCT = 3.0   # se il conto perde il 3% in un giorno, stop fino a domani
WEEKLY_LOSS_LIMIT_PCT = 6.0
NEWS_BLOCK_MINUTES = 60      # nessun nuovo trade +-60 minuti da una notizia ad alto impatto
SIGNAL_VALID_MINUTES = 30

DEFAULT_SETTINGS = {
    "capitale_iniziale": 10000.0,
    "leva": 30,
    "rischio_pct": 1.0,
    "valuta": "EUR",
    "pausa": False,
    "coppie_attive": list(PAIRS.keys()),
    "notifiche_apertura": True,
    "notifiche_chiusura": True,
    "report_settimanale": True,
    "reset_conto": False,
}
