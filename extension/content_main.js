// Работает внутри страницы YouTube: отдаёт список дорожек субтитров и умеет включить субтитры в самом плеере.
(() => {
  const player = () => document.getElementById("movie_player");
  window.addEventListener("message", (e) => {
    if (e.source !== window || !e.data || e.data.babelo !== "req") return;
    let res = null;
    try {
      const p = player();
      if (e.data.type === "tracks") {
        const pr = (p && p.getPlayerResponse && p.getPlayerResponse()) || window.ytInitialPlayerResponse;
        const tl = pr && pr.captions && pr.captions.playerCaptionsTracklistRenderer;
        const tracks = ((tl && tl.captionTracks) || []).map((t) => ({
          baseUrl: t.baseUrl,
          languageCode: t.languageCode,
          kind: t.kind || "",
          name: (t.name && (t.name.simpleText || (t.name.runs || []).map((r) => r.text).join(""))) || ""
        }));
        res = { tracks, videoId: pr && pr.videoDetails && pr.videoDetails.videoId };
      } else if (e.data.type === "enableCaptions" && p) {
        if (p.loadModule) p.loadModule("captions");
        if (p.setOption) p.setOption("captions", "track", { languageCode: e.data.payload && e.data.payload.lang });
        res = { ok: true };
      } else if (e.data.type === "disableCaptions" && p) {
        if (p.unloadModule) p.unloadModule("captions");
        res = { ok: true };
      }
    } catch (err) {
      res = { tracks: [], error: String(err) };
    }
    window.postMessage({ babelo: "res", id: e.data.id, res }, "*");
  });
})();
