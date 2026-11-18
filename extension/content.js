// Babelo — соединение браузера с программой Babelo (Windows). Здесь нет своих меню и озвучки:
// всё управление, перевод, голос, субтитры и меню (F10) — в программе Babelo. Расширение лишь
//   1) сообщает ей положение видео и выполняет её команды (пуск, пауза, перемотка, громкость видео);
//   2) отдаёт ей субтитры видео заранее, чтобы перевод шёл без повторов.
(() => {
  "use strict";
  if (window.__babeloLoaded) return;
  window.__babeloLoaded = true;
  const Core = window.BabeloCore;
  const MY_VERSION = chrome.runtime.getManifest().version;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const isSpaceless = (l) => ["zh", "ja"].includes(l);
  const bg = (msg) => new Promise((res) => chrome.runtime.sendMessage(msg, (r) =>
    res(r || { ok: false, error: (chrome.runtime.lastError || {}).message })));

  function pickVideo() {
    let best = null, area = 0;
    for (const v of document.querySelectorAll("video")) {
      if (v.readyState === 0 && v.paused) continue;
      const r = v.getBoundingClientRect();
      if (r.width * r.height > area) { area = r.width * r.height; best = v; }
    }
    return best;
  }

  // ------------------------------------------------------------------ субтитры видео
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
      return t ? JSON.parse(t) : null;
    } catch (e) { return null; }
  }
  const setFmt = (u) => u.replace(/([?&])fmt=[^&]*/, "$1fmt=json3").replace(/^([^?]*\?)(?!.*fmt=)/, "$1fmt=json3&");
  async function fetchYtEvents(track) {
    let d = await tryJson(track.baseUrl + "&fmt=json3");
    if (d && d.events && d.events.length) return d.events;
    // плеер сам знает нужные ключи: включаем в нём субтитры и берём тот адрес, который он запросил
    await askMain("enableCaptions", { lang: track.languageCode });
    for (let i = 0; i < 20; i++) {
      await sleep(300);
      const u = performance.getEntriesByType("resource").map((e) => e.name).reverse()
        .find((n) => n.includes("/api/timedtext") && n.includes("lang=" + track.languageCode));
      if (u) {
        d = await tryJson(setFmt(u));
        if (d && d.events && d.events.length) { await askMain("disableCaptions"); return d.events; }
      }
    }
    await askMain("disableCaptions");
    return null;
  }
  async function loadCues(video, pref) {
    if (/(^|\.)youtube\.com$/.test(location.hostname)) {
      const info = await askMain("tracks");
      const tracks = (info && info.tracks) || [];
      if (tracks.length) {
        let tr = pref ? tracks.find((t) => t.languageCode.split("-")[0] === pref) : null;
        if (!tr) {
          const asr = tracks.find((t) => t.kind === "asr");
          tr = asr ? (tracks.find((t) => t.kind !== "asr" && t.languageCode === asr.languageCode) || asr) : tracks[0];
        }
        const events = await fetchYtEvents(tr);
        if (!events) throw new Error("YouTube не отдал субтитры");
        const lang = tr.languageCode.split("-")[0];
        return { lang, kind: tr.kind || "manual", cues: Core.groupCues(Core.normEvents(events), isSpaceless(lang)) };
      }
    }
    const list = [...(video ? video.textTracks : [])].filter((t) => t.kind === "subtitles" || t.kind === "captions");
    if (list.length) {
      const tt = list.find((t) => pref && (t.language || "").startsWith(pref)) || list[0];
      tt.mode = "hidden";
      for (let i = 0; i < 20 && (!tt.cues || !tt.cues.length); i++) await sleep(250);
      if (tt.cues && tt.cues.length) {
        const lang = (tt.language || "en").split("-")[0];
        return { lang, kind: tt.kind, cues: Core.groupCues(Core.normVtt(tt.cues), isSpaceless(lang)) };
      }
    }
    throw new Error("у видео нет субтитров");
  }
  async function handleCaptions(cmd, video) {
    let data;
    try {
      const r = await loadCues(video, cmd.lang);
      data = { ok: true, lang: r.lang, kind: r.kind, cues: r.cues };
    } catch (e) { data = { ok: false, error: String((e && e.message) || e) }; }
    await bg({ type: "local", path: "/media/result", body: { id: cmd.id, data } });
  }

  // ------------------------------------------------------------------ связь с программой
  let savedVol = null;
  function applyVolume(v, vid) {
    if (v === null || v === undefined) {
      if (savedVol) { vid.volume = savedVol.volume; vid.muted = savedVol.muted; savedVol = null; }
      return;
    }
    if (!savedVol) savedVol = { volume: vid.volume, muted: vid.muted };
    vid.muted = false;
    vid.volume = Math.min(1, Math.max(0, v));
  }
  const newer = (a, b) => {
    const x = String(a).split(".").map(Number), y = String(b).split(".").map(Number);
    for (let i = 0; i < Math.max(x.length, y.length); i++) { if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0); }
    return false;
  };
  let nextTry = 0, lastCmd = 0, session = null;
  async function tick() {
    if (Date.now() < nextTry || document.visibilityState !== "visible") return;
    const v = pickVideo();
    if (!v) return;
    const body = { pos: v.currentTime, playing: !v.paused && !v.ended, rate: v.playbackRate, ack: lastCmd,
      seek: isFinite(v.duration) && v.duration > 0 && !!(v.seekable && v.seekable.length) };
    const r = await bg({ type: "local", path: "/media/state", body });
    if (!r.ok || !r.data) { nextTry = Date.now() + 3000; return; }
    if (r.data.session !== session) { session = r.data.session; lastCmd = 0; }
    // программа обновилась и положила новые файлы расширения: перезагружаемся один раз
    if (r.data.ext && newer(r.data.ext, MY_VERSION)) {
      chrome.storage.local.get("reloadedFor", (d) => {
        if (d.reloadedFor !== r.data.ext) chrome.storage.local.set({ reloadedFor: r.data.ext }, () => bg({ type: "reload" }));
      });
    }
    const c = r.data.cmd;
    if (c && c.id > lastCmd) {
      lastCmd = c.id;
      if (c.type === "play") v.play().catch(() => {});
      else if (c.type === "pause") v.pause();
      else if (c.type === "seek" && typeof c.t === "number") v.currentTime = c.t;
      else if (c.type === "volume") applyVolume(c.v, v);
      else if (c.type === "captions") handleCaptions(c, v);
      setTimeout(tick, 60);
    }
  }
  setInterval(tick, 250);
})();
