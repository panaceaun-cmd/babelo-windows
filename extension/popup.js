const st = document.getElementById("st");
function ask(type, cb) {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs[0]) return cb(null);
    chrome.tabs.sendMessage(tabs[0].id, { type }, (r) => cb(chrome.runtime.lastError ? null : r));
  });
}
function show(r) {
  if (!r) { st.textContent = "На этой странице расширение не работает (обновите страницу и попробуйте ещё раз)."; return; }
  st.textContent = r.active ? "Перевод включён. " + (r.status || "") : "Перевод выключен.";
}
document.getElementById("b").onclick = () => ask("toggle", () => setTimeout(() => ask("state", show), 300));
ask("state", show);
