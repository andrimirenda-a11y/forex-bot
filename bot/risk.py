"""Calcoli di rischio: valore del pip in EUR, lotti, margine, perdita/guadagno potenziali."""
import math

from .config import CONTRACT_SIZE, LOT_STEP, MIN_LOT, PAIRS


def to_eur(amount: float, currency: str, eur_rates: dict) -> float:
    """eur_rates[X] = quanti X vale 1 EUR (es. USD: 1.08)."""
    if currency == "EUR":
        return amount
    return amount / eur_rates[currency]


def contract(pair: str) -> float:
    return PAIRS[pair].get("contract", CONTRACT_SIZE)


def leverage_for(pair: str, leverage: float) -> float:
    """Leva effettiva: quella scelta, ma mai oltre il limite UE dello strumento (oro/indici 20, argento 10)."""
    return max(1.0, min(float(leverage), PAIRS[pair].get("max_lev", 30)))


def pip_value_eur(pair: str, eur_rates: dict, lots: float = 1.0) -> float:
    """Valore in EUR di 1 pip (o punto) per `lots` lotti."""
    p = PAIRS[pair]
    return to_eur(contract(pair) * lots * p["pip"], p["quote"], eur_rates)


def notional_eur(pair: str, lots: float, eur_rates: dict, price: float | None = None) -> float:
    p = PAIRS[pair]
    if p.get("kind") == "cfd":       # CFD: valore = unità x prezzo, nella valuta di quotazione
        return to_eur(contract(pair) * lots * (price or 0.0), p["quote"], eur_rates)
    return to_eur(CONTRACT_SIZE * lots, p["base"], eur_rates)


def margin_eur(pair: str, lots: float, leverage: float, eur_rates: dict, price: float | None = None) -> float:
    return notional_eur(pair, lots, eur_rates, price) / leverage_for(pair, leverage)


def size_position(pair: str, entry: float, sl: float, equity: float, free_margin: float,
                  settings: dict, eur_rates: dict) -> dict:
    """Calcola i lotti in modo che toccando lo stop si perda esattamente rischio_pct del conto.
    Restituisce dict con lots (0 se il trade va saltato) e tutti i dettagli + eventuale avviso."""
    p = PAIRS[pair]
    sl_pips = abs(entry - sl) / p["pip"]
    pv1 = pip_value_eur(pair, eur_rates, 1.0)
    risk_target = equity * settings["rischio_pct"] / 100
    raw = risk_target / (sl_pips * pv1) if sl_pips > 0 else 0
    lots = math.floor(raw / LOT_STEP + 1e-9) * LOT_STEP
    note = None
    if lots < MIN_LOT:
        min_risk = sl_pips * pv1 * MIN_LOT
        if min_risk <= risk_target * 2.0:
            lots = MIN_LOT
            note = (f"Capitale piccolo: con il lotto minimo rischi {min_risk:.2f} € "
                    f"({min_risk / equity * 100:.1f}% del conto) invece di {risk_target:.2f} €.")
        else:
            return {"lots": 0, "sl_pips": sl_pips, "skip": (
                f"Stop troppo largo per il capitale: anche 0,01 lotti rischierebbero {min_risk:.2f} € "
                f"({min_risk / equity * 100:.1f}% del conto).")}
    lev = leverage_for(pair, settings["leva"])
    m = margin_eur(pair, lots, lev, eur_rates, entry)
    # non usare più di metà del margine libero per un singolo trade
    if m > free_margin * 0.5:
        max_lots = math.floor((free_margin * 0.5 * lev / notional_eur(pair, 1.0, eur_rates, entry)) / LOT_STEP) * LOT_STEP
        if max_lots < MIN_LOT:
            return {"lots": 0, "sl_pips": sl_pips, "skip": (
                f"Margine insufficiente con leva 1:{lev}: servirebbero {m:.2f} € di margine.")}
        note = (note + " " if note else "") + (
            f"Lotti ridotti da {lots:.2f} a {max_lots:.2f} perché con leva 1:{lev} il margine non basta.")
        lots = max_lots
        m = margin_eur(pair, lots, lev, eur_rates, entry)
    lots = round(lots, 2)
    pv = pv1 * lots
    return {
        "lots": lots,
        "sl_pips": round(sl_pips, 1),
        "pip_value_eur": round(pv, 4),
        "risk_eur": round(sl_pips * pv, 2),
        "margin_eur": round(m, 2),
        "note": note,
        "skip": None,
    }
