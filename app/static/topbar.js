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

// 글꼴 준비 후 한 번 측정. 지도 목록이 늦으면 도착 시 한 번만 보완하고 고정한다.
async function initStatePillWidth() {
  await document.fonts.ready;
  const pill = topbar.get("raid-state");
  const style = getComputedStyle(pill);
  const context = document.createElement("canvas").getContext("2d");
  if (!context) return;
  context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  const labels = Object.values(STATE_LABEL).map((key) => t(`topbar.${key}`));
  const mapNames = () => typeof mapUI === "undefined" ? [] : mapUI.maps.map((map) => map.name);
  const measure = (names) => {
    const widest = Math.max(...labels.concat(names).map((text) => context.measureText(text).width));
    const extra = 18 + parseFloat(style.columnGap) + parseFloat(style.paddingLeft)
      + parseFloat(style.paddingRight) + parseFloat(style.borderLeftWidth) + parseFloat(style.borderRightWidth);
    const width = Math.max(120, Math.min(200, Math.ceil((widest + extra) / 4) * 4));
    pill.style.setProperty("--state-pill-w", `${width}px`);
  };
  const names = mapNames();
  measure(names);
  if (names.length) return;
  // map.js가 이미 불러오는 목록을 재사용하며 추가 요청이나 상태별 재측정은 하지 않는다.
  const select = topbar.get("map-select");
  if (!select) return;
  const observer = new MutationObserver(() => {
    const arrived = mapNames();
    if (!arrived.length) return;
    measure(arrived);
    observer.disconnect();
  });
  observer.observe(select, { childList: true });
}

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
  const status = s.status === "lobby" && now < topbar.endedUntil ? "ended" : s.status;
  if (pill.dataset.status !== status) pill.dataset.status = status;
  // 서버의 상태 값(코드) → 화면 글자
  const label = t(`topbar.${STATE_LABEL[status] || "stateOff"}`);
  const showMap = s.map_name && (status === "loading" || status === "raid");
  topbar.get("raid-state-text").textContent = showMap ? s.map_name : label;
  pill.title = showMap ? `${label} · ${s.map_name}` : label;

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

async function pollState() {
  try {
    const res = await fetch("/api/state");
    if (res.ok) {
      const next = await res.json();
      const changed = next.status !== topbar.state.status || next.map_id !== topbar.state.map_id;
      const questLog = next.quest_rev !== topbar.state.quest_rev; // 퀘스트 알림 로그가 늘어남
      if (next.status !== "lobby" || next.cancelled) {
        topbar.endedUntil = 0;
      } else if (topbar.state.status === "raid") {
        topbar.endedUntil = Date.now() + TOPBAR_ENDED_MS;
      }
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

initStatePillWidth();
pollState();
setInterval(pollState, 2000);
setInterval(drawTopbar, 1000);
