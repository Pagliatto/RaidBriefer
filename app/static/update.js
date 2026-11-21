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
  dismissed: null,
  starting: false,
};
const UPDATE_LATER_KEY = "raidbriefer.updateLater";

function updateInRaid(state = null) {
  const current = state || (typeof topbar !== "undefined" ? topbar.state : null);
  return ["loading", "raid"].includes((current || {}).status);
}

function laterVersion() {
  try { return localStorage.getItem(UPDATE_LATER_KEY); } catch (e) { return null; }
}

function updateLaterChosen(version) {
  return updateUI.dismissed === version || laterVersion() === version;
}

function drawUpdateDot() {
  const s = updateUI.info;
  const unseen = s && s.available && !updateLaterChosen(s.latest) && !updateUI.starting &&
    !["downloading", "installing"].includes(s.status);
  const button = document.getElementById("settings-open");
  let dot = button.querySelector(".update-available-dot");
  if (unseen && !dot) {
    dot = el("span", "update-available-dot");
    dot.setAttribute("role", "img");
    dot.setAttribute("aria-label", t("update.unseenAvailable"));
    dot.title = t("update.unseenAvailable");
    button.append(dot);
  } else if (!unseen && dot) dot.remove();
}

function showUpdateBanner(show) {
  const banner = updateUI.banner;
  if (show) {
    if (!banner.hidden && !isWindowClosing(banner)) return;
    banner.hidden = false;
    animateOpen(banner, "banner");
  } else if (!banner.hidden && !isWindowClosing(banner)) {
    if (banner.contains(document.activeElement)) document.getElementById("settings-open").focus();
    animateClose(banner, () => { banner.hidden = true; }, "banner");
  }
}

function drawUpdate() {
  drawUpdateDot();
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
  const show = s && (busy || s.status === "failed" || (s.available && !updateLaterChosen(s.latest))) && !updateInRaid();
  showUpdateBanner(Boolean(show));
  if (!show) return;
  updateUI.notes.href = s.page;
  // 같은 상태를 다시 조회해도 원호를 새로 만들지 않는다.
  const mark = document.getElementById("update-state-icon");
  if (mark.dataset.status !== s.status) {
    mark.dataset.status = s.status;
    mark.replaceChildren(s.status === "downloading" ? el("i", "data-update-arc")
      : icon(s.status === "failed" ? "triangle-alert" : "package", 16));
  }
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

let updatePollPromise = null;
let updatePollTimer = null;
let updatePollSequence = 0;
let updatePollAttempts = 0;
function loadUpdate() {
  if (updatePollPromise) return updatePollPromise;
  clearTimeout(updatePollTimer);
  const sequence = ++updatePollSequence;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  updatePollPromise = (async () => {
    try {
      const res = await fetch("/api/update", { signal: controller.signal });
      if (!res.ok) return;
      const info = await res.json();
      if (sequence !== updatePollSequence || controller.signal.aborted) return;
      updateUI.info = info;
      updateUI.starting = false;
      drawUpdate();
    } catch (e) { /* 다음 예약에서 다시 확인한다. */ }
  })().finally(() => {
    clearTimeout(timeout);
    updatePollPromise = null;
    const busy = ["downloading", "installing"].includes(updateUI.info?.status);
    if (++updatePollAttempts < 240 && (busy || !updateUI.info?.check_finished)) {
      updatePollTimer = setTimeout(loadUpdate, busy ? 1500 : 3000);
    }
  });
  return updatePollPromise;
}

updateUI.apply.addEventListener("click", async () => {
  const s = updateUI.info;
  if (!s) return;
  if (!s.can_apply || s.status === "failed") {
    window.open(s.page, "_blank", "noopener");
    return;
  }
  updatePollSequence++;
  updateUI.apply.disabled = true;
  updateUI.starting = true;
  drawUpdateDot();
  try {
    const res = await fetch("/api/update/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    });
    updateUI.info = await res.json();
    if (!res.ok || updateUI.info.status === "failed") updateUI.starting = false;
  } catch (e) {
    // 업데이트가 시작되면 프로그램이 곧 꺼지므로 연결이 끊길 수 있음
  }
  drawUpdate();
  // 받는 동안 진행 상태를 계속 보여 줌 (끝나면 프로그램이 닫힘)
  updatePollAttempts = 0;
  loadUpdate();
});

updateUI.later.addEventListener("click", () => {
  updateUI.dismissed = updateUI.info.latest;
  try { localStorage.setItem(UPDATE_LATER_KEY, updateUI.info.latest); } catch (e) { /* 저장 못 해도 이번엔 숨김 */ }
  showUpdateBanner(false);
  drawUpdateDot();
});

// 레이드에 들어가면 숨기고, 나오면 다시
window.addEventListener("game-state", drawUpdate);

// 서버가 깃허브에 묻는 데 몇 초 걸려서, 처음에는 몇 번 다시 확인
setTimeout(loadUpdate, 1500);

// 처음 설정 확인이 끝난 뒤 현재 버전 안내를 읽는다.
const whatsNewUI = {
  dialog: document.getElementById("whats-new"),
  info: null,
  pending: false,
  requested: false,
  currentOnly: false,
  checking: false,
  seen: null,
  unsaved: null,
  saving: false,
};

async function saveWhatsNewSeen() {
  if (!whatsNewUI.unsaved || whatsNewUI.saving) return;
  whatsNewUI.saving = true;
  const version = whatsNewUI.unsaved;
  try {
    const res = await fetch("/api/whats-new/seen", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version }),
    });
    if (res.ok && whatsNewUI.unsaved === version) whatsNewUI.unsaved = null;
  } catch (e) { /* 연결이 돌아오면 다시 저장한다. */ }
  finally { whatsNewUI.saving = false; }
}

async function loadWhatsNew() {
  try {
    const res = await fetch("/api/whats-new");
    if (!res.ok) return;
    const info = await res.json();
    whatsNewUI.info = info;
    renderUpdateHistory(info.history || []);
    whatsNewUI.pending = !!(info.show && whatsNewUI.seen !== info.version);
    tryOpenWhatsNew();
  } catch (e) { /* 다음 설정 확인 때 다시 읽는다. */ }
}

async function tryOpenWhatsNew() {
  const ui = whatsNewUI;
  if (!ui.info || (!ui.pending && !ui.requested) || ui.checking || ui.dialog.open) return;
  if (document.body.classList.contains("in-setup")) return;
  // 첫 상단바 응답 전의 기본값(off)으로 레이드 여부를 판단하지 않는다.
  ui.checking = true;
  try {
    if (!await pollState() || updateInRaid()) return;
    if (document.body.classList.contains("in-setup")) return;
    const settingsDialog = document.getElementById("settings");
    if (ui.requested && settingsDialog.open) closeAnimatedDialog(settingsDialog);
    if (document.querySelector("dialog[open]") || !document.getElementById("brightness-popup").hidden) return;
    const versions = (ui.currentOnly ? [ui.info.notes] : ui.info.versions || [])
      .filter((notes) => notes && Array.isArray(notes.lines) && notes.lines.length);
    if (!versions.length) return;
    document.getElementById("whats-new-title").textContent = t("whatsNew.title", { version: ui.info.version });
    const makeList = (lines) => {
      const list = el("ul", "whats-new-list");
      list.append(...lines.map((line) => {
        const row = el("li");
        const dot = el("i", "whats-new-dot");
        dot.setAttribute("aria-hidden", "true");
        row.append(dot, el("span", "", line));
        return row;
      }));
      return list;
    };
    const content = document.getElementById("whats-new-list");
    content.replaceChildren(makeList(versions[0].lines));
    if (versions.length > 1) {
      const earlier = el("details", "whats-new-earlier");
      earlier.append(el("summary", "", t("whatsNew.earlier", { n: versions.length - 1 })));
      for (const notes of versions.slice(1)) {
        earlier.append(el("h3", "", "v" + notes.version), makeList(notes.lines));
      }
      content.append(earlier);
    }
    content.scrollTop = 0;
    // 같은 동작이 여러 버전에 있어도 버튼은 한 번만 보여 준다.
    const actions = new Set(versions.map((notes) => notes.action));
    document.getElementById("whats-new-action").hidden = !actions.has("brightness");
    ui.dialog.returnValue = "";
    openAnimatedDialog(ui.dialog);
    document.getElementById("whats-new-ok").focus();
  } catch (e) { /* 게임 상태를 확인하지 못하면 안내도 다음에 띄운다. */ }
  finally { ui.checking = false; }
}

// 설정 → 정보 탭의 '업데이트 기록': 지금 버전은 펼치고 나머지는 접음 (2026-11-21 사용자)
function renderUpdateHistory(history) {
  const box = document.getElementById("update-history");
  if (!box) return;
  box.replaceChildren();
  history.forEach((entry, index) => {
    if (!entry || !Array.isArray(entry.lines) || !entry.lines.length) return;
    const item = el("details", "update-history-item");
    item.open = index === 0;
    const summary = el("summary");
    const name = el("span", "update-history-version", "v" + entry.version);
    if (entry.version === whatsNewUI.info?.version) name.append(el("span", "update-history-current", t("settings.historyCurrent")));
    summary.append(name);
    if (entry.date) summary.append(el("span", "update-history-date", entry.date));
    const list = el("ul");
    entry.lines.forEach((line) => list.append(el("li", "", line)));
    item.append(summary, list);
    box.append(item);
  });
}
document.getElementById("whats-new-ok").addEventListener("click", () => closeAnimatedDialog(whatsNewUI.dialog));
document.getElementById("whats-new-action").addEventListener("click", () => closeAnimatedDialog(whatsNewUI.dialog, "brightness"));
bindAnimatedDialog(whatsNewUI.dialog);
whatsNewUI.dialog.addEventListener("window-closed", (event) => {
  const ui = whatsNewUI;
  if (event.detail === "deferred") return;
  ui.pending = false;
  ui.requested = false;
  ui.currentOnly = false;
  ui.seen = ui.info.version;
  ui.unsaved = ui.info.version;
  saveWhatsNewSeen();
  if (event.detail === "brightness") window.openBrightnessPopup();
});
window.addEventListener("game-state", () => {
  if (updateInRaid() && whatsNewUI.dialog.open) {
    // 레이드 때문에 숨긴 것은 읽은 것으로 저장하지 않는다.
    whatsNewUI.requested = true;
    closeAnimatedDialog(whatsNewUI.dialog, "deferred");
  } else tryOpenWhatsNew();
});
window.addEventListener("settings-changed", loadWhatsNew);
setInterval(() => { saveWhatsNewSeen(); tryOpenWhatsNew(); }, 5000);
