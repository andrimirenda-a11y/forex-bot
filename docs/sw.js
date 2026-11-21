/* Service worker: mette in cache SOLO la "scocca" dell'app (HTML/CSS/JS/icone).
   I dati del bot (raw.githubusercontent.com, sample-data/) e l'API GitHub non vengono mai messi in cache. */
var CACHE = "forexbot-shell-v2";
var SHELL = [
  "./",
  "index.html",
  "styles.css",
  "config.js",
  "core.js",
  "app.js",
  "manifest.json",
  "icon-192.png",
  "icon-512.png"
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (c) { return c.addAll(SHELL); }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

function isShell(url) {
  if (url.origin !== self.location.origin) return false;          // GitHub, TradingView, ecc.: mai in cache
  if (url.pathname.indexOf("/sample-data/") !== -1) return false; // dati demo: sempre dalla rete
  if (/\.json$/.test(url.pathname) && !/manifest\.json$/.test(url.pathname)) return false;
  return true;
}

/* Rete prima (così gli aggiornamenti arrivano subito), cache come riserva offline. */
self.addEventListener("fetch", function (event) {
  var req = event.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  if (!isShell(url)) return;
  event.respondWith(
    fetch(req).then(function (res) {
      if (res && res.ok && res.type === "basic") {
        var copy = res.clone();
        var key = req.mode === "navigate" ? "index.html" : req;
        caches.open(CACHE).then(function (c) { c.put(key, copy); });
      }
      return res;
    }).catch(function () {
      return caches.match(req, { ignoreSearch: true }).then(function (hit) {
        if (hit) return hit;
        if (req.mode === "navigate") return caches.match("index.html");
        return Response.error();
      });
    })
  );
});
