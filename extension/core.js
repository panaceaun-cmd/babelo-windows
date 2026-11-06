// Чистые функции без доступа к странице (их можно проверять отдельно).
(function (root) {
  const SENT_END = /[.!?…。！？]["')»”]*$/;
  const SOUND = /^\s*(\[.*\]|\(.*\)|♪.*)\s*$/;

  function eventText(ev) {
    return (ev.segs || []).map((s) => s.utf8 || "").join("").replace(/\s*\n\s*/g, " ").trim();
  }

  // события YouTube (json3) -> [{start,end,text}] в секундах
  function normEvents(events) {
    const out = [];
    for (const ev of events || []) {
      const text = eventText(ev);
      if (!text) continue;
      const start = (ev.tStartMs || 0) / 1000;
      const dur = (ev.dDurationMs || 0) / 1000;
      out.push({ start, end: start + (dur || 2), text });
    }
    out.sort((a, b) => a.start - b.start);
    for (let i = 0; i < out.length - 1; i++) {
      if (out[i].end > out[i + 1].start + 0.3) out[i].end = out[i + 1].start + 0.3;
    }
    return out;
  }

  // дорожки HTML5 (WebVTT) -> то же самое
  function normVtt(list) {
    const out = [];
    for (let i = 0; i < list.length; i++) {
      const c = list[i];
      const text = String(c.text || "").replace(/<[^>]+>/g, "").replace(/\s*\n\s*/g, " ").trim();
      if (text) out.push({ start: c.startTime, end: c.endTime, text });
    }
    return out;
  }

  // короткие реплики -> фразы: так перевод и озвучка звучат естественнее
  function groupCues(cues, spaceless) {
    const groups = [];
    let cur = null;
    const join = (a, b) => (spaceless ? a + b : a + " " + b);
    for (const c of cues) {
      if (SOUND.test(c.text)) {
        if (cur) { groups.push(cur); cur = null; }
        groups.push({ start: c.start, end: c.end, text: c.text, skip: true });
        continue;
      }
      if (!cur) { cur = { start: c.start, end: c.end, text: c.text }; continue; }
      const gap = c.start - cur.end;
      const dur = c.end - cur.start;
      const ended = SENT_END.test(cur.text) && cur.end - cur.start >= 2.5;
      if (gap > 1.2 || dur > 11 || ended || cur.text.length > 220) {
        groups.push(cur);
        cur = { start: c.start, end: c.end, text: c.text };
      } else {
        cur.text = join(cur.text, c.text);
        cur.end = c.end;
      }
    }
    if (cur) groups.push(cur);
    return groups;
  }

  // переведённые реплики YouTube (tlang) -> по фразам по времени
  function mapTranslated(groups, trCues) {
    let j = 0;
    for (const g of groups) {
      if (g.skip) continue;
      while (j < trCues.length && trCues[j].start < g.start - 0.05) j++;
      const parts = [];
      let k = j;
      while (k < trCues.length && trCues[k].start < g.end - 0.01) { parts.push(trCues[k].text); k++; }
      j = k;
      g.tr = parts.join(" ").trim() || null;
      g.pairs = null;
    }
  }

  // скорость речи, чтобы фраза уложилась в своё окно (около 13 знаков в секунду при обычной скорости)
  function fitRate(text, windowSec, base) {
    const need = text.length / (13 * Math.max(1, windowSec));
    return Math.min(2.4, Math.max(0.6, Math.max(base, need)));
  }

  function plainPairs(text) {
    return String(text).split(/\s+/).filter(Boolean).map((w) => [w, null]);
  }

  const api = { normEvents, normVtt, groupCues, mapTranslated, fitRate, plainPairs };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.BabeloCore = api;
})(typeof window !== "undefined" ? window : globalThis);
