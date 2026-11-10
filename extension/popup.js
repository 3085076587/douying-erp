const API_BASE = "http://120.76.58.132:8082";

const loginBox = document.getElementById("login-box");
const logged = document.getElementById("logged");
const usernameInput = document.getElementById("username");
const passwordInput = document.getElementById("password");
const button = document.getElementById("add");
const status = document.getElementById("status");

let savedToken = "";

function show(text) {
  status.textContent = text || "";
}

function tokenAlive(token) {
  const parts = String(token || "").split("|");
  if (parts.length !== 4) return false;
  const expires = Number(parts[2]);
  return Number.isFinite(expires) && expires > Date.now() / 1000 + 30;
}

function showLogin(needLogin) {
  loginBox.hidden = !needLogin;
  logged.hidden = needLogin;
}

async function remember(token, username) {
  savedToken = token;
  const data = { token: token };
  if (username) data.username = username;
  await chrome.storage.local.set(data);
  showLogin(false);
}

async function readDashboardToken() {
  const allowed = await chrome.permissions.contains({ origins: [API_BASE + "/*"] });
  if (!allowed) return "";
  const tabs = await chrome.tabs.query({ url: API_BASE + "/*" });
  for (let i = 0; i < tabs.length; i += 1) {
    if (!tabs[i].id) continue;
    try {
      const [injected] = await chrome.scripting.executeScript({
        target: { tabId: tabs[i].id },
        func: function () {
          return localStorage.getItem("erp_access_token") || "";
        },
      });
      const token = injected && injected.result;
      if (tokenAlive(token)) return token;
    } catch (err) {
      /* 这个后台标签读不到，换下一个 */
    }
  }
  return "";
}

chrome.storage.local.get(["username", "token"], async function (saved) {
  if (saved.username) usernameInput.value = saved.username;
  if (tokenAlive(saved.token)) {
    savedToken = saved.token;
    showLogin(false);
    return;
  }
  const token = await readDashboardToken();
  if (tokenAlive(token)) await remember(token, saved.username || "");
});

document.getElementById("switch").addEventListener("click", async function () {
  savedToken = "";
  await chrome.storage.local.remove("token");
  showLogin(true);
  show("");
});

button.addEventListener("click", async function () {
  button.disabled = true;
  const allowed = await chrome.permissions.request({ origins: [API_BASE + "/*"] });
  if (!allowed) {
    button.disabled = false;
    show("需要允许访问后台");
    return;
  }
  let token = tokenAlive(savedToken) ? savedToken : "";
  if (!token) token = await readDashboardToken();
  if (!token) {
    const username = usernameInput.value.trim();
    const password = passwordInput.value;
    if (!username || !password) {
      showLogin(true);
      button.disabled = false;
      show("请填写账号和密码");
      return;
    }
    try {
      const response = await fetch(API_BASE + "/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username, password: password }),
      });
      if (response.status === 401) {
        button.disabled = false;
        show("账号或密码错误");
        return;
      }
      if (!response.ok) {
        button.disabled = false;
        show("登录失败");
        return;
      }
      const data = await response.json();
      token = data.access_token || "";
      passwordInput.value = "";
      if (token) await remember(token, username);
    } catch (err) {
      button.disabled = false;
      show("登录失败");
      return;
    }
  }
  if (!tokenAlive(token)) {
    showLogin(true);
    button.disabled = false;
    show("请填写账号和密码");
    return;
  }
  await remember(token);
  const result = await chrome.runtime.sendMessage({
    type: "capture",
    apiBase: API_BASE,
    token: token,
  });
  const detail = result && result.detail ? result.detail : "正在获取";
  if (detail.indexOf("401") >= 0) {
    savedToken = "";
    await chrome.storage.local.remove("token");
    showLogin(true);
  }
  show(detail);
  button.disabled = false;
});

setInterval(function () {
  chrome.runtime.sendMessage({ type: "status" }, function (result) {
    if (chrome.runtime.lastError || !result || !result.detail) return;
    show(result.detail);
  });
}, 1000);
