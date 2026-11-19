// 상단바: 게임 상태 + 레이드 시간 + 타르코프 게임 속 시각
// - 서버의 /api/state 를 2초마다 물어봄 (서버가 게임 로그를 지켜보고 있음)
// - 시계는 1초마다 다시 계산

const topbar = {
  state: { status: "off" },
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
const STATE_LABEL = { off: "stateOff", lobby: "stateLobby", loading: "stateLoading", raid: "stateRaid" };

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
  const pill = topbar.get("raid-state");
  pill.dataset.status = s.status;
  // 서버의 상태 값(코드) → 화면 글자
  const label = t(`topbar.${STATE_LABEL[s.status] || "stateOff"}`);
  topbar.get("raid-state-text").textContent = s.map_name ? `${label} · ${s.map_name}` : label;

  // 레이드 시간은 레이드 중일 때만
  const inRaid = s.status === "raid" && s.since;
  const time = topbar.get("raid-time");
  const bar = topbar.get("raid-bar");
  time.hidden = !inRaid;
  bar.hidden = !(inRaid && s.raid_minutes);
  if (inRaid) {
    const elapsed = (now - s.since) / 1000;
    topbar.get("raid-elapsed").textContent = formatDuration(elapsed);
    if (s.raid_minutes) {
      const total = s.raid_minutes * 60;
      const left = total - elapsed;
      const low = left < 10 * 60; // 10분 안 남으면 빨강
      topbar.get("raid-left").textContent = formatDuration(left);
      time.classList.toggle("low", low);
      bar.classList.toggle("low", low);
      topbar.get("raid-bar-fill").style.width = `${Math.max(0, Math.min(1, left / total)) * 100}%`;
    } else {
      topbar.get("raid-left").textContent = "?"; // 맵 정보에 제한 시간이 없을 때
    }
  }
  // 고정된 높이를 알려줘서, 카드로 이동할 때 상단바 밑에 숨지 않게
  document.documentElement.style.setProperty("--sticky-h", `${topbar.get("sticky-top").offsetHeight + 8}px`);
}

async function pollState() {
  try {
    const res = await fetch("/api/state");
    if (res.ok) {
      const next = await res.json();
      const changed = next.status !== topbar.state.status || next.map_id !== topbar.state.map_id;
      const questLog = next.quest_rev !== topbar.state.quest_rev; // 퀘스트 알림 로그가 늘어남
      topbar.state = next;
      if (questLog) window.dispatchEvent(new CustomEvent("quest-log-changed", { detail: next }));
      // 다른 화면(나중에: 맵에 들어가면 자동 전환)이 쓸 수 있게 알림
      if (changed) window.dispatchEvent(new CustomEvent("game-state", { detail: next }));
    }
  } catch (error) {
    // 서버가 잠깐 꺼져 있어도 조용히 다음 번에 다시
  }
  drawTopbar();
}

pollState();
setInterval(pollState, 2000);
setInterval(drawTopbar, 1000);
