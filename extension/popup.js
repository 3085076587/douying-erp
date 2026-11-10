const baseInput = document.getElementById("base");
const tokenInput = document.getElementById("token");
const button = document.getElementById("add");
const status = document.getElementById("status");

chrome.storage.local.get(["apiBase", "token"], function (saved) {
  if (saved.apiBase) baseInput.value = saved.apiBase;
  if (saved.token) tokenInput.value = saved.token;
});

function show(text) {
  status.textContent = text || "";
}

button.addEventListener("click", async function () {
  const apiBase = baseInput.value.trim().replace(/\/$/, "");
  const token = tokenInput.value.trim();
  if (!apiBase || !token) {
    show("先填服务器和后台令牌");
    return;
  }
  let origin = "";
  try {
    origin = new URL(apiBase).origin + "/*";
  } catch (err) {
    show("服务器地址不正确");
    return;
  }
  button.disabled = true;
  const allowed = await chrome.permissions.request({ origins: [origin] });
  if (!allowed) {
    button.disabled = false;
    show("需要允许访问这个服务器");
    return;
  }
  await chrome.storage.local.set({ apiBase: apiBase, token: token });
  const result = await chrome.runtime.sendMessage({ type: "start-login", apiBase: apiBase, token: token });
  show(result && result.detail ? result.detail : "已打开登录页");
  button.disabled = false;
});

setInterval(function () {
  chrome.runtime.sendMessage({ type: "status" }, function (result) {
    if (chrome.runtime.lastError || !result || !result.detail) return;
    show(result.detail);
  });
}, 1000);
