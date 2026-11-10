const LOGIN_URL = "https://fxg.jinritemai.com/login/common";
const SESSION_NAMES = ["sessionid", "sessionid_ss", "sid_tt", "uid_tt", "sid_guard"];
const ALARM = "dy-account-import";

let watch = null;

function collectPage() {
  const localStorageData = {};
  for (let i = 0; i < localStorage.length; i += 1) {
    const key = localStorage.key(i);
    localStorageData[key] = localStorage.getItem(key) || "";
  }
  const text = document.body ? document.body.innerText : "";
  const email = (text.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/) || [""])[0];
  const shops = [];
  const seen = {};
  document.querySelectorAll("div,span,p,a").forEach(function (el) {
    if (el.children.length) return;
    const name = (el.innerText || "").trim();
    if (name.length < 2 || name.length > 30 || seen[name]) return;
    if (!/店|专营|企业|旗舰|个体/.test(name)) return;
    seen[name] = 1;
    shops.push({ name: name });
  });
  return {
    account: email,
    shops: shops.slice(0, 50),
    local_storage: localStorageData,
    ua: navigator.userAgent,
  };
}

async function markerOf(cookies) {
  const text = SESSION_NAMES.map(function (name) {
    const found = cookies.find(function (item) { return item.name === name; });
    return name + "=" + (found ? found.value : "");
  }).join(";");
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest)).map(function (item) {
    return item.toString(16).padStart(2, "0");
  }).join("");
}

function hasSession(cookies) {
  return cookies.some(function (item) {
    return SESSION_NAMES.indexOf(item.name) >= 0 && item.value;
  });
}

function inWorkbench(url) {
  if (!url || url.indexOf("jinritemai.com") < 0 || url.indexOf("/login/") >= 0) return false;
  return url.indexOf("/ffa/") >= 0 || url.indexOf("homepage") >= 0;
}

async function finish(detail) {
  watch.detail = detail;
  watch.state = "done";
  await chrome.alarms.clear(ALARM);
}

async function check() {
  if (!watch || watch.state === "done" || watch.busy) return;
  watch.busy = true;
  try {
    let tab;
    try {
      tab = await chrome.tabs.get(watch.tabId);
    } catch (err) {
      await finish("登录页已关闭");
      return;
    }
    const cookies = await chrome.cookies.getAll({ domain: ".jinritemai.com" });
    const marker = await markerOf(cookies);
    const ready = inWorkbench(tab.url || "") || (hasSession(cookies) && marker !== watch.marker);
    if (!ready) return;
    const [injected] = await chrome.scripting.executeScript({
      target: { tabId: watch.tabId },
      func: collectPage,
    });
    const page = (injected && injected.result) || {};
    const payload = {
      account: page.account || "",
      shops: page.shops || [],
      cookies: cookies.map(function (item) {
        return {
          name: item.name,
          value: item.value,
          domain: item.domain,
          path: item.path,
          secure: item.secure,
          httpOnly: item.httpOnly,
          expirationDate: item.expirationDate || null,
          sameSite: item.sameSite || "",
        };
      }),
      local_storage: page.local_storage || {},
      ua: page.ua || "",
    };
    watch.state = "uploading";
    const response = await fetch(watch.apiBase + "/api/v1/accounts/import", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer " + watch.token,
      },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      watch.state = "waiting";
      watch.detail = "上传失败 " + response.status;
      return;
    }
    const data = await response.json();
    await finish("已导入，cookie " + (data.cookie_count || payload.cookies.length) + " 个");
  } catch (err) {
    if (watch && watch.state !== "done") {
      watch.state = "waiting";
      watch.detail = "上传失败";
    }
  } finally {
    if (watch) watch.busy = false;
  }
}

chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (message.type === "status") {
    sendResponse(watch ? { state: watch.state, detail: watch.detail } : { state: "idle", detail: "" });
    return false;
  }
  if (message.type !== "start-login") return false;
  chrome.cookies.getAll({ domain: ".jinritemai.com" }).then(function (cookies) {
    return markerOf(cookies);
  }).then(function (marker) {
    return chrome.tabs.create({ url: LOGIN_URL, active: true }).then(function (tab) {
      watch = {
        tabId: tab.id,
        apiBase: message.apiBase,
        token: message.token,
        marker: marker,
        state: "waiting",
        detail: "请在新标签登录",
        busy: false,
      };
      return chrome.alarms.create(ALARM, { delayInMinutes: 1, periodInMinutes: 1 });
    });
  }).then(function () {
    sendResponse({ detail: watch.detail });
  }).catch(function () {
    sendResponse({ detail: "登录页没有打开" });
  });
  return true;
});

chrome.tabs.onUpdated.addListener(function (tabId, info) {
  if (!watch || tabId !== watch.tabId || info.status !== "complete") return;
  check();
});

chrome.cookies.onChanged.addListener(function (change) {
  if (!watch || change.removed || SESSION_NAMES.indexOf(change.cookie.name) < 0) return;
  check();
});

chrome.alarms.onAlarm.addListener(function (alarm) {
  if (alarm.name === ALARM) check();
});
