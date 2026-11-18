"""Invio messaggi e lettura comandi Telegram (API ufficiale dei bot, gratuita)."""
import os

import requests

API = "https://api.telegram.org/bot{token}/{method}"


def _token():
    return os.environ.get("TELEGRAM_TOKEN", "").strip()


def enabled() -> bool:
    return bool(_token())


def call(method: str, **params):
    tok = _token()
    if not tok:
        return None
    try:
        # per il long polling (getUpdates con timeout) l'attesa HTTP deve essere più lunga
        wait = 20 + int(params.get("timeout", 0) or 0)
        r = requests.post(API.format(token=tok, method=method), json=params, timeout=wait)
        data = r.json()
        if not data.get("ok"):
            print(f"[telegram] {method} errore: {data}")
            return None
        return data["result"]
    except Exception as e:  # noqa: BLE001
        print(f"[telegram] {method} eccezione: {e}")
        return None


def send(chat_id, text: str) -> bool:
    if not chat_id:
        print("[telegram] nessuna chat collegata, messaggio non inviato:\n" + text)
        return False
    # Telegram accetta max 4096 caratteri
    for i in range(0, len(text), 3900):
        res = call("sendMessage", chat_id=chat_id, text=text[i:i + 3900], parse_mode="HTML",
                   disable_web_page_preview=True)
        if res is None:
            # riprova senza formattazione se l'HTML non è valido
            call("sendMessage", chat_id=chat_id, text=text[i:i + 3900])
    return True


def updates(offset: int):
    return call("getUpdates", offset=offset, timeout=0, allowed_updates=["message"]) or []


def set_commands():
    cmds = [
        ("stato", "Conto, posizioni aperte e stato del bot"),
        ("posizioni", "Dettaglio delle posizioni aperte"),
        ("statistiche", "Risultati: vincite, profitto, drawdown"),
        ("capitale", "Imposta il capitale, es. /capitale 2000"),
        ("leva", "Imposta la leva, es. /leva 30"),
        ("rischio", "Rischio per trade in %, es. /rischio 1"),
        ("pausa", "Nessun nuovo trade finché non riprendi"),
        ("riprendi", "Riattiva il bot e le coppie in pausa"),
        ("stop", "Chiude tutte le posizioni e mette in pausa"),
        ("impostazioni", "Mostra le impostazioni attuali"),
        ("aiuto", "Elenco comandi"),
    ]
    call("setMyCommands", commands=[{"command": c, "description": d} for c, d in cmds])
