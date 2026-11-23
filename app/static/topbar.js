// 상단바: 게임 상태 + 레이드 시간 + 타르코프 게임 속 시각
// - 서버의 /api/state 를 2초마다 물어봄 (서버가 게임 로그를 지켜보고 있음)
// - 시계는 1초마다 다시 계산

const topbar = {
  state: { status: "off" },
  endedUntil: 0,
  get: (id) => document.getElementById(id),
};

// 초 → "12:34" 또는 "1:02:03"
function formatDuration(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

// 타르코프 시각: 게임 속 시간은 현실보다 7배 빠르고, 러시아 시간(UTC+3) 기준.
// 레이드를 고를 때 보이는 두 시간대는 서로 12시간 차이.
const TARKOV_DAY_MS = 24 * 60 * 60 * 1000;
function tarkovTime(nowMs, extraHours) {
  const ms = (3 * 3600 * 1000 + nowMs * 7 + extraHours * 3600 * 1000) % TARKOV_DAY_MS;
  const d = new Date(ms);
  const h = d.getUTCHours();
  return {
    text: `${String(h).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`,
    night: h < 6 || h >= 21, // 대략 밤 시간
  };
}

// 서버가 보내는 게임 상태 값 → 사전 이름표 (값 자체는 서버와의 약속이라 그대로)
const STATE_LABEL = { off: "stateOff", lobby: "stateLobby", loading: "stateLoading", raid: "stateRaid", ended: "stateEnded" };
const TOPBAR_ENDED_MS = 30000;

// 시각 하나를 알약 칸에: 낮 ☀ 노랑 / 밤 ☾ 파랑
function drawClock(id, time) {
  const box = topbar.get(id);
  box.className = `clock ${time.night ? "night" : "day"}`;
  box.title = time.night ? t("topbar.clockNight") : t("topbar.clockDay");
  const sym = el("i");
  sym.innerHTML = iconSvg(time.night ? "moon" : "sun", 15); // 낮·밤 그림 (기기마다 같게)
  box.replaceChildren(sym, el("b", "", time.text));
}

function drawTopbar() {
  const now = Date.now();
  drawClock("clock-a", tarkovTime(now, 0));
  drawClock("clock-b", tarkovTime(now, 12));

  const s = topbar.state;
  topbar.get("data-footer").classList.toggle("data-is-updating", !!s.data_updating);
  topbar.get("data-updating").hidden = !s.data_updating;
  const pill = topbar.get("raid-state");
  const status = s.status === "lobby" && now < topbar.endedUntil ? "ended" : s.status;
  if (pill.dataset.status !== status) pill.dataset.status = status;
  // 서버의 상태 값(코드) → 화면 글자
  const label = t(`topbar.${STATE_LABEL[status] || "stateOff"}`);
  const showMap = s.map_name && (status === "loading" || status === "raid");
  topbar.get("raid-state-text").textContent = showMap ? s.map_name : label;
  pill.title = showMap ? `${label} · ${s.map_name}` : label;
  // 게임 로그를 못 읽는 중: 홈 안내 + 코드 (게임이 켜져 있을 때만 서버가 보냄). 상단바는 알림함 배지로 (2026-11-23 사용자)
  const problem = s.log_problem === "unreadable" || s.log_problem === "no_map" ? s.log_problem : null;
  const banner = document.getElementById("log-banner");
  if (banner && banner.hidden !== !problem) banner.hidden = !problem;
  if (problem) {
    document.getElementById("log-banner-text").textContent = t(problem === "no_map" ? "home.logNoMap" : "home.logUnreadable");
    document.getElementById("log-banner-code").textContent = problem === "no_map" ? "LOG-02" : "LOG-01"; // i18n-ok 코드
  }
  if (typeof drawAlerts === "function") drawAlerts(s);

  // 레이드 밖에서도 같은 칸을 유지하고 시간만 대기 표시로 바꿈
  const inRaid = s.status === "raid" && s.since;
  const time = topbar.get("raid-time");
  const bar = topbar.get("raid-bar");
  time.classList.toggle("idle", !inRaid);
  let urgency = 0;
  bar.hidden = !(inRaid && s.raid_minutes);
  if (inRaid) {
    const elapsed = (now - s.since) / 1000;
    time.title = `${t("topbar.elapsed")} ${formatDuration(elapsed)}`;
    if (s.raid_minutes) {
      const total = s.raid_minutes * 60;
      const left = total - elapsed;
      urgency = left < 2 * 60 ? 3 : left <= 5 * 60 ? 2 : left <= 10 * 60 ? 1 : 0;
      topbar.get("raid-left").textContent = formatDuration(left);
      topbar.get("raid-bar-fill").style.width = `${Math.max(0, Math.min(1, left / total)) * 100}%`;
    } else {
      topbar.get("raid-left").textContent = "?"; // 맵 정보에 제한 시간이 없을 때
    }
  } else {
    time.removeAttribute("title");
    topbar.get("raid-left").textContent = "-- : --";
  }
  // 같은 상태에서는 클래스를 유지해 호흡 애니메이션이 매초 다시 시작하지 않음.
  if (time.dataset.urgency !== String(urgency)) {
    time.dataset.urgency = String(urgency);
    time.classList.toggle("low", urgency > 0);
    time.classList.toggle("low-2", urgency === 2);
    time.classList.toggle("low-3", urgency === 3);
    bar.classList.toggle("low", urgency > 0);
  }
  // 고정된 높이를 알려줘서, 카드로 이동할 때 상단바 밑에 숨지 않게
  document.documentElement.style.setProperty("--sticky-h", `${topbar.get("sticky-top").offsetHeight + 8}px`);
}

// 첫 기록 읽기 안내: 서버가 확인한 경과 시간만 사용해 끝난 작업이 뒤늦게 뜨지 않게 한다.
const logReadingNotice = (() => {
  const box = document.getElementById("log-reading");
  const count = document.getElementById("log-reading-count");
  const fill = document.getElementById("log-reading-fill");
  let progress = null;
  let raidTransition = false;
  let shownAt = 0;
  let closeTimer = null;

  function render() {
    clearTimeout(closeTimer);
    closeTimer = null;
    // 처음 설정 중에는 덮지 않는다: 설정 2단계 안에서 진행률을 보여 줌 (setup.js, 2026-11-21 사용자)
    if (raidTransition || document.body.classList.contains("in-setup")) {
      cancelWindowMotion(box);
      box.hidden = true;
      return;
    }
    if (progress) {
      // 전체 개수를 아직 모르면 "0 / 0" 대신 숫자를 숨기고 움직이는 막대만 보여 준다
      count.hidden = !(progress.total > 0);
      if (progress.total > 0) count.textContent = t("logReading.folders", { done: progress.done, total: progress.total });
      fill.classList.toggle("determinate", progress.total > 0);
      fill.style.transform = progress.total > 0
        ? "scaleX(" + Math.max(0, Math.min(1, progress.done / progress.total)) + ")" : "";
      if (isWindowClosing(box)) cancelWindowMotion(box);
      // 700ms 이하는 표시하지 않고, 다음 서버 응답에서 여전히 읽는 중인지 확인한다.
      if (box.hidden && progress.elapsed_ms > 700) {
        shownAt = performance.now();
        box.hidden = false;
        animateOpen(box, "notice", 200);
      }
    } else if (!box.hidden) {
      const wait = Math.max(0, 600 - (performance.now() - shownAt));
      closeTimer = setTimeout(() => {
        closeTimer = null;
        if (!progress && !raidTransition) animateClose(box, () => { box.hidden = true; }, "notice");
      }, wait);
    }
  }

  // 설정·상태·탭을 계속 사용할 수 있도록 실제 고정 영역의 높이를 따른다.
  const sticky = document.getElementById("sticky-top");
  const resize = () => box.style.setProperty("--log-reading-top", sticky.getBoundingClientRect().bottom + "px");
  new ResizeObserver(resize).observe(sticky);
  resize();
  window.addEventListener("raid-transition", (event) => {
    raidTransition = event.detail;
    render();
  });
  return {
    update(next) {
      progress = next || null;
      render();
      window.dispatchEvent(new CustomEvent("log-reading", { detail: progress }));
    },
  };
})();

let statePollPromise = null;
let statePollTimer = null;
let statePollSequence = 0;
function pollState() {
  if (statePollPromise) return statePollPromise;
  clearTimeout(statePollTimer);
  const sequence = ++statePollSequence;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  statePollPromise = (async () => {
    try {
      const res = await fetch("/api/state", { signal: controller.signal });
      if (res.ok) {
        const next = await res.json();
        if (sequence !== statePollSequence || controller.signal.aborted) return false;
        const changed = next.status !== topbar.state.status || next.map_id !== topbar.state.map_id ||
          next.since !== topbar.state.since || !!next.cancelled !== !!topbar.state.cancelled;
        const questLog = next.quest_rev !== topbar.state.quest_rev; // 퀘스트 알림 로그가 늘어남
        if (next.status !== "lobby" || next.cancelled) {
          topbar.endedUntil = 0;
        } else if (topbar.state.status === "raid") {
          topbar.endedUntil = Date.now() + TOPBAR_ENDED_MS;
        }
        topbar.state = next;
        logReadingNotice.update(next.log_reading);
        if (questLog) window.dispatchEvent(new CustomEvent("quest-log-changed", { detail: next }));
        // 다른 화면(나중에: 맵에 들어가면 자동 전환)이 쓸 수 있게 알림
        if (changed) window.dispatchEvent(new CustomEvent("game-state", { detail: next }));
        return true;
      }
    } catch (error) {
      // 서버가 잠깐 꺼져 있어도 조용히 다음 번에 다시
    }
    return false;
  })().finally(() => {
    clearTimeout(timeout);
    statePollPromise = null;
    drawTopbar();
    statePollTimer = setTimeout(pollState, topbar.state.log_reading ? 250 : 2000);
  });
  return statePollPromise;
}

pollState();
setInterval(drawTopbar, 1000);
