/* Forex Bot - funzioni pure (calcoli, formattazione).
   Nessun accesso al DOM: usabile sia nel browser (window.Core) sia in Node (require). */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Core = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var PAIRS = ["EURUSD", "GBPUSD", "EURGBP", "USDJPY", "USDCHF", "AUDUSD", "USDCAD"];
  var LEVERAGES = [1, 2, 5, 10, 20, 30, 50, 100, 200, 500];
  var CONTRACT_SIZE = 100000;
  var MIN_LOT = 0.01;

  function isNum(x) { return typeof x === "number" && isFinite(x); }
  function num(x, d) { var n = Number(x); return isFinite(n) ? n : (d === undefined ? 0 : d); }

  /* ---------- formattazione ---------- */
  var eurFmt = null, eurFmt0 = null;
  function fmtEur(v, opts) {
    if (!isNum(v)) return "—";
    opts = opts || {};
    try {
      if (!eurFmt) {
        eurFmt = new Intl.NumberFormat("it-IT", { style: "currency", currency: "EUR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
        eurFmt0 = new Intl.NumberFormat("it-IT", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
      }
      var s = (opts.noDecimals ? eurFmt0 : eurFmt).format(v);
      if (opts.sign && v > 0) s = "+" + s;
      return s;
    } catch (e) {
      return (opts.sign && v > 0 ? "+" : "") + v.toFixed(2) + " €";
    }
  }
  function fmtNum(v, dec) {
    if (!isNum(v)) return "—";
    dec = dec === undefined ? 2 : dec;
    try {
      return new Intl.NumberFormat("it-IT", { minimumFractionDigits: dec, maximumFractionDigits: dec }).format(v);
    } catch (e) { return v.toFixed(dec); }
  }
  function fmtPct(v, dec, sign) {
    if (!isNum(v)) return "—";
    var s = fmtNum(v, dec === undefined ? 2 : dec) + "%";
    return (sign && v > 0 ? "+" : "") + s;
  }
  /* percentuale "corta": 1 → "1%", 0,25 → "0,25%", 1,5 → "1,5%" */
  function fmtPctShort(v) {
    if (!isNum(v)) return "—";
    return fmtNum(v, 2).replace(/,00$/, "").replace(/(,\d)0$/, "$1") + "%";
  }
  function pipSize(pair) { return /JPY$/.test(pair || "") ? 0.01 : 0.0001; }
  function priceDigits(pair) { return /JPY$/.test(pair || "") ? 3 : 5; }
  function fmtPrice(v, pair) {
    if (!isNum(v)) return "—";
    return v.toFixed(priceDigits(pair));
  }
  function fmtPair(pair) {
    pair = String(pair || "");
    return pair.length === 6 ? pair.slice(0, 3) + "/" + pair.slice(3) : pair;
  }
  function fmtLots(v) { return isNum(v) ? fmtNum(v, 2) : "—"; }

  function parseTime(iso) {
    if (!iso) return null;
    var t = Date.parse(iso);
    return isFinite(t) ? t : null;
  }
  function fmtDateTime(iso) {
    var t = parseTime(iso);
    if (t === null) return "—";
    var d = new Date(t);
    try {
      return d.toLocaleString("it-IT", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
    } catch (e) { return d.toISOString().slice(0, 16).replace("T", " "); }
  }
  function fmtDate(iso) {
    var t = parseTime(iso);
    if (t === null) return "—";
    try { return new Date(t).toLocaleDateString("it-IT", { day: "2-digit", month: "short", year: "numeric" }); }
    catch (e) { return new Date(t).toISOString().slice(0, 10); }
  }
  /* "adesso", "3 min fa", "2 ore fa", "5 giorni fa" */
  function timeAgo(iso, now) {
    var t = parseTime(iso);
    if (t === null) return "mai";
    now = now === undefined ? Date.now() : now;
    var s = Math.max(0, Math.round((now - t) / 1000));
    if (s < 60) return "adesso";
    var m = Math.floor(s / 60);
    if (m < 60) return m + " min fa";
    var h = Math.floor(m / 60);
    if (h < 24) return h === 1 ? "1 ora fa" : h + " ore fa";
    var d = Math.floor(h / 24);
    return d === 1 ? "1 giorno fa" : d + " giorni fa";
  }
  function minutesSince(iso, now) {
    var t = parseTime(iso);
    if (t === null) return null;
    now = now === undefined ? Date.now() : now;
    return (now - t) / 60000;
  }
  function fmtDuration(fromIso, toIso) {
    var a = parseTime(fromIso), b = parseTime(toIso);
    if (a === null || b === null) return "—";
    var m = Math.max(0, Math.round((b - a) / 60000));
    if (m < 60) return m + " min";
    var h = Math.floor(m / 60);
    if (h < 48) return h + " h " + (m % 60) + " min";
    return Math.floor(h / 24) + " g " + (h % 24) + " h";
  }

  /* ---------- posizioni ---------- */
  /* Posizione del prezzo tra SL (0) e TP (1); vale sia per BUY che per SELL. */
  function slTpFraction(price, sl, tp) {
    if (!isNum(price) || !isNum(sl) || !isNum(tp) || sl === tp) return null;
    var f = (price - sl) / (tp - sl);
    return Math.max(0, Math.min(1, f));
  }
  function pipsBetween(a, b, pair) {
    if (!isNum(a) || !isNum(b)) return null;
    return Math.abs(a - b) / pipSize(pair);
  }

  /* ---------- statistiche dai trade chiusi ---------- */
  function computeStats(history) {
    var list = Array.isArray(history) ? history.filter(function (t) { return t && isNum(Number(t.pnl_eur)); }) : [];
    var out = {
      trades: list.length, wins: 0, losses: 0, breakeven: 0,
      winRate: null, profitFactor: null, net: 0, grossWin: 0, grossLoss: 0,
      avgWin: null, avgLoss: null, best: null, worst: null,
      maxWinStreak: 0, maxLossStreak: 0, currentStreak: 0, currentStreakType: null,
      netPips: 0, avgR: null, byResult: { TP: 0, SL: 0, BE: 0, STOP_MANUALE: 0 },
      perPair: {}
    };
    if (!list.length) return out;
    var sorted = list.slice().sort(function (a, b) {
      return (parseTime(a.closed_at) || 0) - (parseTime(b.closed_at) || 0);
    });
    var winStreak = 0, lossStreak = 0, sumR = 0, nR = 0;
    sorted.forEach(function (t) {
      var p = Number(t.pnl_eur);
      out.net += p;
      out.netPips += num(t.pnl_pips);
      if (isNum(Number(t.r_multiple)) && t.r_multiple !== null) { sumR += Number(t.r_multiple); nR++; }
      if (out.byResult[t.result] !== undefined) out.byResult[t.result]++;
      var kind = p > 0.005 ? "W" : (p < -0.005 ? "L" : "B");
      if (kind === "W") { out.wins++; out.grossWin += p; winStreak++; lossStreak = 0; }
      else if (kind === "L") { out.losses++; out.grossLoss += -p; lossStreak++; winStreak = 0; }
      else { out.breakeven++; }
      if (winStreak > out.maxWinStreak) out.maxWinStreak = winStreak;
      if (lossStreak > out.maxLossStreak) out.maxLossStreak = lossStreak;
      if (!out.best || p > Number(out.best.pnl_eur)) out.best = t;
      if (!out.worst || p < Number(out.worst.pnl_eur)) out.worst = t;

      var pp = out.perPair[t.pair] || (out.perPair[t.pair] = { pair: t.pair, trades: 0, wins: 0, losses: 0, net: 0, grossWin: 0, grossLoss: 0, pips: 0 });
      pp.trades++; pp.net += p; pp.pips += num(t.pnl_pips);
      if (kind === "W") { pp.wins++; pp.grossWin += p; }
      if (kind === "L") { pp.losses++; pp.grossLoss += -p; }
    });
    // serie in corso (ignora i pareggi)
    for (var i = sorted.length - 1; i >= 0; i--) {
      var pv = Number(sorted[i].pnl_eur);
      var k = pv > 0.005 ? "W" : (pv < -0.005 ? "L" : "B");
      if (k === "B") continue;
      if (out.currentStreakType === null) { out.currentStreakType = k; out.currentStreak = 1; }
      else if (k === out.currentStreakType) out.currentStreak++;
      else break;
    }
    var decided = out.wins + out.losses;
    out.winRate = decided ? (out.wins / list.length) * 100 : 0;
    out.profitFactor = out.grossLoss > 0 ? out.grossWin / out.grossLoss : (out.grossWin > 0 ? Infinity : null);
    out.avgWin = out.wins ? out.grossWin / out.wins : null;
    out.avgLoss = out.losses ? -out.grossLoss / out.losses : null;
    out.avgR = nR ? sumR / nR : null;
    Object.keys(out.perPair).forEach(function (k2) {
      var q = out.perPair[k2];
      q.winRate = q.trades ? (q.wins / q.trades) * 100 : 0;
      q.profitFactor = q.grossLoss > 0 ? q.grossWin / q.grossLoss : (q.grossWin > 0 ? Infinity : null);
    });
    return out;
  }

  /* Massimo drawdown % sulla curva equity: [{t, equity}] */
  function maxDrawdownPct(equity) {
    if (!Array.isArray(equity) || !equity.length) return null;
    var peak = -Infinity, maxDd = 0;
    equity.forEach(function (p) {
      var e = Number(p && p.equity);
      if (!isNum(e)) return;
      if (e > peak) peak = e;
      if (peak > 0) {
        var dd = (peak - e) / peak * 100;
        if (dd > maxDd) maxDd = dd;
      }
    });
    return peak === -Infinity ? null : maxDd;
  }

  /* P&L di oggi (giornata locale): equity attuale - equity all'ultimo punto prima della mezzanotte. */
  function todayPnl(equityCurve, currentEquity, startBalance, now) {
    now = now === undefined ? Date.now() : now;
    if (!isNum(currentEquity)) return null;
    var d = new Date(now);
    var midnight = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    var ref = null;
    if (Array.isArray(equityCurve)) {
      for (var i = 0; i < equityCurve.length; i++) {
        var t = parseTime(equityCurve[i].t);
        var e = Number(equityCurve[i].equity);
        if (t === null || !isNum(e)) continue;
        if (t < midnight) ref = e;
        else { if (ref === null) ref = e; break; }
      }
    }
    if (ref === null) ref = isNum(startBalance) ? startBalance : null;
    return ref === null ? null : currentEquity - ref;
  }

  /* ---------- calcolatore lotti (EUR/USD) ----------
     capitale €, rischio %, leva, stop in pip, prezzo EURUSD.
     Valore pip EURUSD per 1 lotto = 10 USD = 10 / EURUSD €. Margine = lotti * 100000 € / leva. */
  function lotCalc(o) {
    var capital = num(o.capital), riskPct = num(o.riskPct), lev = num(o.leverage, 30) || 1;
    var stop = num(o.stopPips, 20) || 20;
    var eurusd = num(o.eurusd, 1.08);
    if (!(eurusd > 0.5 && eurusd < 2.5)) eurusd = 1.08;
    var pipValEur = 10 / eurusd;            // € per pip per 1 lotto
    var riskTarget = capital * riskPct / 100;
    var raw = riskTarget / (stop * pipValEur);
    var lots = Math.floor(raw / MIN_LOT + 1e-9) * MIN_LOT;
    lots = Math.round(lots * 100) / 100;
    var minRisk = MIN_LOT * stop * pipValEur;
    var tooSmall = lots < MIN_LOT;
    var useLots = tooSmall ? MIN_LOT : lots;
    var riskEur = useLots * stop * pipValEur;
    var marginEur = useLots * CONTRACT_SIZE / lev;
    return {
      lots: useLots, rawLots: raw, riskTarget: riskTarget, riskEur: riskEur,
      riskPctReal: capital > 0 ? riskEur / capital * 100 : null,
      marginEur: marginEur, marginPct: capital > 0 ? marginEur / capital * 100 : null,
      pipValEur: pipValEur, minRiskEur: minRisk, tooSmall: tooSmall,
      marginTooHigh: capital > 0 && marginEur > capital, eurusd: eurusd,
      minCapitalForRisk: riskPct > 0 ? minRisk / (riskPct / 100) : null
    };
  }

  /* ---------- grafico: geometria della linea ----------
     points: [{x: ms, y: valore}] → {path, area, xs, ys, min, max} in un box w×h con padding. */
  function lineGeometry(points, w, h, pad) {
    pad = pad || { t: 8, r: 8, b: 8, l: 8 };
    var pts = (points || []).filter(function (p) { return isNum(p.x) && isNum(p.y); });
    if (pts.length < 2) return null;
    var minX = pts[0].x, maxX = pts[pts.length - 1].x;
    var minY = Infinity, maxY = -Infinity;
    pts.forEach(function (p) { if (p.y < minY) minY = p.y; if (p.y > maxY) maxY = p.y; });
    if (maxY === minY) { maxY += 1; minY -= 1; }
    var span = maxY - minY;
    minY -= span * 0.08; maxY += span * 0.08;
    if (maxX === minX) maxX = minX + 1;
    var iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
    var sx = function (x) { return pad.l + (x - minX) / (maxX - minX) * iw; };
    var sy = function (y) { return pad.t + (1 - (y - minY) / (maxY - minY)) * ih; };
    var xs = [], ys = [], d = "";
    pts.forEach(function (p, i) {
      var X = sx(p.x), Y = sy(p.y);
      xs.push(X); ys.push(Y);
      d += (i ? "L" : "M") + X.toFixed(1) + " " + Y.toFixed(1);
    });
    var base = (pad.t + ih).toFixed(1);
    var area = d + "L" + xs[xs.length - 1].toFixed(1) + " " + base + "L" + xs[0].toFixed(1) + " " + base + "Z";
    return { path: d, area: area, xs: xs, ys: ys, pts: pts, min: minY, max: maxY, sx: sx, sy: sy };
  }

  /* Riduce una serie lunga a ~max punti (mantiene primo e ultimo). */
  function downsample(arr, max) {
    if (!Array.isArray(arr) || arr.length <= max) return arr || [];
    var out = [], step = (arr.length - 1) / (max - 1);
    for (var i = 0; i < max; i++) out.push(arr[Math.round(i * step)]);
    return out;
  }

  /* ---------- impostazioni ---------- */
  var DEFAULT_SETTINGS = {
    capitale_iniziale: 10000.0, leva: 30, rischio_pct: 1.0, valuta: "EUR", pausa: false,
    coppie_attive: PAIRS.slice(), notifiche_apertura: true, notifiche_chiusura: true,
    report_settimanale: true, reset_conto: false
  };
  function normalizeSettings(s) {
    var out = {};
    var k;
    for (k in DEFAULT_SETTINGS) out[k] = Array.isArray(DEFAULT_SETTINGS[k]) ? DEFAULT_SETTINGS[k].slice() : DEFAULT_SETTINGS[k];
    if (s && typeof s === "object") for (k in s) if (Object.prototype.hasOwnProperty.call(s, k)) out[k] = s[k];
    out.capitale_iniziale = num(out.capitale_iniziale, 10000);
    out.leva = num(out.leva, 30);
    out.rischio_pct = num(out.rischio_pct, 1);
    if (!Array.isArray(out.coppie_attive)) out.coppie_attive = PAIRS.slice();
    return out;
  }
  function validateSettings(s) {
    var errs = [];
    if (!(s.capitale_iniziale >= 100 && s.capitale_iniziale <= 10000000)) errs.push("Il capitale deve essere tra 100 € e 10.000.000 €.");
    if (LEVERAGES.indexOf(Number(s.leva)) < 0) errs.push("Leva non valida.");
    if (!(s.rischio_pct >= 0.25 && s.rischio_pct <= 5)) errs.push("Il rischio deve essere tra 0,25% e 5%.");
    if (!s.coppie_attive || !s.coppie_attive.length) errs.push("Seleziona almeno una coppia.");
    return errs;
  }

  /* base64 di una stringa UTF-8 (per l'API GitHub) */
  function b64EncodeUtf8(str) {
    if (typeof Buffer !== "undefined" && typeof btoa === "undefined") return Buffer.from(str, "utf8").toString("base64");
    var bytes = new TextEncoder().encode(str), bin = "";
    for (var i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(bin);
  }
  function b64DecodeUtf8(b64) {
    b64 = String(b64 || "").replace(/\s+/g, "");
    if (typeof Buffer !== "undefined" && typeof atob === "undefined") return Buffer.from(b64, "base64").toString("utf8");
    var bin = atob(b64), bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder().decode(bytes);
  }

  return {
    PAIRS: PAIRS, LEVERAGES: LEVERAGES, DEFAULT_SETTINGS: DEFAULT_SETTINGS,
    isNum: isNum, num: num,
    fmtEur: fmtEur, fmtNum: fmtNum, fmtPct: fmtPct, fmtPctShort: fmtPctShort, fmtPrice: fmtPrice, fmtPair: fmtPair, fmtLots: fmtLots,
    fmtDateTime: fmtDateTime, fmtDate: fmtDate, fmtDuration: fmtDuration, timeAgo: timeAgo, minutesSince: minutesSince,
    parseTime: parseTime, pipSize: pipSize, priceDigits: priceDigits,
    slTpFraction: slTpFraction, pipsBetween: pipsBetween,
    computeStats: computeStats, maxDrawdownPct: maxDrawdownPct, todayPnl: todayPnl,
    lotCalc: lotCalc, lineGeometry: lineGeometry, downsample: downsample,
    normalizeSettings: normalizeSettings, validateSettings: validateSettings,
    b64EncodeUtf8: b64EncodeUtf8, b64DecodeUtf8: b64DecodeUtf8
  };
});
