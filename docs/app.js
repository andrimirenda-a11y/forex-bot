/* Forex Bot - logica dell'interfaccia. Richiede config.js e core.js caricati prima. */
(function () {
  "use strict";

  var C = window.Core;
  var CFG = window.APP_CONFIG || {};
  var QS = new URLSearchParams(location.search);
  var DEMO = QS.get("demo") === "1";
  var FILES = ["state", "settings", "history", "equity", "signals", "calibration", "backtest", "log", "study"];
  var REFRESH_MS = 60000;
  var STALE_MIN = 40;
  var TOKEN_KEY = "fxbot_gh_token";
  var SAVED_KEY = "fxbot_last_saved";
  var PAIR_KEY = "fxbot_chart_pair";
  var RANGE_KEY = "fxbot_eq_range";
  var TV_SYMBOLS = { EURUSD: "FX:EURUSD", GBPUSD: "FX:GBPUSD", EURGBP: "FX:EURGBP", USDJPY: "FX:USDJPY", USDCHF: "FX:USDCHF", AUDUSD: "FX:AUDUSD", USDCAD: "FX:USDCAD",
    XAUUSD: "OANDA:XAUUSD", XAGUSD: "OANDA:XAGUSD", US500: "OANDA:SPX500USD", US30: "OANDA:US30USD" };

  /* localStorage protetto da try/catch (può essere disabilitato) */
  var store = {
    get: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { window.localStorage.setItem(k, v); return true; } catch (e) { return false; } },
    del: function (k) { try { window.localStorage.removeItem(k); } catch (e) { /* ignora */ } }
  };

  var data = {};            // contenuto dei file JSON (null se mancante)
  var loadErrors = {};      // name -> "missing" | "network" | "parse"
  var loading = false;
  var lastLoad = 0;
  var everLoaded = false;
  var currentTab = "conto";
  var settingsDirty = false;
  var historyShown = 30;
  var eqRange = store.get(RANGE_KEY) || "all";
  var chartPair = store.get(PAIR_KEY) || "EURUSD";
  var tvLoaded = false, tvLoading = false, tvPairShown = null;
  var chartSeq = 0;

  /* ================= utilità ================= */
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function cls(v) { return !C.isNum(v) || Math.abs(v) < 0.005 ? "" : (v > 0 ? "pos" : "neg"); }
  function arr(x) { return Array.isArray(x) ? x : []; }
  function obj(x) { return x && typeof x === "object" && !Array.isArray(x) ? x : {}; }
  function n(x) { var v = Number(x); return x === null || x === undefined || x === "" || !isFinite(v) ? null : v; }

  var toastTimer = null;
  function toast(msg) {
    var t = $("toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, 2800);
  }

  function emptyState(title, text, icon) {
    return '<div class="card empty"><div class="empty-icon">' + (icon || ICONS.clock) + '</div>' +
      '<div class="empty-title">' + esc(title) + '</div><p>' + esc(text) + '</p></div>';
  }
  var ICONS = {
    clock: '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    list: '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h10"/></svg>',
    chart: '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l6-6 4 4 8-8M15 7h6v6"/></svg>',
    radar: '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/></svg>'
  };

  /* ================= caricamento dati ================= */
  function dataUrl(name) {
    var t = Date.now();
    if (DEMO) return "sample-data/" + name + ".json?t=" + t;
    return "https://raw.githubusercontent.com/" + encodeURIComponent(CFG.owner || "") + "/" +
      encodeURIComponent(CFG.repo || "") + "/" + encodeURIComponent(CFG.branch || "main") +
      "/data/" + name + ".json?t=" + t;
  }

  function fetchJson(name) {
    return fetch(dataUrl(name), { cache: "no-store" }).then(function (r) {
      if (r.status === 404) return { name: name, value: null, err: "missing" };
      if (!r.ok) return { name: name, value: null, err: "network" };
      return r.text().then(function (txt) {
        if (!txt || !txt.trim()) return { name: name, value: null, err: "missing" };
        try { return { name: name, value: JSON.parse(txt), err: null }; }
        catch (e) { return { name: name, value: null, err: "parse" }; }
      });
    }).catch(function () { return { name: name, value: null, err: "network" }; });
  }

  function loadAll(manual) {
    if (loading) return Promise.resolve();
    loading = true;
    $("refreshBtn").classList.add("spinning");
    return Promise.all(FILES.map(fetchJson)).then(function (results) {
      var networkFails = 0;
      results.forEach(function (res) {
        if (res.err === "network") {
          networkFails++;
          // in caso di errore di rete teniamo i dati precedenti
          if (!(res.name in data)) data[res.name] = null;
        } else {
          data[res.name] = res.value;
        }
        loadErrors[res.name] = res.err;
      });
      applyLocalSettingsOverride();
      lastLoad = Date.now();
      everLoaded = true;
      var banner = $("globalBanner");
      if (networkFails === FILES.length) {
        banner.className = "banner banner-err";
        banner.textContent = "Impossibile scaricare i dati. Controlla la connessione: riproverò tra un minuto.";
        banner.hidden = false;
      } else if (loadErrors.state === "parse") {
        banner.className = "banner banner-warn";
        banner.textContent = "Il file dello stato del bot è illeggibile in questo momento (forse è in aggiornamento). Riprova tra poco.";
        banner.hidden = false;
      } else {
        banner.hidden = true;
      }
      renderAll();
      if (manual) toast(networkFails === FILES.length ? "Aggiornamento non riuscito" : "Dati aggiornati");
    }).catch(function (e) {
      console.error(e);
    }).then(function () {
      loading = false;
      $("refreshBtn").classList.remove("spinning");
    });
  }

  /* raw.githubusercontent.com può restituire per qualche minuto la versione vecchia:
     dopo un salvataggio dall'app usiamo la copia locale per 10 minuti. */
  function applyLocalSettingsOverride() {
    var raw = store.get(SAVED_KEY);
    if (!raw) return;
    try {
      var saved = JSON.parse(raw);
      if (!saved || !saved.t || Date.now() - saved.t > 10 * 60000) { store.del(SAVED_KEY); return; }
      data.settings = saved.settings;
    } catch (e) { store.del(SAVED_KEY); }
  }

  /* ================= stato del bot ================= */
  function botStatus() {
    var st = data.state, s = data.settings;
    if (!st) return { cls: "off", label: "Non avviato" };
    var until = C.parseTime(st.trading_halted_until);
    if (until && until > Date.now()) {
      return { cls: "halted", label: "Bloccato", note: "Bloccato fino a " + C.fmtDateTime(st.trading_halted_until) + (st.halt_reason ? " — " + st.halt_reason : "") };
    }
    if (s && s.pausa) return { cls: "paused", label: "In pausa" };
    return { cls: "on", label: "Attivo" };
  }
  function isWeekendMarketClosed(now) {
    var d = new Date(now || Date.now());
    var day = d.getUTCDay(), h = d.getUTCHours();
    return day === 6 || (day === 0 && h < 22) || (day === 5 && h >= 22);
  }
  function staleInfo() {
    var st = data.state;
    if (!st || !st.last_run) return null;
    var m = C.minutesSince(st.last_run);
    if (m === null || m <= STALE_MIN) return null;
    return { minutes: m, weekend: isWeekendMarketClosed() };
  }
  function lastPrice(pair) {
    var lp = obj(data.state && data.state.last_prices)[pair];
    if (lp === null || lp === undefined) return null;
    if (typeof lp === "number") return lp;
    return n(lp.price);
  }

  /* ================= header ================= */
  function renderHeader() {
    var lbl = $("updatedLabel");
    var st = data.state;
    lbl.classList.remove("stale");
    if (!everLoaded) { lbl.textContent = "caricamento…"; return; }
    if (!st || !st.last_run) { lbl.textContent = "bot non ancora avviato"; return; }
    lbl.textContent = "aggiornato " + C.timeAgo(st.last_run);
    if (staleInfo()) lbl.classList.add("stale");
    var cnt = arr(st.open_positions).length;
    var badge = $("posBadge");
    badge.hidden = !cnt;
    badge.textContent = cnt;
  }

  /* ================= CONTO ================= */
  function renderConto() {
    var box = $("contoBody");
    var st = data.state, s = data.settings || {};
    if (!everLoaded) {
      box.innerHTML = '<div class="skeleton" style="height:170px;margin-bottom:12px"></div><div class="skeleton" style="height:140px"></div>';
      return;
    }
    if (!st) {
      box.innerHTML = emptyState("Il bot non è ancora partito", "Tra pochi minuti qui vedrai il tuo conto finto.", ICONS.clock);
      return;
    }
    var equity = n(st.equity);
    var start = n(st.start_balance) || n(s.capitale_iniziale);
    var pnl = equity !== null && start ? equity - start : null;
    var pnlPct = pnl !== null && start ? pnl / start * 100 : null;
    var status = botStatus();
    var closedMkt = st.market_open === false || isWeekendMarketClosed();
    var pill = status.cls !== "on" ? status : (closedMkt ? { cls: "paused", label: "Mercato chiuso" } : status);
    var html = "";
    var stale = staleInfo();
    if (stale && !stale.weekend) {
      html += '<div class="banner banner-warn"><span>Il bot non si aggiorna da ' + esc(C.timeAgo(st.last_run).replace(" fa", "")) +
        ": di solito riparte da solo entro pochi minuti.</span></div>";
    }

    // 1) il conto
    html += '<div class="card hero">' +
      '<div class="hero-top"><span class="hero-label">Il tuo conto finto</span>' +
      '<span class="status ' + pill.cls + '">' + esc(pill.label) + "</span></div>" +
      '<div class="hero-value">' + C.fmtEur(equity) + "</div>" +
      '<div class="hero-change">' + chip(pnl, pnlPct) + '<span class="muted" style="font-size:13px;font-weight:500">da quando hai iniziato con ' +
      C.fmtEur(start, { noDecimals: true }) + "</span></div>" +
      (status.note ? '<div class="halt-note">' + esc(status.note) + "</div>" : "") +
      (st.status_line ? '<div class="now-text">' + esc(st.status_line) + "</div>" : "") + "</div>";

    // 2) operazioni aperte
    var open = arr(st.open_positions);
    html += '<div class="card"><h3 class="card-title">Operazioni aperte' + (open.length ? " (" + open.length + ")" : "") + "</h3>";
    if (!open.length) {
      html += '<div class="muted simple-empty">Nessuna in questo momento.' + (closedMkt ? "" : " Il bot cerca occasioni ogni mattina tra le 9 e le 12.") + "</div>";
    } else {
      html += open.map(function (p) {
        var eur = n(p.unrealized_eur);
        return simpleRow(p.side, C.fmtPair(p.pair),
          p.exit_at ? "chiude da sola alle " + C.fmtTime(p.exit_at) : "aperta " + C.timeAgo(p.opened_at),
          '<span class="' + cls(eur) + '">' + C.fmtEur(eur, { sign: true }) + "</span>", "goto-op");
      }).join("");
    }
    html += "</div>";

    // 3) ultime chiuse
    var closed = arr(data.history).slice(-5).reverse();
    if (closed.length) {
      html += '<div class="card"><h3 class="card-title">Ultime chiuse</h3>' + closed.map(closedRow).join("") +
        '<button type="button" class="link-btn goto-op">Vedi tutte</button></div>';
    }

    // 4) esame della strategia
    html += examCard();

    // 5) andamento
    html += '<div class="card chart-card"><h3 class="card-title">Andamento del conto</h3><div class="chart-box" id="eqChart"></div></div>';
    box.innerHTML = html;
    drawEquityChart();
  }

  var RESULT_TXT = { TP: ["🎯", "obiettivo raggiunto"], SL: ["🛑", "stop"], BE: ["⚖️", "chiusa a pareggio"],
    TEMPO: ["⏰", "chiusa a fine giornata"], STOP_MANUALE: ["✋", "chiusa da te"] };
  function simpleRow(side, name, sub, right, extraCls) {
    var buy = String(side).toUpperCase() === "BUY";
    return '<div class="srow ' + (extraCls || "") + '"><div><div class="srow-name">' + esc(name) +
      ' <span class="side ' + (buy ? "buy" : "sell") + '">' + (buy ? "COMPRA" : "VENDI") + "</span></div>" +
      '<div class="srow-sub">' + esc(sub) + '</div></div><div class="srow-right">' + right + "</div></div>";
  }
  function closedRow(t) {
    var r = RESULT_TXT[t.result] || ["•", t.result || ""];
    var eur = n(t.pnl_eur);
    var right = t.studio
      ? '<span class="' + cls(n(t.r_multiple)) + '">' + (n(t.r_multiple) > 0 ? "+" : "") + C.fmtNum(n(t.r_multiple), 2) + "R</span>"
      : '<span class="' + cls(eur) + '">' + C.fmtEur(eur, { sign: true }) + "</span>";
    return simpleRow(t.side, (t.studio ? "📝 " : "") + C.fmtPair(t.pair),
      r[0] + " " + r[1] + " · " + C.fmtDateTime(t.closed_at), right);
  }
  function examCard() {
    var real = arr(data.history), study = arr(data.study && data.study.closed);
    var all = real.concat(study);
    var nT = all.length, need = 30;
    var wins = all.filter(function (t) { return n(t.r_multiple) > 0; }).length;
    var sumR = all.reduce(function (a, t) { return a + (n(t.r_multiple) || 0); }, 0);
    var pct = Math.min(100, nT / need * 100);
    var verdict;
    if (nT < need) verdict = "Servono almeno " + need + " operazioni per giudicare. Mancano " + (need - nT) + ".";
    else if (sumR > 0) verdict = "✅ Finora guadagna: se resta così, si può pensare a un conto demo vero.";
    else verdict = "❌ Finora non guadagna: niente soldi veri.";
    return '<div class="card"><h3 class="card-title">Esame della strategia</h3>' +
      '<div class="exam-nums"><div><b>' + nT + "</b><span>operazioni</span></div>" +
      "<div><b>" + wins + "</b><span>vinte</span></div>" +
      "<div><b>" + (nT - wins) + "</b><span>perse</span></div>" +
      '<div><b class="' + cls(sumR) + '">' + (sumR > 0 ? "+" : "") + C.fmtNum(sumR, 1) + "R</b><span>risultato</span></div></div>" +
      '<div class="margin-bar"><div style="width:' + pct.toFixed(0) + '%"></div></div>' +
      '<div class="kpi-sub" style="margin-top:8px">' + esc(verdict) + "</div>" +
      (study.length ? '<div class="kpi-sub">Comprende ' + study.length + " operazioni di studio 📝 (segnali troppo grandi per il conto, seguiti sulla carta).</div>" : "") +
      '<div class="kpi-sub">R = quante volte il rischio: +2R vuol dire che hai guadagnato il doppio di quanto rischiavi.</div></div>';
  }
  function chip(v, pct) {
    if (v === null) return '<span class="chip flat">—</span>';
    var c = Math.abs(v) < 0.005 ? "flat" : (v > 0 ? "pos" : "neg");
    var arrow = c === "pos" ? "▲" : (c === "neg" ? "▼" : "");
    return '<span class="chip ' + c + '">' + arrow + " " + C.fmtEur(v, { sign: true }) + (pct !== null ? " (" + C.fmtPct(pct, 2, true) + ")" : "") + "</span>";
  }
  function kpi(label, value, sub) {
    return '<div class="kpi"><div class="kpi-label">' + label + '</div><div class="kpi-value">' + value + "</div>" +
      (sub ? '<div class="kpi-sub">' + sub + "</div>" : "") + "</div>";
  }

  function drawEquityChart() {
    var box = $("eqChart");
    if (!box) return;
    var pts = arr(data.equity).map(function (p) { return { x: C.parseTime(p && p.t), y: n(p && p.equity) }; })
      .filter(function (p) { return p.x !== null && p.y !== null; })
      .sort(function (a, b) { return a.x - b.x; });
    var st = data.state;
    if (st && n(st.equity) !== null && C.parseTime(st.last_run)) {
      var lr = C.parseTime(st.last_run);
      if (!pts.length || lr > pts[pts.length - 1].x) pts.push({ x: lr, y: n(st.equity) });
    }
    var ranges = { "1d": 864e5, "7d": 7 * 864e5, "30d": 30 * 864e5 };
    if (ranges[eqRange] && pts.length) {
      var from = Date.now() - ranges[eqRange];
      var inRange = pts.filter(function (p) { return p.x >= from; });
      // includi l'ultimo punto prima dell'intervallo come riferimento
      var before = pts.filter(function (p) { return p.x < from; });
      if (before.length) inRange.unshift(before[before.length - 1]);
      pts = inRange;
    }
    var start = n(st && st.start_balance);
    if (start) {   // mostra solo il periodo dopo l'ultimo cambio di capitale
      var k = pts.length;
      while (k > 0 && pts[k - 1].y > start * 0.3 && pts[k - 1].y < start * 3) k--;
      pts = pts.slice(k);
    }
    lineChart(box, C.downsample(pts, 400), {
      fmtY: function (v) { return C.fmtEur(v, { noDecimals: Math.abs(v) >= 1000 }); },
      fmtTip: function (v) { return C.fmtEur(v); },
      ref: start, refLabel: "capitale iniziale",
      empty: "Il grafico comparirà dopo le prime operazioni."
    });
  }

  /* Grafico a linea in SVG con crosshair (riutilizzato per equity e backtest) */
  function lineChart(box, pts, opt) {
    if (!pts || pts.length < 2) {
      box.innerHTML = '<div class="chart-empty">' + esc(opt.empty || "Dati insufficienti") + "</div>";
      return;
    }
    var w = Math.max(280, Math.round(box.clientWidth || (box.parentNode && box.parentNode.clientWidth) || 340));
    var h = opt.height || 200;
    var pad = { t: 12, r: 62, b: 24, l: 10 };
    var vals = pts.slice();
    var g = C.lineGeometry(vals, w, h, pad);
    if (!g) { box.innerHTML = '<div class="chart-empty">Dati insufficienti</div>'; return; }
    var up = vals[vals.length - 1].y >= vals[0].y;
    var color = up ? "var(--green)" : "var(--red)";
    var gid = "lg" + (++chartSeq);
    var ticks = [g.max - (g.max - g.min) * 0.08 / 1.16, (g.max + g.min) / 2, g.min + (g.max - g.min) * 0.08 / 1.16];
    var svg = '<svg viewBox="0 0 ' + w + " " + h + '" width="' + w + '" height="' + h + '" role="img" aria-label="Grafico">' +
      '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" stop-color="' + (up ? "#1fd18b" : "#ff5468") + '" stop-opacity=".28"/>' +
      '<stop offset="1" stop-color="' + (up ? "#1fd18b" : "#ff5468") + '" stop-opacity="0"/></linearGradient></defs>';
    ticks.forEach(function (tv) {
      var y = g.sy(tv).toFixed(1);
      svg += '<line class="chart-grid" x1="' + pad.l + '" x2="' + (w - pad.r + 4) + '" y1="' + y + '" y2="' + y + '"/>' +
        '<text class="chart-axis" x="' + (w - pad.r + 8) + '" y="' + (Number(y) + 3) + '">' + esc(opt.fmtY(tv)) + "</text>";
    });
    if (opt.ref !== null && opt.ref !== undefined && opt.ref > g.min && opt.ref < g.max) {
      var ry = g.sy(opt.ref).toFixed(1);
      svg += '<line x1="' + pad.l + '" x2="' + (w - pad.r) + '" y1="' + ry + '" y2="' + ry + '" stroke="#aab2c5" stroke-opacity=".45" stroke-width="1" stroke-dasharray="4 4"/>';
    }
    svg += '<path d="' + g.area + '" fill="url(#' + gid + ')"/>' +
      '<path d="' + g.path + '" fill="none" stroke="' + color + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>';
    var lx = g.xs[g.xs.length - 1], ly = g.ys[g.ys.length - 1];
    svg += '<circle cx="' + lx.toFixed(1) + '" cy="' + ly.toFixed(1) + '" r="4" fill="' + color + '" stroke="#131823" stroke-width="2"/>';
    var d0 = new Date(vals[0].x), d1 = new Date(vals[vals.length - 1].x);
    var shortFmt = function (d) {
      try { return d.toLocaleDateString("it-IT", { day: "2-digit", month: "short" }); } catch (e) { return d.toISOString().slice(5, 10); }
    };
    svg += '<text class="chart-axis" x="' + pad.l + '" y="' + (h - 6) + '">' + esc(shortFmt(d0)) + "</text>" +
      '<text class="chart-axis" x="' + (w - pad.r) + '" y="' + (h - 6) + '" text-anchor="end">' + esc(shortFmt(d1)) + "</text>";
    svg += '<line id="' + gid + 'x" x1="0" x2="0" y1="' + pad.t + '" y2="' + (h - pad.b) + '" stroke="#aab2c5" stroke-width="1" visibility="hidden"/>' +
      '<circle id="' + gid + 'd" r="5" fill="' + color + '" stroke="#131823" stroke-width="2" visibility="hidden"/>';
    svg += "</svg>";
    box.innerHTML = svg + '<div class="chart-tip" hidden></div>';

    var svgEl = box.querySelector("svg");
    var tip = box.querySelector(".chart-tip");
    var vline = svgEl.querySelector("#" + gid + "x");
    var dot = svgEl.querySelector("#" + gid + "d");
    function show(clientX) {
      var rect = svgEl.getBoundingClientRect();
      if (!rect.width) return;
      var x = (clientX - rect.left) * (w / rect.width);
      var best = 0, bd = Infinity;
      for (var i = 0; i < g.xs.length; i++) {
        var dd = Math.abs(g.xs[i] - x);
        if (dd < bd) { bd = dd; best = i; }
      }
      var px = g.xs[best], py = g.ys[best];
      vline.setAttribute("x1", px); vline.setAttribute("x2", px); vline.setAttribute("visibility", "visible");
      dot.setAttribute("cx", px); dot.setAttribute("cy", py); dot.setAttribute("visibility", "visible");
      var p = vals[best];
      tip.innerHTML = "<b>" + esc(opt.fmtTip(p.y)) + "</b>" + esc(C.fmtDateTime(new Date(p.x).toISOString()));
      tip.hidden = false;
      var left = px * (rect.width / w);
      var half = tip.offsetWidth / 2;
      left = Math.max(half, Math.min(rect.width - half, left));
      tip.style.left = left + "px";
    }
    function hide() {
      tip.hidden = true;
      vline.setAttribute("visibility", "hidden");
      dot.setAttribute("visibility", "hidden");
    }
    box.onpointermove = function (e) { if (e.pointerType === "mouse" || e.buttons || e.pressure > 0) show(e.clientX); };
    var hideT = null;
    box.onpointerdown = function (e) { clearTimeout(hideT); show(e.clientX); };
    // col dito pointerleave arriva subito dopo pointerup: nascondiamo il tooltip dopo un attimo
    box.onpointerleave = function (e) { if (e.pointerType === "mouse") hide(); };
    box.onpointercancel = function () { hideT = setTimeout(hide, 1200); };
    box.onpointerup = function (e) { if (e.pointerType !== "mouse") { clearTimeout(hideT); hideT = setTimeout(hide, 1800); } };
  }

  /* ================= POSIZIONI ================= */
  function renderPositions() {
    var st = data.state;
    var listBox = $("positionsList"), sigBox = $("signalsList");
    var positions = arr(st && st.open_positions);
    $("posCount").textContent = st ? positions.length : "";
    if (!everLoaded) { listBox.innerHTML = '<div class="skeleton" style="height:200px"></div>'; sigBox.innerHTML = ""; return; }
    if (!st) {
      listBox.innerHTML = emptyState("Nessun dato", "Il bot non è ancora partito: le posizioni compariranno qui.", ICONS.list);
    } else if (!positions.length) {
      var status = botStatus();
      var msg = status.cls === "on"
        ? "Il bot cerca occasioni ogni mattina tra le 9 e le 12 e le apre da solo."
        : (status.cls === "paused" ? "Il bot è in pausa: non apre nuovi trade." : (status.note || "Nessuna posizione aperta."));
      listBox.innerHTML = emptyState("Nessuna posizione aperta", msg, ICONS.radar);
    } else {
      listBox.innerHTML = positions.map(positionCard).join("");
    }

    var hist = arr(data.history).concat(arr(data.study && data.study.closed))
      .sort(function (a, b) { return (C.parseTime(b.closed_at) || 0) - (C.parseTime(a.closed_at) || 0); }).slice(0, 50);
    if (!hist.length) {
      sigBox.innerHTML = '<div class="card"><div class="muted simple-empty">Ancora nessuna operazione chiusa.</div></div>';
    } else {
      sigBox.innerHTML = '<div class="card">' + hist.map(closedRow).join("") + "</div>";
    }
  }

  function positionCard(p) {
    var pair = p.pair, buy = String(p.side).toUpperCase() === "BUY";
    var entry = n(p.entry), sl = n(p.sl), tp = n(p.tp);
    var price = n(p.last_price);
    if (price === null) price = lastPrice(pair);
    var pv = n(p.pip_value_eur), pip = C.pipSize(pair);
    var eur = n(p.unrealized_eur);
    if (eur === null && price !== null && entry !== null && pv !== null) eur = (price - entry) / pip * (buy ? 1 : -1) * pv;
    var win = tp !== null && entry !== null && pv !== null ? Math.abs(tp - entry) / pip * pv : null;
    var loss = n(p.risk_eur);
    var f = C.slTpFraction(price, sl, tp);
    var fe = C.slTpFraction(entry, sl, tp);
    var bet = buy ? "che salga" : "che scenda";

    var h = '<div class="card pos-card">' +
      '<div class="pos-head"><div><div class="pos-pair">' + esc(C.fmtPair(pair)) +
      '<span class="side ' + (buy ? "buy" : "sell") + '">' + (buy ? "COMPRA" : "VENDI") + "</span></div>" +
      '<div class="pos-meta">aperta ' + esc(C.timeAgo(p.opened_at)) + " · scommette " + bet + "</div></div>" +
      '<div class="pos-pnl"><div class="pos-pnl-label">adesso</div><div class="big ' + cls(eur) + '">' + C.fmtEur(eur, { sign: true }) + "</div></div></div>";

    // i tre finali possibili, in parole semplici
    h += '<div class="outcomes">' +
      '<div class="oc good"><span>🎯 Se arriva a ' + C.fmtPrice(tp, pair) + "</span><b>" + C.fmtEur(win, { sign: true }) + "</b></div>" +
      '<div class="oc bad"><span>🛑 Se scende a ' + C.fmtPrice(sl, pair) + "</span><b>" + C.fmtEur(loss !== null ? -loss : null, { sign: true }) + "</b></div>" +
      (p.exit_at ? '<div class="oc"><span>⏰ Se nessuno dei due, chiude da sola alle ' + esc(C.fmtTime(p.exit_at)) + "</span><b>quel che c'è</b></div>" : "") +
      "</div>";
    if (!buy) h = h.replace("🛑 Se scende a", "🛑 Se sale a");

    if (f !== null) {
      h += '<div class="track"><div class="track-bar">' +
        (fe !== null ? '<div class="track-entry" style="left:' + (fe * 100).toFixed(1) + '%"></div>' : "") +
        '<div class="track-dot ' + cls(eur) + '" style="left:' + (f * 100).toFixed(1) + '%"></div></div>' +
        '<div class="track-labels"><span class="sl">stop</span><span class="muted">prezzo ora ' + C.fmtPrice(price, pair) +
        '</span><span class="tp">obiettivo</span></div></div>';
    }
    if (p.reason) {
      h += '<div class="pos-reason"><b>Perché l\'ho aperta:</b> ' + esc(p.reason) + "</div>";
    }
    return h + "</div>";
  }
  function cell(k, v) { return "<div><span>" + esc(k) + "</span><b>" + v + "</b></div>"; }

  function signalRow(s) {
    var buy = String(s.side).toUpperCase() === "BUY";
    var opened = s.status === "aperto";
    return '<div class="sig"><div class="sig-top"><div class="sig-left">' + esc(C.fmtPair(s.pair)) +
      '<span class="side ' + (buy ? "buy" : "sell") + '">' + (buy ? "BUY" : "SELL") + "</span>" +
      (n(s.score) !== null ? '<span class="score">' + esc(s.score) + "</span>" : "") + "</div>" +
      '<span class="tag ' + (opened ? "green" : "") + '">' + (opened ? "Aperto" : "Scartato") + "</span></div>" +
      '<div class="sig-prices">' + esc(C.fmtDateTime(s.time)) + " · entrata " + C.fmtPrice(n(s.entry), s.pair) +
      " · SL " + C.fmtPrice(n(s.sl), s.pair) + " · TP " + C.fmtPrice(n(s.tp), s.pair) + "</div>" +
      (s.note ? '<div class="sig-note">' + esc(s.note) + "</div>" : "") + "</div>";
  }

  /* ================= STATISTICHE ================= */
  function renderStats() {
    var box = $("statsBody");
    if (!everLoaded) { box.innerHTML = '<div class="skeleton" style="height:260px"></div>'; return; }
    var hist = arr(data.history);
    var S = C.computeStats(hist);
    var dd = C.maxDrawdownPct(arr(data.equity));
    var html = '<h2 class="section-title">Risultati del conto virtuale</h2>';

    if (!S.trades) {
      html += emptyState("Nessun trade chiuso ancora", "Le statistiche compariranno dopo i primi trade chiusi dal bot.", ICONS.chart);
    } else {
      var winW = S.wins / S.trades * 100, beW = S.breakeven / S.trades * 100, lossW = S.losses / S.trades * 100;
      var pf = S.profitFactor === Infinity ? "∞" : C.fmtNum(S.profitFactor, 2);
      var streak = S.currentStreakType ? S.currentStreak + " " + (S.currentStreakType === "W" ? "vinc." : "perd.") : "—";
      html += '<div class="card"><div style="display:flex;justify-content:space-between;align-items:flex-end;gap:10px">' +
        '<div><div class="kpi-label">Risultato netto</div><div class="hero-value ' + cls(S.net) + '" style="font-size:32px">' + C.fmtEur(S.net, { sign: true }) + "</div></div>" +
        '<div style="text-align:right"><div class="kpi-label">Win rate</div><div class="kpi-value" style="font-size:24px">' + C.fmtPct(S.winRate, 1) + "</div></div></div>" +
        '<div class="winbar">' + (winW ? '<div style="width:' + winW + '%;background:var(--green)"></div>' : "") +
        (beW ? '<div style="width:' + beW + '%;background:var(--muted)"></div>' : "") +
        (lossW ? '<div style="width:' + lossW + '%;background:var(--red)"></div>' : "") + "</div>" +
        '<div class="winbar-legend"><span><i style="background:var(--green)"></i>' + S.wins + " vinti</span>" +
        '<span><i style="background:var(--muted)"></i>' + S.breakeven + " pari</span>" +
        '<span><i style="background:var(--red)"></i>' + S.losses + " persi</span></div></div>";

      html += '<div class="card"><div class="stat-grid">' +
        stat("Trade", S.trades) + stat("Profit factor", pf) + stat("Max drawdown", dd !== null ? '<span class="' + (dd > 0 ? "neg" : "") + '">' + C.fmtPct(-dd, 1) + "</span>" : "—") +
        stat("Media vincita", '<span class="pos">' + C.fmtEur(S.avgWin) + "</span>") + stat("Media perdita", '<span class="neg">' + C.fmtEur(S.avgLoss) + "</span>") +
        stat("R medio", S.avgR !== null ? (S.avgR > 0 ? "+" : "") + C.fmtNum(S.avgR, 2) + "R" : "—") +
        stat("Miglior trade", '<span class="pos">' + C.fmtEur(n(S.best && S.best.pnl_eur), { sign: true }) + "</span>", S.best ? C.fmtPair(S.best.pair) : "") +
        stat("Peggior trade", '<span class="neg">' + C.fmtEur(n(S.worst && S.worst.pnl_eur), { sign: true }) + "</span>", S.worst ? C.fmtPair(S.worst.pair) : "") +
        stat("Pip totali", '<span class="' + cls(S.netPips) + '">' + (S.netPips > 0 ? "+" : "") + C.fmtNum(S.netPips, 1) + "</span>") +
        stat("Serie vincente", S.maxWinStreak) + stat("Serie perdente", S.maxLossStreak) + stat("Serie attuale", streak) +
        "</div></div>";

      var pairs = Object.keys(S.perPair).map(function (k) { return S.perPair[k]; }).sort(function (a, b) { return b.net - a.net; });
      html += '<div class="card"><h3 class="card-title">Per coppia</h3><div class="table-wrap"><table class="tbl"><thead><tr>' +
        "<th>Coppia</th><th>Trade</th><th>Win</th><th>PF</th><th>Netto</th></tr></thead><tbody>" +
        pairs.map(function (q) {
          return "<tr><td><b>" + esc(C.fmtPair(q.pair)) + "</b></td><td>" + q.trades + "</td><td>" + C.fmtPct(q.winRate, 0) + "</td><td>" +
            (q.profitFactor === Infinity ? "∞" : C.fmtNum(q.profitFactor, 2)) + '</td><td class="' + cls(q.net) + '">' + C.fmtEur(q.net, { sign: true }) + "</td></tr>";
        }).join("") + "</tbody></table></div></div>";
    }
    html += '<p class="disclaimer">Conto virtuale: risultati passati non garantiscono risultati futuri.</p>';

    // coppie in pausa
    var paused = obj(data.state && data.state.paused_pairs);
    var pk = Object.keys(paused);
    if (pk.length) {
      html += '<h2 class="section-title">Coppie in pausa automatica</h2><div class="card list-card">' + pk.map(function (k) {
        var p = obj(paused[k]);
        return '<div class="sig"><div class="sig-top"><div class="sig-left">' + esc(C.fmtPair(k)) + '</div><span class="tag amber">In pausa</span></div>' +
          '<div class="sig-note">' + esc(p.reason || "Motivo non indicato") + (p.since ? " · dal " + esc(C.fmtDateTime(p.since)) : "") + "</div></div>";
      }).join("") + "</div>";
    }

    html += renderBacktest();

    // storico
    html += '<h2 class="section-title">Storico trade <span class="count">' + hist.length + "</span></h2>";
    if (!hist.length) {
      html += '<div class="card"><p class="help" style="margin:0">Nessun trade chiuso.</p></div>';
    } else {
      var sorted = hist.slice().sort(function (a, b) { return (C.parseTime(b.closed_at) || 0) - (C.parseTime(a.closed_at) || 0); });
      html += '<div class="card list-card">' + sorted.slice(0, historyShown).map(tradeRow).join("") + "</div>";
      if (sorted.length > historyShown) {
        html += '<button type="button" class="btn btn-secondary btn-block more-btn" id="moreHistory">Mostra altri (' + (sorted.length - historyShown) + ")</button>";
      }
    }
    box.innerHTML = html;
    var bt = $("btChart");
    if (bt) drawBacktestChart(bt);
    var more = $("moreHistory");
    if (more) more.onclick = function () { historyShown += 50; renderStats(); };
  }
  function stat(label, value, sub) {
    return '<div class="stat"><span>' + esc(label) + "</span><b>" + value + "</b>" + (sub ? '<span>' + esc(sub) + "</span>" : "") + "</div>";
  }
  var RESULT_TAGS = { TP: ["green", "TP"], SL: ["red", "SL"], BE: ["amber", "Pareggio"], STOP_MANUALE: ["blue", "Manuale"], TEMPO: ["blue", "Fine giornata"] };
  function tradeRow(t) {
    var buy = String(t.side).toUpperCase() === "BUY";
    var rt = RESULT_TAGS[t.result] || ["", t.result || "?"];
    var pnl = n(t.pnl_eur), pips = n(t.pnl_pips), r = n(t.r_multiple);
    return '<div class="trade"><div class="trade-left"><div class="trade-title">' + esc(C.fmtPair(t.pair)) +
      '<span class="side ' + (buy ? "buy" : "sell") + '">' + (buy ? "BUY" : "SELL") + "</span>" +
      '<span class="tag ' + rt[0] + '">' + esc(rt[1]) + "</span></div>" +
      '<div class="trade-sub">' + esc(C.fmtDateTime(t.closed_at)) + " · " + esc(C.fmtDuration(t.opened_at, t.closed_at)) + " · " + C.fmtLots(n(t.lots)) + " lotti</div></div>" +
      '<div class="trade-right"><b class="' + cls(pnl) + '">' + C.fmtEur(pnl, { sign: true }) + "</b><span>" +
      (pips !== null ? (pips > 0 ? "+" : "") + C.fmtNum(pips, 1) + " " + C.unitName(t.pair) : "") + (r !== null ? " · " + (r > 0 ? "+" : "") + C.fmtNum(r, 2) + "R" : "") + "</span></div></div>";
  }

  function rStats(s) {
    s = obj(s);
    var r = function (v, sign) { v = n(v); return v === null ? "—" : (sign && v > 0 ? "+" : "") + C.fmtNum(v, 1) + "R"; };
    return { trades: n(s.trades) === null ? "—" : s.trades, wr: n(s.win_rate) === null ? "—" : C.fmtPct(n(s.win_rate), 1),
      pf: n(s.profit_factor) === null ? "—" : C.fmtNum(n(s.profit_factor), 2), net: r(s.net_r, true), netRaw: n(s.net_r),
      exp: r(s.expectancy_r, true), dd: r(s.max_dd_r), aw: r(s.avg_win_r, true), al: r(s.avg_loss_r) };
  }
  function renderBacktest() {
    var bt = data.backtest, cal = data.calibration;
    if (!bt && !cal) {
      return '<h2 class="section-title">Backtest</h2>' + '<div class="card"><span class="sim-label">RISULTATI STORICI SIMULATI</span>' +
        '<p class="help" style="margin:0">Il backtest non è ancora stato eseguito: quando il bot calibra la strategia sui dati storici, qui vedrai quali coppie sono state abilitate e con quali risultati.</p></div>';
    }
    var html = '<h2 class="section-title">Backtest e calibrazione</h2><div class="card"><span class="sim-label">RISULTATI STORICI SIMULATI</span>';
    var src = bt || cal;
    html += '<p class="help">Periodo ' + esc(C.fmtDate(src.data_from)) + " – " + esc(C.fmtDate(src.data_to)) +
      (src.generated ? " · calcolato il " + esc(C.fmtDate(src.generated)) : "") +
      ". Risultati in R (1R = la cifra rischiata per trade), solo coppie abilitate, fuori campione.</p>";
    if (bt && bt.total) {
      var t = rStats(bt.total);
      html += '<div class="stat-grid">' + stat("Trade", t.trades) + stat("Win rate", t.wr) + stat("Profit factor", t.pf) +
        stat("Netto", '<span class="' + cls(t.netRaw) + '">' + t.net + "</span>") + stat("Per trade", t.exp) + stat("Max DD", t.dd) + "</div>";
    }
    if (bt && arr(bt.equity_r).length > 1) {
      html += '<div class="kpi-label" style="margin:14px 0 4px">R cumulati nel tempo</div><div class="chart-box" id="btChart"></div>';
    }
    html += "</div>";

    var calPairs = obj(cal && cal.pairs);
    var keys = C.PAIRS.filter(function (p) { return calPairs[p]; }).concat(Object.keys(calPairs).filter(function (p) { return C.PAIRS.indexOf(p) < 0; }));
    if (keys.length) {
      html += '<div class="card"><h3 class="card-title">Coppie (fuori campione)</h3><div class="table-wrap"><table class="tbl"><thead><tr>' +
        "<th>Coppia</th><th>Stato</th><th>Trade</th><th>Win</th><th>PF</th><th>Netto</th></tr></thead><tbody>";
      var notes = "";
      keys.forEach(function (k) {
        var c = obj(calPairs[k]);
        var os = rStats(c.out_sample || (bt && obj(bt.per_pair)[k]));
        html += "<tr><td><b>" + esc(C.fmtPair(k)) + "</b></td><td>" + (c.enabled ? '<span class="tag green">Attiva</span>' : '<span class="tag">Esclusa</span>') +
          "</td><td>" + os.trades + "</td><td>" + os.wr + "</td><td>" + os.pf + '</td><td class="' + cls(os.netRaw) + '">' + os.net + "</td></tr>";
        if (c.note) notes += "<li><b>" + esc(C.fmtPair(k)) + ":</b> " + esc(c.note) + "</li>";
      });
      html += "</tbody></table></div>" + (notes ? '<ul class="help" style="margin:12px 0 0;padding-left:18px">' + notes + "</ul>" : "") + "</div>";
    }
    return html;
  }
  function drawBacktestChart(box) {
    var pts = arr(data.backtest && data.backtest.equity_r).map(function (p) { return { x: C.parseTime(p && p.t), y: n(p && p.r) }; })
      .filter(function (p) { return p.x !== null && p.y !== null; }).sort(function (a, b) { return a.x - b.x; });
    lineChart(box, C.downsample(pts, 400), {
      height: 170,
      fmtY: function (v) { return (v > 0 ? "+" : "") + C.fmtNum(v, 0) + "R"; },
      fmtTip: function (v) { return (v > 0 ? "+" : "") + C.fmtNum(v, 1) + "R"; },
      ref: 0, empty: "Dati insufficienti"
    });
  }

  /* ================= GRAFICI (TradingView) ================= */
  function renderPairSelector() {
    var sel = $("pairSelector");
    sel.innerHTML = C.PAIRS.map(function (p) {
      return '<button type="button" role="tab" class="pair-btn' + (p === chartPair ? " active" : "") + '" data-pair="' + p + '">' + C.fmtPair(p) + "</button>";
    }).join("");
  }
  function loadTradingView() {
    if (tvLoaded) { showTvChart(); return; }
    if (tvLoading) return;
    tvLoading = true;
    $("tvFallback").hidden = true;
    var s = document.createElement("script");
    s.src = "https://s3.tradingview.com/tv.js";
    s.async = true;
    s.onload = function () { tvLoading = false; tvLoaded = !!window.TradingView; if (tvLoaded) showTvChart(); else tvFail(); };
    s.onerror = function () { tvLoading = false; s.remove(); tvFail(); };
    document.head.appendChild(s);
  }
  function tvFail() { $("tvChart").innerHTML = ""; $("tvFallback").hidden = false; }
  function showTvChart() {
    if (!window.TradingView || tvPairShown === chartPair) return;
    try {
      $("tvChart").innerHTML = "";
      $("tvFallback").hidden = true;
      /* eslint-disable no-new */
      new window.TradingView.widget({
        container_id: "tvChart",
        autosize: true,
        symbol: TV_SYMBOLS[chartPair] || "FX:EURUSD",
        interval: "60",
        timezone: "Europe/Rome",
        theme: "dark",
        style: "1",
        locale: "it",
        enable_publishing: false,
        allow_symbol_change: false,
        hide_side_toolbar: true,
        withdateranges: true,
        save_image: false,
        hide_volume: true,
        toolbar_bg: "#131823",
        backgroundColor: "#131823",
        gridColor: "rgba(38, 45, 64, 0.6)"
      });
      tvPairShown = chartPair;
    } catch (e) {
      console.error(e);
      tvFail();
    }
  }

  /* ================= IMPOSTAZIONI ================= */
  function setupSettingsForm() {
    $("sLeva").innerHTML = C.LEVERAGES.map(function (l) { return '<option value="' + l + '">1:' + l + "</option>"; }).join("");
    renderPairChecks([]);
    var form = $("settingsForm");
    form.addEventListener("input", onFormInput);
    form.addEventListener("change", onFormInput);
    form.addEventListener("submit", function (e) { e.preventDefault(); saveFromForm(); });
    $("resetBtn").addEventListener("click", resetAccount);
    $("ghSaveToken").addEventListener("click", connectGithub);
    renderGhStatus();
  }
  function renderPairChecks(active) {
    var paused = obj(data.state && data.state.paused_pairs);
    $("pairChecks").innerHTML = C.PAIRS.map(function (p) {
      return '<label class="pair-check"><input type="checkbox" name="pair" value="' + p + '"' + (active.indexOf(p) >= 0 ? " checked" : "") + ">" +
        "<span>" + C.fmtPair(p) + (paused[p] ? " <em>PAUSA AUTO</em>" : "") + "</span></label>";
    }).join("");
  }
  function onFormInput(e) {
    if (e && e.target && e.target.id === "ghToken") return;
    settingsDirty = true;
    updateCalc();
  }
  function fillForm(force) {
    if (settingsDirty && !force) { updateCalc(); return; }
    var s = C.normalizeSettings(data.settings);
    $("sCapitale").value = s.capitale_iniziale;
    var lev = C.LEVERAGES.indexOf(Number(s.leva)) >= 0 ? Number(s.leva) : 30;
    $("sLeva").value = String(lev);
    $("sRischio").value = String(Math.min(5, Math.max(0.25, Math.round(s.rischio_pct * 4) / 4)));
    $("sPausa").checked = !!s.pausa;
    $("sNotApertura").checked = !!s.notifiche_apertura;
    $("sNotChiusura").checked = !!s.notifiche_chiusura;
    $("sReport").checked = !!s.report_settimanale;
    renderPairChecks(s.coppie_attive);
    settingsDirty = false;
    updateCalc();
  }
  function readForm() {
    var pairs = [];
    Array.prototype.forEach.call(document.querySelectorAll('#pairChecks input[name="pair"]'), function (cb) { if (cb.checked) pairs.push(cb.value); });
    return {
      capitale_iniziale: Math.round(Number(String($("sCapitale").value).replace(",", ".")) * 100) / 100,
      leva: Number($("sLeva").value),
      rischio_pct: Number($("sRischio").value),
      pausa: $("sPausa").checked,
      coppie_attive: pairs,
      notifiche_apertura: $("sNotApertura").checked,
      notifiche_chiusura: $("sNotChiusura").checked,
      report_settimanale: $("sReport").checked
    };
  }
  function updateCalc() {
    var f = readForm();
    $("sRischioVal").textContent = C.fmtPctShort(f.rischio_pct);
    var cap = f.capitale_iniziale;
    var r = C.lotCalc({ capital: cap, riskPct: f.rischio_pct, leverage: f.leva, stopPips: 20, eurusd: lastPrice("EURUSD") || 1.08 });
    if (!(cap > 0)) {
      $("calcText").textContent = "Inserisci il capitale per vedere l'anteprima.";
      $("calcGrid").innerHTML = "";
      $("calcWarn").hidden = true;
      return;
    }
    $("calcText").innerHTML = "Con queste impostazioni, su uno stop di <b>20 pip</b> su EUR/USD apriresti <b>" + C.fmtLots(r.lots) +
      " lotti</b>, rischiando <b>" + C.fmtEur(r.riskEur) + "</b>, margine <b>" + C.fmtEur(r.marginEur) + "</b>.";
    $("calcGrid").innerHTML =
      "<div><span>Lotti</span><b>" + C.fmtLots(r.lots) + "</b></div>" +
      "<div><span>Rischio</span><b>" + C.fmtEur(r.riskEur) + "</b></div>" +
      "<div><span>Margine</span><b>" + C.fmtEur(r.marginEur, { noDecimals: r.marginEur >= 100 }) + "</b></div>";
    var warns = [];
    if (r.tooSmall) {
      warns.push("Capitale piccolo: anche il lotto minimo (0,01) rischia " + C.fmtEur(r.minRiskEur) + " su 20 pip, più dei " +
        C.fmtEur(r.riskTarget) + " scelti (" + C.fmtPct(r.riskPctReal, 1) + " del conto invece di " + C.fmtPctShort(f.rischio_pct) +
        "). Per rispettare il rischio scelto servirebbero almeno " + C.fmtEur(r.minCapitalForRisk, { noDecimals: true }) + " di capitale.");
    }
    if (r.marginTooHigh) warns.push("Il margine richiesto supera il capitale: aumenta la leva o abbassa il rischio.");
    else if (r.marginPct > 50) warns.push("Il margine impegnato sarebbe il " + C.fmtPct(r.marginPct, 0) + " del capitale: con più posizioni aperte potresti non avere margine sufficiente.");
    if (f.rischio_pct > 2) warns.push("Rischio alto: oltre il 2% per trade poche perdite di fila pesano molto sul conto.");
    var w = $("calcWarn");
    w.hidden = !warns.length;
    w.innerHTML = warns.map(esc).join("<br><br>");
  }

  function formMsg(type, text) {
    var m = $("formMsg");
    m.className = "form-msg " + (type || "");
    m.textContent = text || "";
  }
  function getToken() { return store.get(TOKEN_KEY); }
  function renderGhStatus() {
    var tok = getToken();
    $("ghConnect").hidden = !!tok;
    if (DEMO) {
      $("ghStatus").innerHTML = '<p class="help" style="margin:0 0 12px">Modalità demo: le impostazioni non vengono salvate su GitHub.</p>';
    } else if (tok) {
      $("ghStatus").innerHTML = '<div class="gh-ok"><span><b>Collegato</b> a ' + esc((CFG.owner || "") + "/" + (CFG.repo || "")) +
        '<br><span class="muted" style="font-size:12px">token …' + esc(tok.slice(-4)) + '</span></span>' +
        '<button type="button" class="btn btn-secondary btn-small" id="ghLogout">Scollega</button></div>';
      $("ghLogout").onclick = function () {
        if (!window.confirm("Scollegare GitHub da questo telefono?")) return;
        store.del(TOKEN_KEY);
        renderGhStatus();
        toast("GitHub scollegato");
      };
    } else {
      $("ghStatus").innerHTML = "";
    }
  }
  function ghFetch(method, path, body) {
    var headers = {
      "Accept": "application/vnd.github+json",
      "Authorization": "Bearer " + getToken(),
      "X-GitHub-Api-Version": "2022-11-28"
    };
    if (body) headers["Content-Type"] = "application/json";
    return fetch("https://api.github.com" + path, { method: method, headers: headers, body: body ? JSON.stringify(body) : undefined, cache: "no-store" });
  }
  function repoPath() { return "/repos/" + encodeURIComponent(CFG.owner) + "/" + encodeURIComponent(CFG.repo); }
  function ghError(status, phase) {
    if (status === 401) return "Token GitHub non valido o scaduto. Scollega e incolla un nuovo token.";
    if (status === 403) return "Il token non ha il permesso di scrivere: serve \"Contents: Read and write\" sul repository " + CFG.repo + " (oppure limite di richieste raggiunto, riprova tra poco).";
    if (status === 404) return "Repository non trovato o token senza accesso a " + CFG.owner + "/" + CFG.repo + ".";
    if (status === 409 || status === 422) return "Il file è stato modificato nello stesso momento (dal bot o da Telegram). Riprova.";
    return "Errore GitHub (" + status + ") durante " + phase + ". Riprova.";
  }
  function connectGithub() {
    var tok = String($("ghToken").value || "").trim();
    if (!tok) { formMsg("err", "Incolla prima il token GitHub."); return; }
    if (!store.set(TOKEN_KEY, tok)) { formMsg("err", "Impossibile salvare il token su questo dispositivo (memoria del browser non disponibile)."); return; }
    var btn = $("ghSaveToken");
    btn.disabled = true;
    btn.textContent = "Verifico…";
    ghFetch("GET", repoPath()).then(function (r) {
      if (r.ok) {
        $("ghToken").value = "";
        renderGhStatus();
        formMsg("ok", "GitHub collegato! Ora puoi salvare le impostazioni.");
      } else {
        store.del(TOKEN_KEY);
        formMsg("err", ghError(r.status, "la verifica"));
      }
    }).catch(function () {
      // offline: teniamo il token, verrà verificato al primo salvataggio
      $("ghToken").value = "";
      renderGhStatus();
      formMsg("info", "Token salvato, ma non ho potuto verificarlo (sei offline?). Verrà controllato al primo salvataggio.");
    }).then(function () {
      btn.disabled = false;
      btn.textContent = "Collega";
    });
  }

  /* Legge settings.json dal repo (per sha e valori aggiornati), applica mutate() e scrive. Un tentativo extra in caso di conflitto. */
  function writeSettings(mutate) {
    var path = repoPath() + "/contents/data/settings.json";
    var branch = CFG.branch || "main";
    function attempt(n0) {
      return ghFetch("GET", path + "?ref=" + encodeURIComponent(branch)).then(function (r) {
        if (r.status === 404) return { sha: null, remote: {} };
        if (!r.ok) throw { msg: ghError(r.status, "la lettura") };
        return r.json().then(function (j) {
          var remote = {};
          try { remote = JSON.parse(C.b64DecodeUtf8(j.content || "")) || {}; } catch (e) { remote = {}; }
          return { sha: j.sha, remote: remote };
        });
      }).then(function (cur) {
        var next = mutate(C.normalizeSettings(cur.remote));
        var body = {
          message: "Impostazioni aggiornate dall'app",
          content: C.b64EncodeUtf8(JSON.stringify(next, null, 2) + "\n"),
          branch: branch
        };
        if (cur.sha) body.sha = cur.sha;
        return ghFetch("PUT", path, body).then(function (r) {
          if (r.ok) return next;
          if ((r.status === 409 || r.status === 422) && n0 < 1) return attempt(n0 + 1);
          throw { msg: ghError(r.status, "il salvataggio") };
        });
      });
    }
    return attempt(0).catch(function (e) {
      if (e && e.msg) throw e;
      throw { msg: "Connessione assente o GitHub non raggiungibile: impostazioni non salvate." };
    });
  }
  function applyForm(target, f) {
    ["capitale_iniziale", "leva", "rischio_pct", "pausa", "coppie_attive", "notifiche_apertura", "notifiche_chiusura", "report_settimanale"]
      .forEach(function (k) { target[k] = f[k]; });
    return target;
  }
  function afterSave(saved, okText) {
    data.settings = saved;
    store.set(SAVED_KEY, JSON.stringify({ t: Date.now(), settings: saved }));
    settingsDirty = false;
    formMsg("ok", okText);
    toast("Salvato");
    renderAll();
  }
  function preSaveCheck() {
    if (DEMO) { formMsg("info", "Modalità demo: le impostazioni non vengono salvate."); return false; }
    if (!CFG.owner || !CFG.repo) { formMsg("err", "config.js non è configurato (owner/repo mancanti)."); return false; }
    if (!getToken()) {
      formMsg("err", "Per salvare devi prima collegare GitHub (qui sotto).");
      var gc = $("ghToken");
      if (gc) { gc.scrollIntoView({ behavior: "smooth", block: "center" }); }
      return false;
    }
    return true;
  }
  function saveFromForm() {
    var f = readForm();
    var errs = C.validateSettings(f);
    if (errs.length) { formMsg("err", errs.join(" ")); return; }
    if (!preSaveCheck()) return;
    var btn = $("saveBtn");
    btn.disabled = true;
    btn.textContent = "Salvataggio…";
    formMsg("info", "Salvataggio su GitHub in corso…");
    writeSettings(function (remote) { return applyForm(remote, f); }).then(function (saved) {
      afterSave(saved, "Salvato! Il bot userà le nuove impostazioni entro 15 minuti.");
    }).catch(function (e) {
      formMsg("err", (e && e.msg) || "Errore sconosciuto.");
    }).then(function () {
      btn.disabled = false;
      btn.textContent = "Salva impostazioni";
    });
  }
  function resetAccount() {
    var f = readForm();
    var errs = C.validateSettings(f);
    if (errs.length) { formMsg("err", errs.join(" ")); return; }
    if (!preSaveCheck()) return;
    var ok = window.confirm("Azzerare il conto virtuale?\n\nAl prossimo giro il bot chiuderà le posizioni virtuali e il conto ripartirà da " +
      C.fmtEur(f.capitale_iniziale) + "." + (settingsDirty ? "\n\nVerranno salvate anche le modifiche non salvate qui sopra." : ""));
    if (!ok) return;
    var btn = $("resetBtn");
    btn.disabled = true;
    formMsg("info", "Invio richiesta di azzeramento…");
    writeSettings(function (remote) {
      var next = settingsDirty ? applyForm(remote, f) : remote;
      next.reset_conto = true;
      return next;
    }).then(function (saved) {
      afterSave(saved, "Richiesta inviata! Il bot azzererà il conto virtuale entro 15 minuti.");
    }).catch(function (e) {
      formMsg("err", (e && e.msg) || "Errore sconosciuto.");
    }).then(function () { btn.disabled = false; });
  }

  /* ================= navigazione ================= */
  function showTab(name, noHash) {
    if (!document.getElementById("tab-" + name)) name = "conto";
    currentTab = name;
    Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (s) { s.hidden = s.getAttribute("data-tab") !== name; });
    Array.prototype.forEach.call(document.querySelectorAll(".tab-btn"), function (b) { b.classList.toggle("active", b.getAttribute("data-tab") === name); });
    if (!noHash) { try { history.replaceState(null, "", location.pathname + location.search + "#" + name); } catch (e) { /* ignora */ } }
    window.scrollTo(0, 0);
    if (name === "grafici") loadTradingView();
    if (name === "conto") drawEquityChart();     // ridisegna con la larghezza corretta
    if (name === "statistiche") { var bt = $("btChart"); if (bt) drawBacktestChart(bt); }
  }

  function renderTechLog() {
    var box = $("techLog");
    if (!box) return;
    var logs = arr(data.log).slice(-20).reverse();
    box.innerHTML = logs.length ? logs.map(function (l) {
      return '<div class="log-item"><span class="log-dot lv-' + esc(l.level) + '"></span><div><div>' + esc(l.msg) +
        '</div><div class="log-time">' + esc(C.fmtDateTime(l.t)) + "</div></div></div>";
    }).join("") : '<div class="muted">Nessun evento.</div>';
  }

  function renderAll() {
    renderHeader();
    try { renderTechLog(); } catch (e) { console.error(e); }
    try { renderConto(); } catch (e) { console.error(e); $("contoBody").innerHTML = emptyState("Errore di visualizzazione", "I dati del conto hanno un formato inatteso.", ICONS.clock); }
    try { renderPositions(); } catch (e) { console.error(e); }
    try { renderStats(); } catch (e) { console.error(e); }
    try { fillForm(false); } catch (e) { console.error(e); }
  }

  /* ================= pull to refresh ================= */
  function setupPullToRefresh() {
    var ptr = $("ptr");
    var startY = null, dist = 0, TH = 70;
    window.addEventListener("touchstart", function (e) {
      if (currentTab === "grafici" || window.scrollY > 0 || loading || e.touches.length !== 1) { startY = null; return; }
      startY = e.touches[0].clientY; dist = 0;
    }, { passive: true });
    window.addEventListener("touchmove", function (e) {
      if (startY === null) return;
      dist = (e.touches[0].clientY - startY) * 0.5;
      if (dist <= 0 || window.scrollY > 0) { ptr.style.opacity = 0; ptr.style.transform = ""; return; }
      var d = Math.min(dist, 100);
      ptr.style.opacity = Math.min(1, d / TH);
      ptr.style.transform = "translateY(" + d + "px) rotate(" + (d * 3) + "deg)";
      ptr.classList.toggle("ready", d >= TH);
    }, { passive: true });
    window.addEventListener("touchend", function () {
      if (startY === null) return;
      var go = dist >= TH;
      startY = null;
      ptr.classList.remove("ready");
      if (go) {
        ptr.classList.add("loading");
        ptr.style.transform = "translateY(70px)";
        loadAll(true).then(function () {
          ptr.classList.remove("loading");
          ptr.style.opacity = 0; ptr.style.transform = "";
        });
      } else {
        ptr.style.opacity = 0; ptr.style.transform = "";
      }
    }, { passive: true });
  }

  /* ================= avvio ================= */
  function init() {
    if (DEMO) $("demoPill").hidden = false;
    if (!DEMO && (!CFG.owner || !CFG.repo)) {
      var b = $("globalBanner");
      b.className = "banner banner-err";
      b.textContent = "Configurazione mancante: imposta owner e repo in config.js.";
      b.hidden = false;
    }
    $("tabbar").addEventListener("click", function (e) {
      var b = e.target.closest(".tab-btn");
      if (b) showTab(b.getAttribute("data-tab"));
    });
    $("refreshBtn").addEventListener("click", function () { loadAll(true); });
    $("contoBody").addEventListener("click", function (e) {
      if (e.target.closest(".goto-op")) { showTab("posizioni"); return; }
      var b = e.target.closest("#eqRange button");
      if (!b) return;
      eqRange = b.getAttribute("data-r");
      store.set(RANGE_KEY, eqRange);
      Array.prototype.forEach.call(document.querySelectorAll("#eqRange button"), function (x) { x.classList.toggle("active", x === b); });
      drawEquityChart();
    });
    renderPairSelector();
    $("pairSelector").addEventListener("click", function (e) {
      var b = e.target.closest(".pair-btn");
      if (!b) return;
      chartPair = b.getAttribute("data-pair");
      store.set(PAIR_KEY, chartPair);
      renderPairSelector();
      if (tvLoaded) showTvChart(); else loadTradingView();
    });
    $("tvRetry").addEventListener("click", function () { tvPairShown = null; loadTradingView(); });
    setupSettingsForm();
    setupPullToRefresh();

    var resizeT = null;
    window.addEventListener("resize", function () {
      clearTimeout(resizeT);
      resizeT = setTimeout(function () {
        if (currentTab === "conto") drawEquityChart();
        var bt = $("btChart"); if (bt && currentTab === "statistiche") drawBacktestChart(bt);
      }, 200);
    });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible" && Date.now() - lastLoad > 30000) loadAll(false);
    });

    showTab((location.hash || "").replace("#", "") || "conto", true);
    renderAll();
    loadAll(false);
    setInterval(function () { if (document.visibilityState !== "hidden") loadAll(false); }, REFRESH_MS);
    setInterval(renderHeader, 30000);

    if ("serviceWorker" in navigator) {
      window.addEventListener("load", function () {
        navigator.serviceWorker.register("sw.js").catch(function (e) { console.warn("Service worker non registrato", e); });
      });
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
