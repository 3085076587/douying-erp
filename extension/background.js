const SKIP_NAMES = {
  子账号: 1,
  个体店: 1,
  企业店: 1,
  正常营业: 1,
  抖音工作室: 1,
  请选择店铺: 1,
  选择店铺: 1,
  返回: 1,
};

let job = { state: "idle", detail: "" };

function sleep(ms) {
  return new Promise(function (resolve) {
    setTimeout(resolve, ms);
  });
}

function readPicker() {
  const text = document.body ? document.body.innerText : "";
  const picker = text.indexOf("请选择店铺") >= 0 || text.indexOf("选择店铺") >= 0;
  const captcha = /滑块|向右滑动|拖动滑块|按住左边按钮/.test(text);
  const localStorageData = {};
  for (let i = 0; i < localStorage.length; i += 1) {
    const key = localStorage.key(i);
    localStorageData[key] = localStorage.getItem(key) || "";
  }
  const email = (text.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/) || [""])[0];
  const shops = [];
  const seen = {};
  document.querySelectorAll("div,span,p,a,li").forEach(function (el) {
    if (el.children.length) return;
    const name = (el.innerText || "").replace(/\s+/g, "");
    if (name.length < 2 || name.length > 30 || seen[name]) return;
    let card = el;
    let blob = "";
    for (let i = 0; i < 8 && card; i += 1) {
      blob = card.innerText || "";
      if (blob.indexOf("子账号") >= 0 || blob.indexOf("正常营业") >= 0) break;
      card = card.parentElement;
    }
    if (blob.indexOf("子账号") < 0 && blob.indexOf("正常营业") < 0) return;
    seen[name] = 1;
    shops.push(name);
  });
  return {
    picker: picker,
    captcha: captcha,
    account: email,
    shops: shops,
    local_storage: localStorageData,
    ua: navigator.userAgent,
  };
}

function clickShop(name) {
  const want = (name || "").replace(/\s+/g, "");
  const nodes = document.querySelectorAll("div,span,p,a,li,button");
  let hit = null;
  let area = 1e15;
  for (let i = 0; i < nodes.length; i += 1) {
    const el = nodes[i];
    if ((el.textContent || "").replace(/\s+/g, "") !== want) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    const size = rect.width * rect.height;
    if (size < area) {
      hit = el;
      area = size;
    }
  }
  if (!hit) return false;
  try {
    hit.scrollIntoView({ block: "center", inline: "nearest" });
  } catch (err) {
    /* 页面滚不到也不影响点击 */
  }
  let card = hit;
  for (let i = 0; i < 6 && card; i += 1) {
    card.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window, button: 0 }));
    card.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window, button: 0 }));
    card.click();
    if ((card.innerText || "").length > 80) break;
    card = card.parentElement;
  }
  return true;
}

function pageBack() {
  history.back();
}

async function inspect(tabId) {
  const [injected] = await chrome.scripting.executeScript({
    target: { tabId: tabId },
    func: readPicker,
  });
  return (injected && injected.result) || { picker: false, captcha: false, shops: [] };
}

async function findPickerTab() {
  const tabs = await chrome.tabs.query({ url: "*://*.jinritemai.com/*" });
  for (let i = 0; i < tabs.length; i += 1) {
    const tab = tabs[i];
    if (!tab.id) continue;
    try {
      const page = await inspect(tab.id);
      if (page.picker) return { tab: tab, page: page };
    } catch (err) {
      /* 这个标签还读不了，换下一个 */
    }
  }
  return null;
}

function cookiePayload(cookies) {
  return cookies.map(function (item) {
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
  });
}

async function upload(apiBase, token, page, shopName) {
  const cookies = await chrome.cookies.getAll({ domain: ".jinritemai.com" });
  const response = await fetch(apiBase + "/api/v1/accounts/import", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + token,
    },
    body: JSON.stringify({
      account: page.account || "",
      shops: [{ name: shopName }],
      cookies: cookiePayload(cookies),
      local_storage: page.local_storage || {},
      ua: page.ua || "",
    }),
  });
  if (!response.ok) {
    throw new Error(String(response.status));
  }
  return response.json();
}

async function waitForLeave(tabId) {
  const start = Date.now();
  while (Date.now() - start < 12000) {
    await sleep(500);
    let page;
    try {
      page = await inspect(tabId);
    } catch (err) {
      continue;
    }
    if (page.captcha) return page;
    if (!page.picker) return page;
  }
  return null;
}

async function waitForPicker(tabId) {
  const start = Date.now();
  while (Date.now() - start < 12000) {
    await sleep(500);
    try {
      const page = await inspect(tabId);
      if (page.picker && !page.captcha) return page;
    } catch (err) {
      /* 返回的页面还在加载 */
    }
  }
  return null;
}

async function captureAll(apiBase, token) {
  if (job.state === "running") return job.detail || "正在获取";
  job = { state: "running", detail: "正在查找选择店铺的页面" };
  try {
    const found = await findPickerTab();
    if (!found) {
      job = { state: "done", detail: "请先停在「请选择店铺」这个页面" };
      return job.detail;
    }
    const tabId = found.tab.id;
    await chrome.tabs.update(tabId, { active: true });
    const names = found.page.shops.filter(function (name) {
      return !SKIP_NAMES[name];
    });
    if (!names.length) {
      job = { state: "done", detail: "这个页面上没有店铺" };
      return job.detail;
    }
    let saved = 0;
    for (let index = 0; index < names.length; index += 1) {
      const name = names[index];
      job.detail = "正在进入" + name + "（" + (index + 1) + "/" + names.length + "）";
      const [clicked] = await chrome.scripting.executeScript({
        target: { tabId: tabId },
        func: clickShop,
        args: [name],
      });
      if (!clicked || !clicked.result) {
        job.detail = "没点开" + name;
        continue;
      }
      const entered = await waitForLeave(tabId);
      if (!entered) {
        job.detail = "没点开" + name;
        continue;
      }
      if (entered.captcha) {
        job = { state: "done", detail: "出现验证码了，请你自己通过后再点获取凭证" };
        return job.detail;
      }
      await sleep(1000);
      const page = await inspect(tabId);
      try {
        await upload(apiBase, token, page, name);
      } catch (err) {
        job = { state: "done", detail: "上传失败 " + (err && err.message ? err.message : "") };
        return job.detail;
      }
      saved += 1;
      job.detail = "已获取 " + saved + "/" + names.length;
      if (index === names.length - 1) break;
      await chrome.scripting.executeScript({ target: { tabId: tabId }, func: pageBack });
      const back = await waitForPicker(tabId);
      if (!back) {
        job = { state: "done", detail: "已获取 " + saved + " 家，请回到选择店铺后再获取剩下的" };
        return job.detail;
      }
    }
    job = { state: "done", detail: "已获取 " + saved + " 家店铺" };
    return job.detail;
  } catch (err) {
    job = { state: "done", detail: "获取失败" };
    return job.detail;
  }
}

chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (message.type === "status") {
    sendResponse({ state: job.state, detail: job.detail });
    return false;
  }
  if (message.type !== "capture") return false;
  captureAll(message.apiBase, message.token).then(function (detail) {
    sendResponse({ detail: detail });
  });
  return true;
});
