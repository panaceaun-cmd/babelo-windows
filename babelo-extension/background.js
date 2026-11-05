// Запросы к Babelo на этом же компьютере (127.0.0.1) идут из фонового скрипта: страницы сайтов туда не пускают.
chrome.runtime.onMessage.addListener((msg, sender, send) => {
  if (msg && msg.type === "local") {
    fetch("http://127.0.0.1:47831" + msg.path, {
      method: msg.body ? "POST" : "GET",
      headers: { "Content-Type": "application/json" },
      body: msg.body ? JSON.stringify(msg.body) : undefined
    })
      .then((r) => r.json())
      .then((data) => send({ ok: true, data }))
      .catch((e) => send({ ok: false, error: String(e) }));
    return true;
  }
});
