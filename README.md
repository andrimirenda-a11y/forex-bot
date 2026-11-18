# Forex Bot

Bot di trading forex su **conto virtuale** con notifiche Telegram e app installabile.
Firmato Andrea Pietro Mirenda.

## Come funziona
- **Cervello** (`bot/`): gira gratis su GitHub Actions ogni 10 minuti. Scarica i prezzi reali
  (EUR/USD, GBP/USD, EUR/GBP, USD/JPY, USD/CHF, AUD/USD, USD/CAD), cerca segnali
  (trend sul 4 ore + ritracciamento e ripartenza a 1 ora), filtra le notizie importanti,
  calcola lotti/margine su capitale, leva e rischio, e apre/chiude le posizioni sul conto virtuale.
- **Telegram** (@drew_forex_segnali_bot): avvisi di apertura/chiusura, report ogni domenica, comandi.
- **App**: https://andrimirenda-a11y.github.io/forex-bot/ — conto, posizioni, grafici live, statistiche, impostazioni.
- **Calibrazione** (`bot/calibrate.py`): ogni sabato rifà il backtest su ~2 anni di dati orari,
  sceglie i parametri sul 70% dei dati e li verifica sul 30% mai visto; tiene accese solo le coppie che reggono.

## Freni di sicurezza
Rischio fisso per trade, max 3 posizioni, max 2 sulla stessa valuta nello stesso verso,
stop a -3% giornaliero e -6% settimanale, pausa automatica delle coppie che dal vivo vanno
molto peggio del previsto, comando /stop.

## Comandi Telegram
/stato /posizioni /statistiche /impostazioni /capitale 2000 /leva 30 /rischio 1 /pausa /riprendi /stop /aiuto

## File dati
Vedi `SCHEMA.md`. Test: `python -m pytest -q`.

⚠️ Conto virtuale a scopo di prova. I risultati passati non garantiscono quelli futuri.
