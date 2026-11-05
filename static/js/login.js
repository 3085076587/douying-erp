(function () {
    "use strict";

    var API_BASE = "/api/v1";
    var TOKEN_KEY = "erp_access_token";
    var NAME_KEY = "erp_display_name";
    var REMEMBER_KEY = "erp_remember_username";

    if (localStorage.getItem(TOKEN_KEY)) location.replace("/dashboard");

    var form = document.getElementById("login-form");
    var usernameInput = document.getElementById("username");
    var passwordInput = document.getElementById("password");
    var usernameError = document.getElementById("username-error");
    var passwordError = document.getElementById("password-error");
    var submitBtn = document.getElementById("submit-btn");
    var rememberBox = document.getElementById("remember");
    var toast = document.getElementById("toast");

    function showToast(message) {
        toast.textContent = message;
        toast.className = "toast error";
        clearTimeout(showToast._timer);
        showToast._timer = setTimeout(function () { toast.classList.add("hidden"); }, 3000);
    }

    function setFieldError(input, errorEl, message) {
        input.classList.toggle("error", !!message);
        errorEl.textContent = message || "";
    }

    var saved = localStorage.getItem(REMEMBER_KEY);
    if (saved) {
        usernameInput.value = saved;
        rememberBox.checked = true;
    }

    document.getElementById("toggle-pwd").addEventListener("click", function () {
        passwordInput.type = passwordInput.type === "password" ? "text" : "password";
    });

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        setFieldError(usernameInput, usernameError, "");
        setFieldError(passwordInput, passwordError, "");
        var username = usernameInput.value.trim();
        var ok = true;
        if (!username) {
            setFieldError(usernameInput, usernameError, "请输入账号");
            ok = false;
        }
        if (!passwordInput.value) {
            setFieldError(passwordInput, passwordError, "请输入密码");
            ok = false;
        }
        if (!ok) return;

        submitBtn.disabled = true;
        submitBtn.textContent = "登录中";
        fetch(API_BASE + "/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: username, password: passwordInput.value })
        }).then(function (resp) {
            return resp.json().then(function (data) { return { ok: resp.ok, data: data }; });
        }).then(function (result) {
            if (!result.ok) {
                var detail = result.data && result.data.detail;
                if (Array.isArray(detail)) detail = detail[0].msg;
                throw new Error(detail || "登录失败");
            }
            if (rememberBox.checked) localStorage.setItem(REMEMBER_KEY, username);
            else localStorage.removeItem(REMEMBER_KEY);
            localStorage.setItem(TOKEN_KEY, result.data.access_token);
            localStorage.setItem(NAME_KEY, (result.data.user && result.data.user.display_name) || username);
            location.replace("/dashboard");
        }).catch(function (err) {
            showToast(err.message);
            submitBtn.disabled = false;
            submitBtn.textContent = "登 录";
        });
    });
})();
