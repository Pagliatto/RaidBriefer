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

// 언어 고르기 (설정 창 + 처음 설정 1단계). 바꾸면 화면을 새로 불러와서 모든 글자를 그 언어로
async function renderLangPickers() {
  let info;
  try {
    info = await (await fetch("/api/settings")).json();
  } catch (error) {
    return;
  }
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
