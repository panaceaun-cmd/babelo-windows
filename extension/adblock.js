// Babelo — блокировка рекламы во встроенном браузере.
// Запросы к рекламе режет программа по спискам фильтров (EasyList и др.); здесь — скрытие блоков по тем же спискам,
// вырезание рекламы из данных плеера YouTube и (запасной способ) пропуск рекламы в плеере.
(() => {
  "use strict";
  if (window.__babeloAdblock) return;
  window.__babeloAdblock = true;
  const yt = /(^|\.)youtube\.com$/.test(location.hostname);
  const hasChrome = typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage;

  // ---------- 1. запасной набор скрываемых блоков (работает и без списков фильтров)
  const hide = [
    "ins.adsbygoogle", ".adsbygoogle", '[id^="google_ads"]', '[id^="div-gpt-ad"]', 'iframe[src*="doubleclick.net"]',
    'iframe[src*="googlesyndication"]', 'iframe[id^="google_ads"]', ".ad-banner", ".adbanner", ".ad-container", ".ad-slot",
    ".advertisement", ".banner-ad", ".taboola", '[id^="taboola"]', ".outbrain", '[id^="yandex_ad"]', '[id^="adfox"]',
    ".yandex-adv", "#masthead-ad", "ytd-ad-slot-renderer", "ytd-in-feed-ad-layout-renderer", "ytd-banner-promo-renderer",
    "ytd-statement-banner-renderer", "ytd-display-ad-renderer", "ytd-promoted-sparkles-web-renderer",
    "ytd-promoted-video-renderer", "ytd-companion-slot-renderer", "ytd-player-legacy-desktop-watch-ads-renderer",
    "#player-ads", ".ytp-ad-module", ".ytp-ad-overlay-container", ".video-ads"
  ];
  let sheet = null;
  const ensureSheet = () => {
    if (sheet) return sheet;
    const st = document.createElement("style");
    (document.head || document.documentElement).appendChild(st);
    sheet = st.sheet;
    return sheet;
  };
  const addSelectors = (list) => {
    const sh = ensureSheet();
    if (!sh) return;
    for (const s of list) { try { sh.insertRule(s + "{display:none !important}", sh.cssRules.length); } catch (e) {} }
  };
  const base = () => {
    if (!document.documentElement) { setTimeout(base, 20); return; }
    addSelectors(hide);
  };
  base();

  // ---------- 2. скрытие блоков по спискам фильтров: программа подбирает селекторы под классы и id страницы
  const seenC = new Set(), seenI = new Set();
  let pendC = [], pendI = [], first = true, timer = null;
  const scan = (root) => {
    if (!root || root.nodeType !== 1) return;
    const els = [root, ...root.querySelectorAll("[class],[id]")];
    for (const el of els) {
      if (el.id && !seenI.has(el.id)) { seenI.add(el.id); pendI.push(el.id); }
      if (el.classList) for (const c of el.classList) if (!seenC.has(c)) { seenC.add(c); pendC.push(c); }
    }
  };
  const flush = () => {
    timer = null;
    const body = { host: location.hostname, classes: pendC.splice(0), ids: pendI.splice(0), first };
    if (!first && !body.classes.length && !body.ids.length) return;
    first = false;
    chrome.runtime.sendMessage({ type: "local", path: "/adblock/cosmetic", body }, (r) => {
      if (r && r.ok && r.data) {
        if (r.data.sel && r.data.sel.length) addSelectors(r.data.sel);
        if (r.data.proc && r.data.proc.length && window.__babeloProc) window.__babeloProc.add(r.data.proc);   // правила по тексту и сложным условиям
      }
    });
  };
  const schedule = () => { if (!timer) timer = setTimeout(flush, 700); };
  const startCosmetic = () => {
    scan(document.documentElement);
    schedule();
    new MutationObserver((muts) => {
      for (const m of muts) for (const n of m.addedNodes) scan(n);
      if (pendC.length || pendI.length) schedule();
    }).observe(document.documentElement, { childList: true, subtree: true });
  };
  if (hasChrome) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startCosmetic);
    else startCosmetic();
  }

  if (!yt) return;
  // ---------- 3. YouTube: рекламы нет в данных плеера — она просто не загружается
  const AD_KEYS = ["adPlacements", "adSlots", "playerAds", "adBreakHeartbeatParams"];
  const strip = (o) => {
    try {
      if (o && typeof o === "object") {
        for (const k of AD_KEYS) if (k in o) delete o[k];
        if (o.playerResponse && typeof o.playerResponse === "object") strip(o.playerResponse);
      }
    } catch (e) {}
    return o;
  };
  try {
    let ipr;
    Object.defineProperty(window, "ytInitialPlayerResponse", { configurable: true, get() { return ipr; }, set(v) { ipr = strip(v); } });
  } catch (e) {}
  const nativeParse = JSON.parse;
  JSON.parse = function () { return strip(nativeParse.apply(this, arguments)); };
  const nativeJson = Response.prototype.json;
  Response.prototype.json = function () { return nativeJson.apply(this, arguments).then(strip); };

  // ---------- 4. запасной способ: «Пропустить», а нельзя — промотать рекламу до конца без звука
  let saved = null;
  setInterval(() => {
    const p = document.querySelector(".html5-video-player");
    const v = document.querySelector("video.html5-main-video") || document.querySelector("video");
    const ad = !!(p && p.classList.contains("ad-showing"));
    window.__babeloAd = ad;          // по этому признаку программа не переводит рекламу
    document.querySelectorAll(".ytp-ad-overlay-close-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern, .ytp-skip-ad-button")
      .forEach((b) => { try { b.click(); } catch (e) {} });
    if (!v) return;
    if (ad) {
      if (!saved) saved = { muted: v.muted, rate: v.playbackRate };
      v.muted = true;
      v.playbackRate = 16;
      if (isFinite(v.duration) && v.duration > 0 && v.currentTime < v.duration - 0.3) v.currentTime = v.duration;
    } else if (saved) {
      v.muted = saved.muted;
      v.playbackRate = saved.rate;
      saved = null;
    }
  }, 250);
})();
