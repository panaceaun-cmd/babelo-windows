chrome.runtime.sendMessage({ type: "local", path: "/ping" }, (r) => {
  const st = document.getElementById("st");
  const ok = r && r.ok && r.data && r.data.ok;
  st.textContent = ok ? "Программа Babelo найдена (сборка " + r.data.build + "). Нажмите F10." :
    "Программа Babelo не запущена. Запустите её и нажмите F10.";
});
