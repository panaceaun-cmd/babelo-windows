// Babelo для браузера: непрерывный перевод видео онлайн. Субтитры видео читаются ВПЕРЁД, поэтому перевод готов заранее:
// видео идёт без повторов, оригинал выключен, звучит только перевод. Те же режимы, что в программе для Windows.
(() => {
  "use strict";
  if (window.__babeloLoaded) return;
  window.__babeloLoaded = true;
  const Core = window.BabeloCore;

  const LANGS = { ru: "Русский", en: "English", es: "Español", de: "Deutsch", fr: "Français", uk: "Українська",
    pl: "Polski", it: "Italiano", pt: "Português", tr: "Türkçe", nl: "Nederlands", zh: "中文", ja: "日本語",
    ko: "한국어", ar: "العربية", hi: "हिन्दी" };
  const MODES = [["live", "Обычный (оригинал тише, поверх перевод)"], ["continuous", "Непрерывный: только перевод, без повторов"],
    ["interrupted", "Прерывистый: оригинал, потом тот же отрывок с переводом"]];
  const SUBS = [["off", "Не показывать субтитры"], ["translated", "Переведённые субтитры"],
    ["original", "Субтитры на языке оригинала"], ["both", "Перевод и оригинал вместе"]];
  const PROVIDERS = [["auto", "Авто (Babelo на компьютере → YouTube → встроенный переводчик)"],
    ["local", "Babelo на компьютере (лучшее качество, подсказки слов)"], ["youtube", "Перевод YouTube"],
    ["chrome", "Встроенный переводчик браузера"]];
  const HINT = "Перевод видео и аудио включён F10. Меню F10. Выключить — Esc или дважды F10";
  const DEFAULTS = { target: "ru", source: "auto", mode: "continuous", subs: "translated", provider: "auto", pause_s: 2,
    repeats: 1, speed: 100, volume: 100, orig_vol: 15, lead: 20, mute_original: true, esc_stops: true, voice: "" };
  let CFG = Object.assign({}, DEFAULTS);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const isSpaceless = (l) => ["zh", "ja"].includes(l);

  function loadCfg() {
    return new Promise((res) => chrome.storage.local.get("babelo", (d) => {
      if (d && d.babelo) Object.assign(CFG, d.babelo);
      res();
    }));
  }
  const saveCfg = () => chrome.storage.local.set({ babelo: CFG });

  // ------------------------------------------------------------------ состояние
  let active = false, closed = false, video = null, cues = [], srcLang = null, ytTrack = null;
  let ptr = 0, hold = false, busy = false, buffering = false, elastic = false, elasticT = 0;
  let runId = 0, loopGen = 0, translating = false, timer = null, shown = null, savedVol = null;
  let statusText = null, lastF10 = 0, tipFor = null;
  const ours = { pause: 0, seek: 0 };

  // ------------------------------------------------------------------ интерфейс (в закрытом shadow DOM)
  const host = document.createElement("div");
  host.style.cssText = "all:initial;position:fixed;top:0;left:0;width:0;height:0;z-index:2147483647;";
  const sh = host.attachShadow({ mode: "open" });
  sh.innerHTML = `<style>
    *{box-sizing:border-box;font-family:"Segoe UI",Arial,sans-serif}
    .banner{position:fixed;top:4px;left:50%;transform:translateX(-50%);background:#000;color:#fff;font-weight:700;font-size:14px;
      padding:6px 18px;border-radius:6px;pointer-events:none;text-align:center;z-index:2}
    .banner .st{color:#ffd400;font-size:12px;margin-top:2px}
    .subs{position:fixed;left:50%;bottom:11%;transform:translateX(-50%);background:#000;color:#fff;font-weight:700;font-size:22px;
      padding:10px 22px;max-width:80vw;text-align:center;border-radius:6px;line-height:1.4;z-index:2}
    .subs .o{font-size:18px;color:#ddd;margin-bottom:4px}
    .subs .w{display:inline-block;border-radius:3px;cursor:default;padding:0 1px}.subs .w:hover{background:#333}
    .tip{position:fixed;background:#000;color:#ffd400;font-weight:700;font-size:22px;padding:6px 14px;border-radius:6px;
      pointer-events:none;white-space:nowrap;z-index:3}
    .menu{position:fixed;top:50%;right:0;transform:translate(102%,-50%);width:370px;max-height:92vh;overflow:auto;background:#1c1c20;
      color:#fff;padding:10px 14px 14px;border-radius:10px 0 0 10px;transition:transform .2s;font-size:13px;z-index:4}
    .menu.open{transform:translate(0,-50%)}
    .menu h3{margin:0;font-size:15px;display:flex;justify-content:space-between;align-items:center}
    .menu label{display:block;margin:9px 0 2px;color:#ddd}
    .menu select,.menu input[type=range]{width:100%}
    .menu select{background:#fff;color:#000;border-radius:4px;padding:3px}
    .menu button{width:100%;margin-top:6px;background:#333;color:#fff;border:0;border-radius:5px;padding:7px;cursor:pointer;font-size:13px}
    .menu button:hover{background:#444}.menu .x{width:auto;background:none;font-size:18px;margin:0;padding:0 4px}
    .menu .ck{display:flex;gap:6px;align-items:center;margin-top:8px}
  </style>
  <div class="banner" hidden><div class="hint"></div><div class="st" hidden></div></div>
  <div class="subs" hidden></div><div class="tip" hidden></div><div class="menu"></div>`;
  const $ = (s) => sh.querySelector(s);
  const mount = () => {
    const p = document.fullscreenElement || document.documentElement;
    if (host.parentNode !== p) p.appendChild(host);
  };
  document.addEventListener("fullscreenchange", mount);
  mount();

  function showBanner(on) {
    const b = $(".banner");
    $(".hint").textContent = HINT;
    b.hidden = !on;
    mount();
  }
  function setStatus(text) {
    statusText = text;
    const st = $(".banner .st");
    st.hidden = !text;
    st.textContent = text || "";
  }
  function hideTip() { $(".tip").hidden = true; tipFor = null; }
  function hideSubs() { $(".subs").hidden = true; hideTip(); }
  function showTip(span, text) {
    const tip = $(".tip");
    tip.textContent = text;
    tip.hidden = false;
    const r = span.getBoundingClientRect();
    const sr = $(".subs").getBoundingClientRect();
    const tr = tip.getBoundingClientRect();
    tip.style.left = clamp(r.left + r.width / 2 - tr.width / 2, 4, innerWidth - tr.width - 4) + "px";
    tip.style.top = Math.max(4, sr.top - tr.height - 8) + "px";
  }
  async function onWordEnter(span, tw, ow, c) {
    tipFor = span;
    if (ow) return showTip(span, ow);
    const w = await reverseWord(tw);
    if (w && tipFor === span) showTip(span, w);
  }
  function renderSubs(c, kind) {
    const mode = CFG.subs;
    let lines = [];
    if (kind === "orig") lines = mode === "original" || mode === "both" ? ["orig"] : [];
    else lines = ({ translated: ["tr"], original: ["orig"], both: ["orig", "tr"], off: [] })[mode] || [];
    const el = $(".subs");
    if (!lines.length) { hideSubs(); return; }
    el.textContent = "";
    for (const ln of lines) {
      const d = document.createElement("div");
      if (ln === "orig") {
        if (lines.length > 1) d.className = "o";
        d.textContent = c.text;
      } else {
        const pairs = c.pairs && c.pairs.length ? c.pairs : Core.plainPairs(c.tr);
        for (const [tw, ow] of pairs) {
          const s = document.createElement("span");
          s.className = "w";
          s.textContent = tw + " ";
          s.addEventListener("mouseenter", () => onWordEnter(s, tw, ow, c));
          s.addEventListener("mouseleave", hideTip);
          d.appendChild(s);
        }
      }
      el.appendChild(d);
    }
    el.hidden = false;
    mount();
  }

  // ------------------------------------------------------------------ меню
  function voicesFor(lang) {
    const all = (window.speechSynthesis && speechSynthesis.getVoices()) || [];
    const m = all.filter((v) => v.lang.toLowerCase().startsWith(lang));
    return m.length ? m : all;
  }
  function pickVoice() {
    const list = voicesFor(CFG.target);
    return list.find((v) => v.name === CFG.voice) ||
      list.find((v) => /natural|online/i.test(v.name) && v.lang.toLowerCase().startsWith(CFG.target)) ||
      list.find((v) => v.lang.toLowerCase().startsWith(CFG.target)) || list[0] || null;
  }
  function buildMenu() {
    const m = $(".menu");
    m.textContent = "";
    const add = (el) => { m.appendChild(el); return el; };
    const head = document.createElement("h3");
    head.innerHTML = "<span>Babelo — перевод идёт</span>";
    const x = document.createElement("button");
    x.className = "x"; x.textContent = "✕"; x.onclick = closeMenu;
    head.appendChild(x);
    add(head);
    const label = (t) => { const l = document.createElement("label"); l.textContent = t; return add(l); };
    const select = (title, opts, key, onch) => {
      label(title);
      const s = document.createElement("select");
      for (const [k, t] of opts) { const o = document.createElement("option"); o.value = k; o.textContent = t; s.appendChild(o); }
      s.value = CFG[key];
      s.onchange = () => { CFG[key] = s.value; saveCfg(); onch && onch(); };
      add(s);
    };
    const slider = (title, lo, hi, key) => {
      const l = label("");
      const upd = () => { l.textContent = title + ": " + CFG[key]; };
      const r = document.createElement("input");
      r.type = "range"; r.min = lo; r.max = hi; r.value = CFG[key];
      r.oninput = () => { CFG[key] = Number(r.value); upd(); };
      r.onchange = saveCfg;
      upd(); add(r);
    };
    const check = (title, key) => {
      const d = document.createElement("div"); d.className = "ck";
      const c = document.createElement("input"); c.type = "checkbox"; c.checked = !!CFG[key];
      c.onchange = () => { CFG[key] = c.checked; saveCfg(); };
      const t = document.createElement("span"); t.textContent = title;
      d.appendChild(c); d.appendChild(t); add(d);
    };
    select("Режим перевода", MODES, "mode", () => { runId++; cancelSpeech(); restoreVolume(); hold = hold; });
    check("Заглушать оригинал на время перевода", "mute_original");
    select("Субтитры", SUBS, "subs", hideSubs);
    slider("Прерывистый: пауза между фразой и переводом, с", 0, 10, "pause_s");
    slider("Прерывистый: сколько раз повторить (оригинал и перевод)", 1, 10, "repeats");
    slider("Запас перевода вперёд, с (1–180)", 1, 180, "lead");
    slider("Скорость озвучки, %", 70, 150, "speed");
    slider("Громкость перевода, %", 10, 100, "volume");
    slider("Громкость оригинала в обычном режиме, %", 0, 100, "orig_vol");
    select("Перевод на", Object.entries(LANGS), "target", onTargetChange);
    select("Язык оригинала (со следующего запуска)", [["auto", "Авто"], ...Object.entries(LANGS)], "source");
    select("Откуда брать перевод", PROVIDERS, "provider", onTargetChange);
    label("Голос");
    const vs = document.createElement("select");
    for (const v of voicesFor(CFG.target)) { const o = document.createElement("option"); o.value = v.name; o.textContent = v.name; vs.appendChild(o); }
    vs.value = (pickVoice() || {}).name || "";
    vs.onchange = () => { CFG.voice = vs.value; saveCfg(); };
    add(vs);
    check("Esc выключает перевод", "esc_stops");
    const bc = document.createElement("button"); bc.textContent = "Полное закрытие программы"; bc.onclick = fullClose; add(bc);
  }
  function openMenu() { buildMenu(); mount(); $(".menu").classList.add("open"); }
  function closeMenu() { $(".menu").classList.remove("open"); }
  const toggleMenu = () => ($(".menu").classList.contains("open") ? closeMenu() : openMenu());
  if (window.speechSynthesis) speechSynthesis.addEventListener("voiceschanged", () => { if ($(".menu").classList.contains("open")) buildMenu(); });

  // ------------------------------------------------------------------ субтитры видео вперёд
  function askMain(type, payload) {
    return new Promise((res) => {
      const id = Math.random().toString(36).slice(2);
      const h = (e) => {
        if (e.source === window && e.data && e.data.babelo === "res" && e.data.id === id) {
          window.removeEventListener("message", h);
          res(e.data.res);
        }
      };
      window.addEventListener("message", h);
      window.postMessage({ babelo: "req", id, type, payload }, "*");
      setTimeout(() => { window.removeEventListener("message", h); res(null); }, 2500);
    });
  }
  async function tryJson(url) {
    try {
      const r = await fetch(url, { credentials: "include" });
      const t = await r.text();
      if (!t) return null;
      return JSON.parse(t);
    } catch (e) { return null; }
  }
  const setFmt = (u) => u.replace(/([?&])fmt=[^&]*/, "$1fmt=json3").replace(/^([^?]*\?)(?!.*fmt=)/, "$1fmt=json3&");
  async function fetchYtEvents(track, extra) {
    let d = await tryJson(track.baseUrl + "&fmt=json3" + (extra || ""));
    if (d && d.events && d.events.length) return d.events;
    // плеер сам знает нужные ключи: включаем в нём субтитры и берём тот адрес, который он запросил
    await askMain("enableCaptions", { lang: track.languageCode });
    for (let i = 0; i < 20; i++) {
      await sleep(300);
      const u = performance.getEntriesByType("resource").map((e) => e.name).reverse()
        .find((n) => n.includes("/api/timedtext") && n.includes("lang=" + track.languageCode));
      if (u) {
        d = await tryJson(setFmt(u) + (extra || ""));
        if (d && d.events && d.events.length) { await askMain("disableCaptions"); return d.events; }
      }
    }
    await askMain("disableCaptions");
    return null;
  }
  async function loadCues() {
    cues = []; srcLang = null; ytTrack = null;
    if (/(^|\.)youtube\.com$/.test(location.hostname)) {
      const info = await askMain("tracks");
      const tracks = (info && info.tracks) || [];
      if (tracks.length) {
        let tr = null;
        if (CFG.source !== "auto") tr = tracks.find((t) => t.languageCode.split("-")[0] === CFG.source);
        if (!tr) {
          const asr = tracks.find((t) => t.kind === "asr");
          tr = asr ? (tracks.find((t) => t.kind !== "asr" && t.languageCode === asr.languageCode) || asr) : tracks[0];
        }
        const events = await fetchYtEvents(tr, "");
        if (!events) throw new Error("не удалось загрузить субтитры YouTube");
        ytTrack = tr;
        srcLang = tr.languageCode.split("-")[0];
        cues = Core.groupCues(Core.normEvents(events), isSpaceless(srcLang));
        return;
      }
    }
    const list = [...video.textTracks].filter((t) => t.kind === "subtitles" || t.kind === "captions");
    if (list.length) {
      const tt = list.find((t) => CFG.source !== "auto" && (t.language || "").startsWith(CFG.source)) || list[0];
      tt.mode = "hidden";
      for (let i = 0; i < 20 && (!tt.cues || !tt.cues.length); i++) await sleep(250);
      if (tt.cues && tt.cues.length) {
        srcLang = (tt.language || "en").split("-")[0];
        cues = Core.groupCues(Core.normVtt(tt.cues), isSpaceless(srcLang));
      }
    }
  }

  // ------------------------------------------------------------------ перевод
  const bg = (msg) => new Promise((res) => chrome.runtime.sendMessage(msg, (r) =>
    res(r || { ok: false, error: (chrome.runtime.lastError || {}).message })));
  async function localOk() {
    const r = await bg({ type: "local", path: "/ping" });
    return !!(r.ok && r.data && r.data.ok && r.data.models);
  }
  async function translateLocal(batch) {
    const r = await bg({ type: "local", path: "/translate", body: { texts: batch.map((g) => g.text), src: srcLang, tgt: CFG.target } });
    if (!r.ok || !r.data || r.data.error) throw new Error((r.data && r.data.error) || r.error || "Babelo не отвечает");
    return r.data.results;
  }
  let chromeTr = null, chromeTrKey = "";
  async function translateChrome(batch) {
    if (!("Translator" in self)) throw new Error("встроенный переводчик браузера недоступен");
    const key = srcLang + ">" + CFG.target;
    if (!chromeTr || chromeTrKey !== key) { chromeTr = await Translator.create({ sourceLanguage: srcLang, targetLanguage: CFG.target }); chromeTrKey = key; }
    const out = [];
    for (const g of batch) out.push({ tr: await chromeTr.translate(g.text), pairs: null });
    return out;
  }
  async function translateYoutubeAll() {
    if (!ytTrack) throw new Error("перевод YouTube есть только на YouTube");
    const ev = await fetchYtEvents(ytTrack, "&tlang=" + CFG.target);
    if (!ev) throw new Error("YouTube не дал переведённые субтитры");
    Core.mapTranslated(cues, Core.normEvents(ev));
    for (const g of cues) g.done = true;
  }
  const revCache = new Map();
  async function reverseWord(word) {
    const w = word.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "") || word;
    const key = w + "|" + CFG.target + "|" + srcLang;
    if (revCache.has(key)) return revCache.get(key);
    let out = "";
    try {
      const r = await bg({ type: "local", path: "/reverse", body: { word: w, src: srcLang, tgt: CFG.target } });
      if (r.ok && r.data && r.data.word) out = r.data.word;
      else if ("Translator" in self) {
        const t = await Translator.create({ sourceLanguage: CFG.target, targetLanguage: srcLang });
        out = await t.translate(w);
      }
    } catch (e) { /* подсказки нет — ничего страшного */ }
    revCache.set(key, out);
    return out;
  }
  function nextBatch() {
    const t = video ? video.currentTime : 0;
    const todo = cues.filter((c) => !c.skip && !c.done);
    const ahead = todo.filter((c) => c.end >= t - 1);
    return (ahead.length ? ahead : todo).slice(0, 6);
  }
  async function translateLoop() {
    const gen = ++loopGen;
    translating = true;
    for (const c of cues) { if (!c.skip) { c.done = false; c.tr = null; c.pairs = null; } }
    const order = [];
    const pv = CFG.provider;
    if ((pv === "auto" || pv === "local") && (await localOk())) order.push("local");
    if ((pv === "auto" || pv === "youtube") && ytTrack) order.push("youtube");
    if (pv === "auto" || pv === "chrome") order.push("chrome");
    if (pv === "local" && !order.includes("local")) { setStatus("Babelo на компьютере не отвечает: запустите программу и скачайте модели"); }
    let lastErr = "";
    for (const name of order) {
      if (!active || gen !== loopGen) break;
      try {
        if (name === "youtube") { await translateYoutubeAll(); lastErr = ""; break; }
        for (;;) {
          if (!active || gen !== loopGen) break;
          const batch = nextBatch();
          if (!batch.length) break;
          const res = await (name === "local" ? translateLocal : translateChrome)(batch);
          batch.forEach((g, i) => { g.tr = res[i].tr; g.pairs = res[i].pairs; g.done = true; });
        }
        lastErr = "";
        break;
      } catch (e) {
        lastErr = String(e.message || e);
        for (const c of cues) { if (!c.skip && !c.tr) c.done = false; }
      }
    }
    if (gen === loopGen) {
      translating = false;
      if (lastErr) setStatus("Перевод не получился: " + lastErr);
    }
  }
  function onTargetChange() {
    if (!active) return;
    cancelSpeech(); hideSubs(); runId++;
    translateLoop();
  }

  // ------------------------------------------------------------------ озвучка
  let curUtt = null;
  const isSpeaking = () => !!window.speechSynthesis && (speechSynthesis.speaking || speechSynthesis.pending);
  function cancelSpeech() { try { speechSynthesis.cancel(); } catch (e) { /* нет озвучки */ } curUtt = null; }
  function speak(text, rate) {
    return new Promise((res) => {
      cancelSpeech();
      const u = new SpeechSynthesisUtterance(text);
      const v = pickVoice();
      if (v) { u.voice = v; u.lang = v.lang; } else u.lang = CFG.target;
      u.rate = rate;
      u.volume = clamp(CFG.volume / 100, 0, 1);
      u.onend = u.onerror = () => { if (curUtt === u) curUtt = null; res(); };
      curUtt = u;
      speechSynthesis.speak(u);
    });
  }

  // ------------------------------------------------------------------ управление видео
  const ourPause = () => { ours.pause = Date.now() + 900; video.pause(); };
  const ourPlay = () => { ours.pause = Date.now() + 900; return video.play().catch(() => {}); };
  const ourSeek = (t) => { ours.seek = Date.now() + 1500; video.currentTime = t; };
  async function waitSeek(t) {
    for (let i = 0; i < 60 && (Math.abs(video.currentTime - t) > 0.6 || video.seeking); i++) await sleep(33);
  }
  function setVol(v) {
    if (savedVol === null) savedVol = { volume: video.volume, muted: video.muted };
    video.muted = false;
    video.volume = clamp(v, 0, 1);
  }
  function restoreVolume() {
    if (savedVol && video) { video.volume = savedVol.volume; video.muted = savedVol.muted; }
    savedVol = null;
  }
  function resetPtr() {
    const t = video ? video.currentTime : 0;
    ptr = cues.findIndex((c) => c.end >= t - 0.3);
    if (ptr < 0) ptr = cues.length;
  }
  function onPause() {
    if (!active || Date.now() < ours.pause || video.ended) return;
    hold = true; runId++;
    cancelSpeech(); hideSubs(); restoreVolume();
    setStatus("Видео на паузе — перевод ждёт. Включите воспроизведение, и перевод продолжится.");
  }
  function onPlay() {
    if (!active || !hold) return;
    hold = false; setStatus(null); resetPtr();
  }
  function onSeeking() {
    if (!active || Date.now() < ours.seek) return;
    cancelSpeech(); hideSubs(); runId++; elastic = false; resetPtr();
  }

  const ready = (t, win) => cues.every((c) => c.skip || c.tr || c.end < t || c.start > t + win);
  function checkBuffering(t) {
    if (!translating) {
      if (buffering) { buffering = false; setStatus(null); ourPlay(); }
      return false;
    }
    const lead = clamp(CFG.lead, 1, 180);
    if (!buffering) {
      if (!ready(t, 2)) { buffering = true; ourPause(); cancelSpeech(); }
    } else if (ready(t, lead)) { buffering = false; setStatus(null); ourPlay(); }
    if (buffering) {
      const total = cues.filter((c) => !c.skip).length || 1;
      setStatus("Подготовка перевода " + Math.round(100 * cues.filter((c) => !c.skip && c.tr).length / total) + " %");
    }
    return buffering;
  }
  function speakCue(c) {
    shown = c;
    renderSubs(c, "tr");
    const win = Math.max(1, (cues[ptr] ? Math.min(cues[ptr].start, c.end + 2) : c.end + 2) - c.start);
    speak(c.tr, Core.fitRate(c.tr, win, CFG.speed / 100));
  }
  function tick() {
    if (!active || !video || hold || busy) return;
    const t = video.currentTime;
    if (CFG.mode === "interrupted") return tickInterrupted(t);
    setVol(CFG.mode === "continuous" ? 0 : (CFG.mute_original ? CFG.orig_vol / 100 : 1));
    if (checkBuffering(t)) return;
    while (ptr < cues.length) {
      const c = cues[ptr];
      if (c.skip) { ptr++; continue; }
      if (t < c.start - 0.2) break;
      if (!c.tr && t <= c.end + 1.5) break;
      ptr++;
      if (c.tr && t <= c.end + 1.5) speakCue(c);
    }
    if (shown && t > shown.end + 0.2) {
      if (isSpeaking()) {                                       // речь длиннее фразы: видео чуть ждёт
        if (!elastic) { elastic = true; elasticT = Date.now(); ourPause(); }
        else if (Date.now() - elasticT > 6000) cancelSpeech();
      } else {
        if (elastic) { elastic = false; ourPlay(); }
        hideSubs(); shown = null;
      }
    }
  }

  // --- прерывистый режим: оригинал -> пауза -> тот же отрывок с переводом (повторы и паузы настраиваются)
  function tickInterrupted(t) {
    setVol(1);
    while (ptr < cues.length && (cues[ptr].skip || t >= cues[ptr].end + 0.05)) {
      const c = cues[ptr++];
      if (!c.skip && t - c.end < 0.8) { runInterrupted(c); return; }
    }
  }
  async function sleepA(ms, alive) {
    const t0 = Date.now();
    while (alive() && Date.now() - t0 < ms) await sleep(50);
  }
  async function playFragment(c, alive, withSpeech) {
    ourSeek(c.start);
    await waitSeek(c.start);
    if (withSpeech) speak(c.tr, Core.fitRate(c.tr, c.end - c.start, CFG.speed / 100));
    await ourPlay();
    const t0 = Date.now();
    while (alive() && video.currentTime < c.end && Date.now() - t0 < (c.end - c.start) * 1000 + 4000) await sleep(30);
    ourPause();
    if (withSpeech) {
      while (alive() && isSpeaking()) await sleep(50);
      if (!alive()) cancelSpeech();
    }
  }
  async function runInterrupted(c) {
    busy = true;
    const my = ++runId;
    const alive = () => active && !hold && my === runId;
    try {
      ourPause();
      for (let i = 0; alive() && !c.tr && translating && i < 300; i++) { setStatus("Перевожу фразу…"); await sleep(100); }
      setStatus(null);
      if (!c.tr || !alive()) return;
      const n = clamp(CFG.repeats, 1, 10), gap = clamp(CFG.pause_s, 0, 10) * 1000;
      for (let r = 0; r < n && alive(); r++) {
        if (r > 0) { renderSubs(c, "orig"); setVol(1); await playFragment(c, alive, false); }
        await sleepA(gap, alive);
        renderSubs(c, "tr");
        setVol(CFG.mute_original ? 0 : 1);
        await playFragment(c, alive, true);
        await sleepA(gap, alive);
      }
      hideSubs();
      if (alive()) { ourSeek(c.end); setVol(1); await ourPlay(); }
    } finally { busy = false; }
  }

  // ------------------------------------------------------------------ запуск и остановка
  function pickVideo() {
    let best = null, area = 0;
    for (const v of document.querySelectorAll("video")) {
      if (v.readyState === 0 && v.paused) continue;
      const r = v.getBoundingClientRect();
      if (r.width * r.height > area) { area = r.width * r.height; best = v; }
    }
    return best;
  }
  async function start() {
    if (active || closed) return;
    video = pickVideo();
    if (!video) { showBanner(true); setStatus("Видео на странице не найдено"); setTimeout(() => { if (!active) showBanner(false); }, 3000); return; }
    active = true; hold = false; busy = false; buffering = false; elastic = false; shown = null; ptr = 0; runId++;
    showBanner(true);
    video.addEventListener("pause", onPause);
    video.addEventListener("play", onPlay);
    video.addEventListener("seeking", onSeeking);
    timer = setInterval(tick, 40);
    setStatus("Ищу субтитры видео…");
    try { await loadCues(); } catch (e) { setStatus("Субтитры: " + (e.message || e)); return; }
    if (!active) return;
    if (!cues.length) { setStatus("У этого видео нет субтитров — используйте программу Babelo (звук компьютера) или режим файла"); return; }
    if (!(srcLang in LANGS)) { setStatus("Язык видео «" + srcLang + "» пока не поддерживается"); return; }
    if (srcLang === CFG.target) { setStatus("Язык видео уже совпадает с языком перевода"); return; }
    setStatus(null);
    resetPtr();
    translateLoop();
  }
  function stop() {
    if (!active) return;
    active = false; runId++; loopGen++;
    clearInterval(timer);
    if (video) {
      video.removeEventListener("pause", onPause);
      video.removeEventListener("play", onPlay);
      video.removeEventListener("seeking", onSeeking);
    }
    cancelSpeech(); hideSubs(); closeMenu(); restoreVolume(); setStatus(null);
    if ((buffering || elastic) && video) ourPlay();
    buffering = elastic = false; translating = false; cues = []; shown = null;
    showBanner(false);
  }
  function fullClose() { stop(); closed = true; host.remove(); }

  function onF10() {
    const now = Date.now();
    const quick = now - lastF10 < 450;
    lastF10 = now;
    if (!active) start();
    else if (quick) stop();
    else toggleMenu();
  }
  window.addEventListener("keydown", (e) => {
    if (closed) return;
    if (e.key === "F10") { e.preventDefault(); e.stopPropagation(); onF10(); }
    else if (e.key === "Escape" && active && CFG.esc_stops) stop();
  }, true);

  // ------------------------------------------------------------------ мост к программе Babelo
  // Если программа Babelo запущена, она получает отсюда точную позицию видео и может его перематывать (пауза/пуск/перемотка).
  let bridgeNext = 0, lastCmd = 0, bridgeSession = null;
  async function bridgeTick() {
    if (closed || Date.now() < bridgeNext || document.visibilityState !== "visible") return;
    const v = active && video ? video : pickVideo();
    if (!v) return;
    const body = { pos: v.currentTime, playing: !v.paused && !v.ended, rate: v.playbackRate, ack: lastCmd,
      seek: isFinite(v.duration) && v.duration > 0 && !!(v.seekable && v.seekable.length) };
    const r = await bg({ type: "local", path: "/media/state", body });
    if (!r.ok || !r.data) { bridgeNext = Date.now() + 3000; return; }
    if (r.data.session !== bridgeSession) { bridgeSession = r.data.session; lastCmd = 0; }
    const c = r.data.cmd;
    if (c && c.id > lastCmd) {
      lastCmd = c.id;
      ours.pause = ours.seek = Date.now() + 1500;
      if (c.type === "play") v.play().catch(() => {});
      else if (c.type === "pause") v.pause();
      else if (c.type === "seek" && typeof c.t === "number") v.currentTime = c.t;
      setTimeout(bridgeTick, 60);
    }
  }
  setInterval(bridgeTick, 250);

  chrome.runtime.onMessage.addListener((m, s, send) => {
    if (m && m.type === "toggle") { if (active) stop(); else start(); send({ active }); }
    else if (m && m.type === "state") send({ active, status: statusText, closed });
  });
  loadCfg();
})();
