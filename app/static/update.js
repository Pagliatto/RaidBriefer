// 새 버전 안내 + 자동 업데이트 (서버 app/updater.py)
// - 켤 때 서버가 깃허브에 최신 버전을 물어봄 → 여기서 몇 번 확인해서 새 버전이면 탭 아래에 안내
// - 레이드 중(로딩 포함)에는 숨김, [나중에]를 누르면 그 버전은 다시 안 띄움 (이 기기에 기억)
// - exe 로 실행 중이면 [지금 업데이트] = 받아서 바꾸고 다시 켬, 소스로 실행 중이면 받기 페이지 열기

const updateUI = {
  banner: document.getElementById("update-banner"),
  text: document.getElementById("update-text"),
  apply: document.getElementById("update-apply"),
  notes: document.getElementById("update-notes"),
  later: document.getElementById("update-later"),
  info: null,
};
const UPDATE_LATER_KEY = "raidbriefer.updateLater";

function updateInRaid() {
  return typeof topbar !== "undefined" && ["loading", "raid"].includes((topbar.state || {}).status);
}

function laterVersion() {
  try { return localStorage.getItem(UPDATE_LATER_KEY); } catch (e) { return null; }
}

function drawUpdate() {
  const s = updateUI.info;
  // 설정 창의 '버전' 칸
  const cur = document.getElementById("update-current");
  const status = document.getElementById("update-status");
  if (s) {
    cur.textContent = t("update.settingsCurrent", { current: s.current });
    status.textContent = !s.latest ? t("update.settingsUnknown")
      : s.available ? t("update.settingsNew", { latest: s.latest }) : t("update.settingsLatest");
  }
  // 탭 아래 안내
  const busy = s && (s.status === "downloading" || s.status === "installing");
  const show = s && (busy || s.status === "failed" || (s.available && laterVersion() !== s.latest)) && !updateInRaid();
  updateUI.banner.hidden = !show;
  if (!show) return;
  updateUI.notes.href = s.page;
  if (s.status === "failed") {
    updateUI.text.textContent = t("update.failed");
    updateUI.apply.textContent = t("update.openPage");
    updateUI.apply.disabled = false;
  } else if (busy) {
    updateUI.text.textContent = t(s.status === "installing" ? "update.installing" : "update.downloading");
    updateUI.apply.disabled = true;
  } else {
    updateUI.text.textContent = t("update.available", { latest: `v${s.latest}`, current: `v${s.current}` });
    updateUI.apply.textContent = s.can_apply ? t("update.apply") : t("update.openPage");
    updateUI.apply.disabled = false;
  }
  updateUI.later.hidden = busy;
}

async function loadUpdate() {
  try {
    updateUI.info = await (await fetch("/api/update")).json();
  } catch (e) {
    return; // 서버가 잠깐 안 될 때는 다음에
  }
  drawUpdate();
}

updateUI.apply.addEventListener("click", async () => {
  const s = updateUI.info;
  if (!s) return;
  if (!s.can_apply || s.status === "failed") {
    window.open(s.page, "_blank", "noopener");
    return;
  }
  updateUI.apply.disabled = true;
  try {
    const res = await fetch("/api/update/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    });
    updateUI.info = await res.json();
  } catch (e) {
    // 업데이트가 시작되면 프로그램이 곧 꺼지므로 연결이 끊길 수 있음
  }
  drawUpdate();
  // 받는 동안 진행 상태를 계속 보여 줌 (끝나면 프로그램이 닫힘)
  const timer = setInterval(async () => {
    await loadUpdate();
    if (!updateUI.info || updateUI.info.status === "failed") clearInterval(timer);
  }, 1500);
});

updateUI.later.addEventListener("click", () => {
  try { localStorage.setItem(UPDATE_LATER_KEY, updateUI.info.latest); } catch (e) { /* 저장 못 해도 이번엔 숨김 */ }
  updateUI.banner.hidden = true;
});

// 레이드에 들어가면 숨기고, 나오면 다시
window.addEventListener("game-state", drawUpdate);

// 서버가 깃허브에 묻는 데 몇 초 걸려서, 처음에는 몇 번 다시 확인
[1500, 5000, 12000].forEach((ms) => setTimeout(loadUpdate, ms));
