// 설정 창: 오른쪽 위 ⚙ 버튼
// - 게임 설치 위치 보기 / 바꾸기 / 자동으로 찾기 (서버 /api/settings)

const settingsUI = {
  dialog: document.getElementById("settings"),
  input: document.getElementById("settings-game-dir"),
  result: document.getElementById("settings-result"),
  buttons: [document.getElementById("settings-save"), document.getElementById("settings-find")],
};

function showSettingsResult(text, ok, box = settingsUI.result) {
  const pending = ok === null;
  box.className = "settings-result " + (pending ? "pending" : ok ? "ok" : "bad");
  const mark = pending ? el("i", "data-update-arc") : icon(ok ? "check" : "triangle-alert", 14);
  mark.setAttribute("aria-hidden", "true");
  box.replaceChildren(mark, el("span", "", text));
}

// 정보 탭의 [최신 버전 확인]: 서버가 깃허브에 다시 물어봄 (연타는 서버가 1분 간격으로 막음)
// 결과 글자(최신·새 버전)는 바로 위 '버전' 칸이 보여 주므로 여기서는 확인했는지·실패했는지만 알림
const updateCheckUI = {
  button: document.getElementById("update-check"),
  result: document.getElementById("update-check-result"),
};
updateCheckUI.button.addEventListener("click", async () => {
  const { button, result } = updateCheckUI;
  button.disabled = true;
  showSettingsResult(t("update.checking"), null, result);
  try {
    const res = await fetch("/api/update/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await res.json();
    if (!res.ok) throw new Error();
    if (data.result === "busy") showSettingsResult(t("update.checkBusy"), null, result);
    else showSettingsResult(t(data.result === "ok" ? "update.checkDone" : "update.checkFailed"), data.result === "ok", result);
    await loadUpdate(); // 버전 칸·탭 아래 안내·⚙ 점을 새 결과로
  } catch (error) {
    showSettingsResult(t("update.checkFailed"), false, result);
  } finally {
    button.disabled = false;
  }
});

async function sendSettings(body) {
  settingsUI.buttons.forEach((b) => (b.disabled = true));
  showSettingsResult(t("setup.checking"), null);
  try {
    const res = await fetch("/api/settings", {
      method: body ? "POST" : "GET",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json();
    if (!res.ok) {
      showSettingsResult(data.error || t("settings.saveFailed"), false);
      return;
    }
    settingsUI.input.value = data.game_dir;
    showSettingsResult(body ? t("settings.saved", { message: data.check.message }) : data.check.message, data.check.ok);
    // 위치가 바뀌었으면 퀘스트를 새로 불러오게 알림
    if (body) window.dispatchEvent(new CustomEvent("settings-changed", { detail: data }));
  } catch (error) {
    showSettingsResult(t("settings.noServer"), false);
  } finally {
    settingsUI.buttons.forEach((b) => (b.disabled = false));
  }
}

// 설정 탭 (일반 / 기록 관리 / 정보): 고른 탭의 칸만 보이게
function showSettingsTab(name) {
  if (name === "records") loadShots();
  if (name === "about") {
    loadUpdate();
    updateCheckUI.result.replaceChildren(); // 지난번 확인 결과는 지우고 시작
  }
  document.querySelectorAll(".settings-tab").forEach((b) => b.classList.toggle("active", b.dataset.stab === name));
  document.querySelectorAll(".settings-item[data-stab]").forEach((s) => { s.hidden = s.dataset.stab !== name; });
}
document.querySelectorAll(".settings-tab").forEach((b) => b.addEventListener("click", () => showSettingsTab(b.dataset.stab)));

document.getElementById("settings-open").addEventListener("click", () => {
  showSettingsTab("general"); // 열 때는 항상 '일반'부터
  openAnimatedDialog(settingsUI.dialog);
  sendSettings(null); // 열 때마다 지금 값과 확인 결과를 새로 보여줌
});
document.getElementById("settings-save").addEventListener("click", () =>
  sendSettings({ game_dir: settingsUI.input.value }));
document.getElementById("settings-find").addEventListener("click", () => sendSettings({ find: true }));
settingsUI.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendSettings({ game_dir: settingsUI.input.value });
});
// Esc·닫기 버튼·바깥 클릭에 같은 닫기 처리를 사용한다.
bindAnimatedDialog(settingsUI.dialog);
// setup.js가 처음 설정 화면을 열기 전에 같은 닫기 효과를 시작한다.
document.getElementById("settings-setup").addEventListener("click", () => closeAnimatedDialog(settingsUI.dialog));

// 전체 초기화: 확인 창 → 모든 기록 지움 → 새로고침하면 처음 설정이 뜸
document.getElementById("settings-reset").addEventListener("click", async () => {
  const ok = window.confirm(t("settings.resetConfirm"));
  if (!ok) return;
  try {
    const res = await fetch("/api/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    });
    if (!res.ok) throw new Error();
    location.reload();
  } catch (error) {
    showSettingsResult(t("settings.resetFailed"), false);
  }
});

// 스크린샷 정리 (기록 관리 탭): 게임이 찍은 스크린샷만, 누를 때만, 휴지통으로
const shotsUI = {
  open: document.getElementById("settings-shots-open"),
  clean: document.getElementById("settings-shots-clean"),
  result: document.getElementById("settings-shots-result"),
  info: null, // 서버가 마지막으로 알려 준 {found, count, bytes, as_of}
};

function shotSize(bytes) {
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb >= 10 ? Math.round(mb) : mb.toFixed(1)} MB`;
}

function showShots(info) {
  shotsUI.info = info;
  shotsUI.open.disabled = !info.found;
  shotsUI.clean.disabled = !info.count;
  const text = !info.found ? t("settings.shotsNoFolder")
    : info.count ? t("settings.shotsCount", { count: info.count, size: shotSize(info.bytes) }) : t("settings.shotsNone");
  shotsUI.result.className = "settings-result";
  shotsUI.result.textContent = text;
}

async function loadShots() {
  shotsUI.clean.disabled = true;
  try {
    const res = await fetch("/api/screenshots");
    if (!res.ok) throw new Error();
    showShots(await res.json());
  } catch (error) {
    shotsUI.result.replaceChildren();
  }
}

shotsUI.open.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/screenshots/open-folder", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    if (!res.ok || !(await res.json()).ok) throw new Error();
  } catch (error) {
    showSettingsResult(t("settings.shotsOpenFailed"), false, shotsUI.result);
  }
});

shotsUI.clean.addEventListener("click", async () => {
  const info = shotsUI.info;
  if (!info || !info.count) return;
  if (!window.confirm(t("settings.shotsConfirm", { count: info.count, size: shotSize(info.bytes) }))) return;
  shotsUI.clean.disabled = true;
  try {
    const res = await fetch("/api/screenshots/clean", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true, as_of: info.as_of }),
    });
    if (!res.ok) throw new Error();
    const done = await res.json();
    await loadShots(); // 남은 개수로 버튼을 다시 맞춤
    const size = shotSize(done.bytes);
    if (done.reason === "busy") showSettingsResult(t("settings.shotsBusy"), false, shotsUI.result);
    else if (done.reason === "no_recycle" && !done.moved) showSettingsResult(t("settings.shotsNoRecycle"), false, shotsUI.result);
    else if (done.left) showSettingsResult(t("settings.shotsPartial", { count: done.moved, left: done.left }), false, shotsUI.result);
    else showSettingsResult(t("settings.shotsDone", { count: done.moved, size }), true, shotsUI.result);
  } catch (error) {
    showSettingsResult(t("settings.shotsFailed"), false, shotsUI.result);
    shotsUI.clean.disabled = !(shotsUI.info && shotsUI.info.count);
  }
});

// 언어 고르기 (설정 창 + 처음 설정 1단계). 바꾸면 화면을 새로 불러와서 모든 글자를 그 언어로
// 남은 시간 경고 테두리 (설정 창 일반 탭): 켬 / 끔 스위치. 값은 html data-raid-border 에 적어 CSS·상단바가 봄
function renderRaidBorderPicker(current) {
  const on = current !== "off"; // 모르는 값·옛 값(얇게·보통)은 켬
  document.documentElement.dataset.raidBorder = on ? "on" : "off";
  const box = document.getElementById("settings-raid-border");
  const toggle = el("label", "ho-switch settings-switch");
  const input = el("input");
  input.type = "checkbox";
  input.setAttribute("role", "switch");
  input.checked = on;
  input.setAttribute("aria-label", t("settings.raidBorderTitle"));
  input.addEventListener("change", async () => {
    input.disabled = true;
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ raid_border: input.checked ? "on" : "off" }),
      });
      if (!res.ok) throw new Error();
      renderRaidBorderPicker((await res.json()).raid_border);
    } catch (error) {
      renderRaidBorderPicker(on ? "on" : "off"); // 저장 못 했으면 스위치를 원래대로
      alert(t("common.saveFailedShort"));
    }
  });
  toggle.append(input, el("span", "ho-switch-track"), el("span", "ho-switch-text", t(on ? "settings.raidBorderOn" : "settings.raidBorderOff")));
  box.replaceChildren(toggle);
}

async function renderLangPickers() {
  let info;
  try {
    info = await (await fetch("/api/settings")).json();
  } catch (error) {
    renderRaidBorderPicker("on"); // 설정을 못 읽어도 경고는 기본값으로 동작
    return;
  }
  renderRaidBorderPicker(info.raid_border);
  ["settings-lang", "setup-lang"].forEach((id) => {
    const box = document.getElementById(id);
    if (!box) return;
    box.replaceChildren();
    (info.langs || []).forEach(([code, name]) => {
      const btn = el("button", code === info.lang ? "on" : "", name);
      btn.type = "button";
      btn.lang = code;
      btn.addEventListener("click", async () => {
        if (code === info.lang) return;
        try {
          const res = await fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lang: code }),
          });
          if (!res.ok) throw new Error();
          location.reload();
        } catch (error) {
          alert(t("common.saveFailedShort"));
        }
      });
      box.append(btn);
    });
  });
}
renderLangPickers();
