// 홈 화면 동작: 탭 바꾸기, 퀘스트 목록 그리기, 맵으로 거르기
const ALL_MAPS = "@any";        // 맵은 상관없지만 레이드에 들어가야 하는 퀘스트 (화면: 모든 맵)
const OUT_OF_RAID = "@out";     // 레이드에 안 들어가도 되는 퀘스트 (무기 개조 등, 화면: 레이드 밖)
const UNKNOWN_MAP = "@unknown"; // 아직 위치를 확인하지 못한 퀘스트
const NOT_A_MAP = [ALL_MAPS, OUT_OF_RAID, UNKNOWN_MAP];  // 실제 맵이 아닌 이름들
// 맵 이름 → 화면 글자 (서버가 보내는 특수 맵 코드는 사전에서)
function mapLabel(name) {
  if (name === ALL_MAPS) return t("quest.anyMap");
  if (name === OUT_OF_RAID) return t("quest.outOfRaid");
  if (name === UNKNOWN_MAP) return t("quest.unknownMap");
  return name;
}
const FILTER_ALL = "*";  // 맵 거르기에서 '전체'를 뜻하는 값 (화면 글자는 사전 quest.filterAll)

const state = {
  data: null,           // 서버에서 받은 퀘스트 정보
  handed: {},           // 제출 체크: { "퀘스트id|아이템": true }
  goals: {},            // 해낸 목표 체크: { "퀘스트id|목표id": true } (건네주기 목표는 제출 체크를 따라감)
  hidden: {},           // 안 할 퀘스트: { "퀘스트id": true }
  list: "in_progress",  // 진행 중 / 완료 / 실패 / 숨김
  map: FILTER_ALL,      // 맵 거르기 (FILTER_ALL = 전체)
};

// 직전 목록과 미확인 변경을 분리: 재시작·재렌더 후에도 배지와 1회 강조를 유지.
const QUEST_SEEN_KEY = "rb-quest-seen";
const QUEST_CHANGES_KEY = "rb-quest-changes";
const QUEST_CHANGE_LABEL = {
  in_progress: "quest.changeNew", completed: "quest.changeCompleted", failed: "quest.changeFailed",
};
function readQuestStorage(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key));
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch (err) { return null; }
}
let questBaseline = readQuestStorage(QUEST_SEEN_KEY);
if (questBaseline && Object.values(questBaseline).some((s) => !Object.hasOwn(QUEST_CHANGE_LABEL, s))) {
  questBaseline = null;
}
const questChanges = Object.create(null);
if (questBaseline) {
  Object.entries(readQuestStorage(QUEST_CHANGES_KEY) || {}).forEach(([id, change]) => {
    if (change && Object.hasOwn(QUEST_CHANGE_LABEL, change.status) && questBaseline[id] === change.status) {
      questChanges[id] = { status: change.status, flashed: change.flashed === true };
    }
  });
}
function saveQuestChanges() {
  try {
    localStorage.setItem(QUEST_SEEN_KEY, JSON.stringify(questBaseline));
    localStorage.setItem(QUEST_CHANGES_KEY, JSON.stringify(questChanges));
  } catch (err) { /* 저장소가 막혀도 이번 실행에서는 계속 동작 */ }
}
function trackQuestChanges(data) {
  const next = Object.create(null);
  Object.keys(QUEST_CHANGE_LABEL).forEach((status) => {
    data[status].forEach((q) => { next[q.id] = status; });
  });
  if (questBaseline) {
    Object.entries(next).forEach(([id, status]) => {
      if (questBaseline[id] !== status) questChanges[id] = { status, flashed: false };
    });
  }
  Object.keys(questChanges).forEach((id) => {
    if (questChanges[id].status !== next[id]) delete questChanges[id];
  });
  questBaseline = next;
  saveQuestChanges();
}
function updateQuestChangeDots() {
  const statuses = new Set(Object.values(questChanges).map((change) => change.status));
  const hiddenUnseen = Object.keys(questChanges).some((id) =>
    questChanges[id].status === "in_progress" && state.hidden[id]);
  document.querySelectorAll('.tab[data-tab="quest"], .subtab').forEach((button) => {
    const list = button.dataset.list;
    const unseen = !list ? statuses.size > 0 : list === "hidden" ? hiddenUnseen : statuses.has(list);
    let dot = button.querySelector(".quest-change-dot");
    if (unseen && !dot) {
      dot = el("span", "quest-change-dot");
      dot.setAttribute("role", "img");
      dot.setAttribute("aria-label", t("quest.unseenChanges"));
      dot.title = t("quest.unseenChanges");
      button.append(dot);
    } else if (!unseen && dot) dot.remove();
  });
  document.getElementById("quest-mark-seen").hidden = statuses.size === 0;
  document.getElementById("quest-mark-seen-count").textContent = Object.keys(questChanges).length;
}
function markQuestChangesSeen(entries) {
  // 낡은 카드의 호버 타이머가 새 상태를 지우지 않도록 같은 변경인지 확인.
  const seen = new Set();
  entries.forEach(([id, change]) => {
    if (questChanges[id] !== change) return;
    delete questChanges[id];
    seen.add(id);
  });
  if (!seen.size) return;
  saveQuestChanges();
  document.querySelectorAll("#quest-list .quest").forEach((card) => {
    if (!seen.has(card.dataset.questId)) return;
    const badge = card.querySelector(".quest-change-badge");
    if (badge) {
      badge.classList.add("seen");
      badge.setAttribute("aria-hidden", "true");
    }
    card.removeAttribute("tabindex");
  });
  updateQuestChangeDots();
}
document.getElementById("quest-mark-seen").addEventListener("click", () => {
  markQuestChangesSeen(Object.entries(questChanges));
});

// 숨긴 탭·화면 밖에서는 소비하지 않고 카드가 실제로 보일 때만 한 번 강조.
const questChangeObserver = new IntersectionObserver((entries) => {
  let changed = false;
  entries.forEach(({ target: card, isIntersecting }) => {
    if (!isIntersecting || !card.isConnected || document.hidden || document.getElementById("tab-quest").hidden) return;
    const change = questChanges[card.dataset.questId];
    if (!change || change.flashed) return;
    change.flashed = true;
    card.classList.add("quest-change-flash");
    // 애니메이션이 멈춰도 반짝임이 남지 않게 2.6초 뒤 무조건 지움
    setTimeout(() => card.classList.remove("quest-change-flash"), 2600);
    questChangeObserver.unobserve(card);
    changed = true;
  });
  if (changed) saveQuestChanges();
});
const questHoverTimers = new Map();
function clearQuestHover(card) {
  clearTimeout(questHoverTimers.get(card));
  questHoverTimers.delete(card);
}
function resetQuestHover() {
  questHoverTimers.forEach((timer) => clearTimeout(timer));
  questHoverTimers.clear();
}
function observeQuestChanges() {
  questChangeObserver.disconnect();
  if (document.hidden || document.getElementById("tab-quest").hidden) return;
  document.querySelectorAll("#quest-list .quest").forEach((card) => {
    const change = questChanges[card.dataset.questId];
    if (change && !change.flashed) questChangeObserver.observe(card);
  });
}
let questTabOpen = false;
document.addEventListener("tab-shown", (e) => {
  resetQuestHover();
  // 퀘스트 탭을 떠나면, 화면에 한 번 보였던(반짝인) 변화 표시는 확인한 것으로 침
  if (questTabOpen && e.detail !== "quest") {
    markQuestChangesSeen(Object.entries(questChanges).filter(([, change]) => change.flashed));
  }
  questTabOpen = e.detail === "quest";
  observeQuestChanges();
});
document.addEventListener("visibilitychange", () => { resetQuestHover(); observeQuestChanges(); });
window.addEventListener("blur", resetQuestHover);
function decorateQuestChange(card, name, id) {
  const change = questChanges[id];
  if (!change) return;
  card.dataset.change = change.status;
  card.tabIndex = 0;
  const badge = el("span", "quest-change-badge", t(QUEST_CHANGE_LABEL[change.status]));
  badge.title = t("quest.changeHint");
  name.append(badge);
  const dismiss = () => { clearQuestHover(card); markQuestChangesSeen([[id, change]]); };
  card.addEventListener("mouseenter", () => {
    clearQuestHover(card);
    questHoverTimers.set(card, setTimeout(() => {
      clearQuestHover(card);
      if (card.isConnected && !document.hidden && !document.getElementById("tab-quest").hidden) dismiss();
    }, 300)); // 목록을 훑기만 해도 지워지게 (스크롤하며 스친 정도는 남음)
  });
  card.addEventListener("mouseleave", () => clearQuestHover(card));
  // 하위 버튼이 먼저 목록을 다시 그려도 확인 처리는 잃지 않음.
  card.addEventListener("click", dismiss, true);
  card.addEventListener("focusin", dismiss);
}

// 진행 중인데 숨기지 않은 퀘스트 (홈 화면도 이것만 씀)
function activeQuests() {
  return state.data ? state.data.in_progress.filter((q) => !state.hidden[q.id]) : [];
}

// 맵은 상관없지만 레이드에서 해야 하는 퀘스트 = 어느 맵에서나 할 수 있는 것
function anywhereQuests(list) {
  return list.filter((q) => q.maps.includes(ALL_MAPS));
}

// 목록 종류별 퀘스트
function questsOf(list) {
  if (!state.data) return [];
  if (list === "in_progress") return activeQuests();
  if (list === "hidden") return state.data.in_progress.filter((q) => state.hidden[q.id]);
  return state.data[list];
}

// ---------- 큰 탭 (홈 / 퀘스트 / 스토리) ----------
// 화면만 바꿈 (기록은 안 남김)
function showTab(name) {
  const changed = currentTab() !== name;
  document.querySelectorAll(".tab").forEach((b) => {
    const on = b.dataset.tab === name;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on);
  });
  document.querySelectorAll("main > .panel").forEach((p) => {
    p.hidden = p.id !== `tab-${name}`;
    if (p.hidden) cancelWindowMotion(p);
  });
  // 지도 탭은 화면 전체를 지도로 씀 (스크롤 없음)
  document.body.classList.toggle("map-mode", name === "map");
  document.dispatchEvent(new CustomEvent("tab-shown", { detail: name }));
  if (changed) {
    const panel = document.getElementById(`tab-${name}`);
    // 지도 탭은 효과 없이 바로 (2026-11-21 사용자: 흐려졌다 선명해지는 게 점멸처럼 보임)
    if (name !== "map") animateOpen(panel, "rise", 150);
  }
}

// ---------- 뒤로 가기 (마우스 뒤로 버튼 / 브라우저 뒤로) ----------
// 화면을 옮길 때마다 브라우저 기록에 { 탭, 스크롤 위치 } 를 남겨 두고,
// 뒤로 가기를 누르면 그때의 탭과 스크롤 위치로 돌아간다.
function currentTab() {
  const on = document.querySelector(".tab.active");
  return on ? on.dataset.tab : "home";
}

// 지금 화면의 스크롤 위치를 지금 기록에 적어 둠 (돌아왔을 때 그 자리로)
function rememberScroll() {
  const extra = currentTab() === "story" && typeof storyFlow !== "undefined" ? { storyView: storyFlow.view } : {};
  history.replaceState({ ...(history.state || {}), tab: currentTab(), scroll: window.scrollY, ...extra }, "");
}

// 다른 화면으로 이동 (기록을 하나 남김)
function goTab(name) {
  rememberScroll();
  showTab(name);
  window.scrollTo(0, 0);
  history.pushState({ tab: name, scroll: 0 }, "");
}

window.addEventListener("popstate", (e) => {
  const s = e.state || { tab: "home", scroll: 0 };
  showTab(s.tab || "home");
  // 스토리 탭 안의 화면(챕터 진행·엔딩 분기)도 그때 것으로
  if (s.storyView && typeof setStoryView === "function") setStoryView(s.storyView);
  setTimeout(() => window.scrollTo(0, s.scroll || 0), 30);
});

// 처음 화면도 기록으로
history.replaceState({ tab: "home", scroll: 0 }, "");

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.dataset.tab !== currentTab()) goTab(btn.dataset.tab);
  });
});

// 다른 화면(홈)에 "퀘스트 정보가 바뀌었다"고 알리기
let firstDataShown = false;
function notifyChanged(name) {
  document.dispatchEvent(new Event(name));
  // dispatchEvent가 돌아오면 home.js의 동기 renderHome도 끝난 상태다.
  if (name === "quests-changed" && state.data && !firstDataShown && typeof renderHome === "function") {
    firstDataShown = true;
    performance.mark("raidbriefer:first-data-shown");
  }
}

// ---------- 진행 중 / 완료 / 실패 ----------
document.querySelectorAll(".subtab").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.list = btn.dataset.list;
    state.map = FILTER_ALL;
    document.querySelectorAll(".subtab").forEach((b) => b.classList.toggle("active", b === btn));
    render();
  });
});

// 글자를 안전하게 넣기 위한 작은 도우미 (HTML로 해석되지 않게)
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// 사용자 동작에서만 호출하며 재렌더링에 효과 상태를 남기지 않는다.
function reducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
// 창 효과는 실제 열기/닫기 때만 실행한다. 재열기는 이전 완료 콜백까지 취소한다.
const windowMotions = new WeakMap();
const activeWindowMotions = new Set();
function isWindowClosing(node) { return windowMotions.get(node)?.closing === true; }
function cancelWindowMotion(node) {
  const motion = windowMotions.get(node);
  if (!motion) return;
  clearTimeout(motion.timer);
  motion.animations.forEach((animation) => animation.cancel());
  node.classList.remove("window-opening");
  node.inert = motion.inert;
  windowMotions.delete(node);
  activeWindowMotions.delete(motion);
}
function animateWindow(node, closing, done, kind, duration) {
  if (!node || (closing && isWindowClosing(node))) return;
  cancelWindowMotion(node);
  if (reducedMotion() || document.hidden || !node.getClientRects().length || !node.animate) { done?.(); return; }
  const motion = { node, closing, inert: node.inert, animations: [] };
  windowMotions.set(node, motion);
  activeWindowMotions.add(motion);
  if (closing) node.inert = true;
  else node.classList.add("window-opening");
  const finish = () => {
    if (windowMotions.get(node) !== motion) return;
    // 숨김/제거부터 적용한 뒤 채우기 효과를 해제해 마지막 프레임이 번쩍이지 않게 한다.
    done?.();
    cancelWindowMotion(node);
  };
  motion.finish = finish;
  const add = (target, transform = null, fade = true) => {
    if (!target) return;
    const from = fade ? { opacity: 0 } : {};
    const to = fade ? { opacity: 1 } : {};
    if (transform) { from.transform = transform; to.transform = "none"; }
    motion.animations.push(target.animate(closing ? [to, from] : [from, to], {
      duration, easing: closing ? "ease-in" : "ease-out", fill: "both",
    }));
  };
  if (kind === "banner") {
    [...node.children].forEach((child) => add(child, "translateY(-6px)"));
  } else {
    const transform = kind === "dialog" ? "scale(.98)" : kind === "popover" ? "translateY(-6px)"
      : kind === "rise" ? "translateY(4px)" : null;
    add(node, transform);
    if (kind === "photo") add(node.querySelector("img"), "scale(.94)", false);
    if (kind === "notice") add(node.querySelector(".transition-box"), "scale(.98)", false);
    if (kind === "dialog-exit") add(node.firstElementChild, "scale(.98)", false);
  }
  Promise.all(motion.animations.map((animation) => animation.finished)).then(finish, () => {});
  // 백그라운드 전환이나 애니메이션 취소 때도 숨김 처리가 남지 않도록 보장한다.
  motion.timer = setTimeout(finish, duration + 50);
}
function animateOpen(node, kind = "fade", duration = 180) {
  animateWindow(node, false, null, kind, duration);
}
function animateClose(node, done, kind = "fade", duration = 140) {
  animateWindow(node, true, done, kind, duration);
}
window.matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change", (event) => {
  if (event.matches) [...activeWindowMotions].forEach((motion) => motion.finish());
});

// 모달의 입력 잠금은 즉시 해제하고, 접근성 트리에서 제외한 잔상만 짧게 닫는다.
const dialogExits = new WeakMap();
function clearDialogExit(dialog) {
  const exit = dialogExits.get(dialog);
  if (exit) { cancelWindowMotion(exit); exit.remove(); dialogExits.delete(dialog); }
}
function openAnimatedDialog(dialog) {
  clearDialogExit(dialog);
  if (dialog.open) return;
  dialog.showModal();
  animateOpen(dialog, "dialog");
}
function closeAnimatedDialog(dialog, value = "") {
  if (!dialog.open) return;
  cancelWindowMotion(dialog);
  clearDialogExit(dialog);
  if (!reducedMotion() && !document.hidden) {
    const rect = dialog.getBoundingClientRect();
    const exit = el("div", "window-dialog-exit");
    exit.inert = true;
    exit.setAttribute("aria-hidden", "true");
    const card = el("div", dialog.className);
    card.append(...[...dialog.childNodes].map((child) => child.cloneNode(true)));
    card.querySelectorAll("[id]").forEach((child) => child.removeAttribute("id"));
    Object.assign(card.style, { position: "fixed", margin: "0", boxSizing: "border-box",
      left: rect.left + "px", top: rect.top + "px", width: rect.width + "px", height: rect.height + "px", overflow: "hidden" });
    exit.append(card);
    document.body.append(exit);
    card.scrollTop = dialog.scrollTop;
    dialogExits.set(dialog, exit);
    animateClose(exit, () => { exit.remove(); dialogExits.delete(dialog); }, "dialog-exit");
  }
  dialog.close(value);
  // native close 이벤트는 큐에 남을 수 있으므로 이번 닫기의 값을 동기적으로 전달한다.
  dialog.dispatchEvent(new CustomEvent("window-closed", { detail: value }));
}
function bindAnimatedDialog(dialog) {
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); closeAnimatedDialog(dialog); });
  dialog.addEventListener("submit", (event) => {
    if (event.target.getAttribute("method") !== "dialog") return;
    event.preventDefault();
    closeAnimatedDialog(dialog, event.submitter?.value || "");
  });
  dialog.addEventListener("click", (event) => { if (event.target === dialog) closeAnimatedDialog(dialog); });
}

function playActionEffect(node, className = "polish-emphasis") {
  if (!node || document.hidden || !node.getClientRects().length || reducedMotion()) return;
  let timer;
  const clear = () => {
    clearTimeout(timer);
    node.classList.remove(className);
    node.removeEventListener("animationend", clear);
    node.removeEventListener("animationcancel", clear);
  };
  node.addEventListener("animationend", clear);
  node.addEventListener("animationcancel", clear);
  node.classList.add(className);
  // 창이 가려지거나 바쁘면 애니메이션이 멈춰 끝 신호가 안 올 수 있음 → 1초 뒤엔 무조건 지움
  // (2026-11-21 사용자: 은신처 강조가 빠지지 않음)
  timer = setTimeout(clear, 1000);
}

function renderMapFilter(items) {
  const counts = new Map([[FILTER_ALL, items.length]]);
  items.forEach((q) => q.maps.forEach((m) => counts.set(m, (counts.get(m) || 0) + 1)));
  // 많이 걸린 맵 순서, "맵 무관"은 맨 뒤
  const names = [...counts.keys()].filter((n) => n !== FILTER_ALL && !NOT_A_MAP.includes(n))
    .sort((a, b) => counts.get(b) - counts.get(a));
  NOT_A_MAP.forEach((n) => { if (counts.has(n)) names.push(n); });  // 실제 맵이 아닌 필터는 맨 뒤

  const box = document.getElementById("map-filter");
  box.replaceChildren();
  [FILTER_ALL, ...names].forEach((name) => {
    const chip = el("button", "chip" + (state.map === name ? " active" : ""), name === FILTER_ALL ? t("quest.filterAll") : mapLabel(name));
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(state.map === name));
    applyMapColor(chip, name);
    chip.append(el("span", "", counts.get(name)));
    chip.addEventListener("click", () => filterByMap(name));
    box.append(chip);
  });
}

// 서버의 언어별 맵 이름을 고정 키에 연결하고, 먼저 그려진 태그도 갱신한다.
const mapColorKeys = new Map();
function applyMapColor(node, name) {
  node.dataset.mapColor = name;
  let key = mapColorKeys.get(name) || name;
  const merged = { "night-factory": "factory", "ground-zero-21": "ground-zero", "ground-zero-tutorial": "ground-zero" };
  key = merged[key] || key;
  key = NOT_A_MAP.includes(key) || key === FILTER_ALL ? "neutral" : key.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  [...node.classList].filter((c) => c.startsWith("map-c-")).forEach((c) => node.classList.remove(c));
  node.classList.add("map-colored", `map-c-${key || "neutral"}`);
}
function registerMapColors(maps) {
  maps.forEach((m) => mapColorKeys.set(m.name, m.key));
  document.querySelectorAll("[data-map-color]").forEach((node) => applyMapColor(node, node.dataset.mapColor));
}

function filterByMap(name, scroll = false) {
  state.map = name;
  render();
  if (scroll) {
    const selected = document.querySelector("#map-filter .chip.active");
    if (selected) selected.focus({ preventScroll: true });
    document.querySelector(".quest-map-filter-row").scrollIntoView({ block: "start" });
    rememberScroll();
  }
}

// 퀘스트 카드에서만 필터 버튼으로 쓰고, 야간 표시는 맵 색 위에 그대로 둔다.
function mapTag(name, nightMaps, filterable = false, mapKey = name) {
  const tag = el(filterable ? "button" : "span", "tag", mapLabel(name));
  applyMapColor(tag, mapKey);
  if (nightMaps && nightMaps.includes(name)) {
    tag.classList.add("night");
    tag.title = t("quest.nightOnly");
    tag.append(el("span", "moon", " ☾"));
  }
  if (filterable) {
    tag.type = "button";
    const hint = t("quest.filterByMap", { map: mapLabel(name) });
    tag.title = tag.title ? `${hint} · ${tag.title}` : hint;
    tag.setAttribute("aria-label", tag.title);
    tag.addEventListener("click", (event) => {
      event.stopPropagation();
      filterByMap(name, true);
    });
  }
  return tag;
}

// 퀘스트 탭으로 가서 해당 퀘스트 카드들을 반짝이며 보여주기 (홈에서 씀)
function goToQuests(ids) {
  state.list = "in_progress";
  state.map = FILTER_ALL;
  document.querySelectorAll(".subtab").forEach((b) => b.classList.toggle("active", b.dataset.list === "in_progress"));
  render();
  goTab("quest");
  const cards = ids.map((id) => document.querySelector(`.quest[data-quest-id="${CSS.escape(id)}"]`)).filter(Boolean);
  if (!cards.length) return;
  // 탭을 바꾼 화면이 다 그려진 다음에 이동 (바로 하면 맨 위로 가는 것과 부딪힘)
  setTimeout(() => {
    cards[0].scrollIntoView({ block: "center" });
    rememberScroll();  // 앞으로 가기로 다시 왔을 때도 이 자리로
  }, 30);
  cards.forEach((c) => {
    c.classList.remove("flash");
    void c.offsetWidth;  // 반짝임 애니메이션을 다시 시작하게
    c.classList.add("flash");
  });
}

function questSourceTag(source) {
  const keys = { manual: "quest.manualName", wiki: "quest.wikiName", blog: "quest.blogName", spt: "quest.sptName", unknown: "quest.unknownName" };
  const key = keys[source];
  if (!key) return null;
  const mark = el("span", `manual-mark source-${source}`, t(key));
  mark.title = t(`${key}Hint`);
  return mark;
}

// 위키는 이름 링크 대신 같은 책 아이콘과 접근성 설명을 쓴다.
function wikiLink(url, label) {
  if (!url) return null;
  const link = el("a", "wiki-link");
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener";
  link.title = label;
  link.setAttribute("aria-label", label);
  link.append(icon("book-open", 14));
  link.addEventListener("click", (event) => event.stopPropagation());
  return link;
}

function keyWikiLink(key, nameOnly = false) {
  return wikiLink(key.wiki, nameOnly ? key.name : t("quest.keyWiki", { name: key.name }));
}

function questWikiLink(quest) {
  return wikiLink(quest.wiki, t("quest.questWiki", { name: quest.name }));
}

// 클릭 동작이 있는 칩의 위키 링크는 버튼 바로 뒤의 형제 요소로 둔다.
function appendPrepWithWiki(parent, chip, keys, nameOnly = false) {
  const links = (keys || []).map((key) => keyWikiLink(key, nameOnly)).filter(Boolean);
  if (!links.length) {
    parent.append(chip);
    return;
  }
  const group = el("span", "prep-with-wiki");
  group.append(chip, ...links);
  parent.append(group);
}

function renderQuest(q) {
  // 상인별 색상: CSS의 .trader-prapor 같은 이름으로 색을 정해 둠
  const li = el("li", `quest trader-${q.trader_key || "etc"}`);
  li.dataset.questId = q.id;  // 홈에서 "이 퀘스트로 가기" 할 때 찾는 이름표
  const top = el("div", "quest-top");
  const name = el("p", "quest-name", q.name);
  const wiki = questWikiLink(q);
  if (wiki) name.append(wiki);
  if (q.name_en) name.append(el("span", "quest-name-en", q.name_en)); // 한국어 화면: 영어 이름 작게
  name.append(el("span", "quest-trader", q.trader));
  decorateQuestChange(li, name, q.id);

  const meta = el("div", "quest-meta");
  q.maps.forEach((m) => meta.append(mapTag(m, q.night_maps, true)));
  if (q.level) meta.append(el("span", "", `Lv${q.level}+`));
  const sourceTag = questSourceTag(q.source);
  if (sourceTag) meta.append(sourceTag);
  if (q.added) {
    const mark = el("span", "inferred-mark", t("quest.added"));
    mark.title = t("quest.addedHint");
    meta.append(mark);
  }
  if (q.inferred) {
    // 로그에 완료 기록은 없지만, 이후 퀘스트를 받았으니 끝낸 게 확실한 것
    const mark = el("span", "inferred-mark", t("quest.inferred"));
    mark.title = t("quest.inferredHint", { from: q.inferred_from });
    meta.append(mark);
  }
  // 진행 중 / 숨김 목록에서는 "안 할 퀘스트" 숨기기·다시 보기 버튼
  if (state.list === "in_progress" || state.list === "hidden") {
    const hidden = !!state.hidden[q.id];
    const btn = el("button", "hide-btn", hidden ? t("quest.show") : t("quest.hide"));
    btn.type = "button";
    btn.title = hidden ? t("quest.showHint") : t("quest.hideHint");
    btn.addEventListener("click", () => toggleHidden(q.id, !hidden));
    // 맵 칸 사이에 두면 맵이 많을 때(예: 타르코프의 저격수) 밀려서 맵 칸처럼 보임 → 카드 오른쪽 위에 고정
    li.classList.add("has-hide");
    li.append(btn);
  }
  top.append(name, meta);
  li.append(top);

  // 목표: 무엇을 해야 하는지
  if (q.objectives && q.objectives.length) {
    const ol = el("ul", "objectives");
    const checkable = state.list === "in_progress";
    q.objectives.forEach((o) => {
      const item = el("li", o.optional ? "optional" : "");
      // 진행 중 목록에서는 앞에 동그라미 체크 (해낸 목표 → 지도 핀 빠짐, 2026-11-28 사용자)
      if (checkable) {
        item.classList.add("has-check");
        item.classList.toggle("goal-done", goalDone(q, o));
        item.append(goalCheck(q, o));
      }
      const body = el("span", "obj-text", o.text);
      if (o.optional) body.append(el("span", "opt-mark", t("quest.optional")));
      // 우리가 번역한 문장이면 영어 원문을 아래에 작게
      if (o.original) body.append(el("span", "original", o.original));
      item.append(body);
      ol.append(item);
    });
    li.append(ol);
  }

  // 준비물: 가기 전에 챙길 것
  if (q.prep && q.prep.length) {
    const box = el("div", "prep");
    q.prep.forEach((p) => {
      const isGive = p.kind === "제출";
      // 제출 칩은 눌러서 체크할 수 있는 버튼, 나머지는 그냥 표시
      const chip = el(isGive ? "button" : "span", `prep-item kind-${PREP_CLASS[p.kind] || "etc"}`);
      let text = p.text;
      if (p.count) text += ` ×${p.count}`;

      if (isGive) {
        const key = handedKey(q, p);
        const done = !!state.handed[key];
        chip.type = "button";
        chip.classList.toggle("done", done);
        chip.setAttribute("aria-pressed", done);
        chip.title = done ? t("quest.handedHint") : t("quest.handHint");
        chip.addEventListener("click", () => toggleHanded(key, !done));
        chip.append(el("span", "check", done ? "✓" : ""));
      }
      chip.append(el("b", "", t(`prepKind.${PREP_CLASS[p.kind] || "etc"}`)));
      if (p.kind === "열쇠" && p.keys && p.keys.length) {
        chip.classList.add("prep-key-chip");
        const names = el("span", "prep-key-names");
        p.keys.forEach((key, index) => {
          if (index) names.append(document.createTextNode(" / "));
          names.append(document.createTextNode(key.name));
          const link = keyWikiLink(key);
          if (link) names.append(link);
        });
        if (p.any_of) names.append(el("span", "key-any-of", ` (${t("quest.keyAnyOf")})`));
        chip.append(names);
      } else {
        chip.append(document.createTextNode(text));
      }
      if (p.fir) chip.append(el("span", "fir", t("quest.fir")));
      if (isGive) appendPrepWithWiki(box, chip, p.keys);
      else box.append(chip);
    });
    li.append(box);
  }
  return li;
}

const PREP_CLASS = { "열쇠": "key", "가져가기": "bring", "무기": "weapon", "착용": "wear", "제출": "give" };

function render() {
  const data = state.data;
  if (!data) return;
  resetQuestHover();
  questChangeObserver.disconnect();
  ["in_progress", "completed", "failed", "hidden"].forEach((k) => {
    document.getElementById(`count-${k}`).textContent = questsOf(k).length;
  });

  const items = questsOf(state.list);
  renderMapFilter(items);

  const shown = state.map === FILTER_ALL ? items : items.filter((q) => q.maps.includes(state.map));
  // 맵 하나를 골랐으면 "어느 맵에서나" 할 수 있는 레이드 퀘스트도 아래에 같이 (준비물 챙기기용)
  const anywhere = (state.map !== FILTER_ALL && !NOT_A_MAP.includes(state.map) && state.list === "in_progress")
    ? anywhereQuests(items) : [];
  const list = document.getElementById("quest-list");
  list.replaceChildren();
  if (shown.length === 0 && anywhere.length === 0) {
    list.append(el("li", "empty", t("quest.empty")));
  } else {
    shown.forEach((q) => list.append(renderQuest(q)));
    if (anywhere.length) {
      list.append(el("li", "list-divider", `${mapLabel(ALL_MAPS)} (${anywhere.length})`));
      anywhere.forEach((q) => list.append(renderQuest(q)));
    }
  }

  let footer = data.data_message + " · " +
    (data.prestige_since ? t("quest.footerPrestige", { date: data.prestige_since }) : t("quest.footerAllLogs"));
  document.getElementById("footer").textContent = footer;
  updateQuestChangeDots();
  observeQuestChanges();
}

let questLoadRevision = 0;
let progressMark;
let checksLoaded = false;
let checkQueue = Promise.resolve();
function queueChecks(task) {
  const result = checkQueue.then(task);
  checkQueue = result.catch(() => {});
  return result;
}
function handedKey(q, p) { return q.id + "|" + p.hand_id; }
function handedLegacyKeys(key, data = state.data) {
  const keys = new Set();
  for (const kind of ["in_progress", "completed", "failed"]) {
    for (const q of data?.[kind] || []) for (const p of q.prep || []) {
      if (p.kind !== "제출" || !p.hand_id || handedKey(q, p) !== key) continue;
      for (const text of p.legacy_texts || []) {
        const old = q.id + "|" + text;
        if (old !== key && Object.hasOwn(state.handed, old)) keys.add(old);
      }
    }
  }
  return [...keys];
}
function applyHandedAliases(data) {
  // 저장 이전에 실패해도 홈·퀘스트가 같은 제출 상태를 읽도록 메모리에 별칭을 만든다.
  for (const kind of ["in_progress", "completed", "failed"]) {
    for (const q of data[kind]) for (const p of q.prep || []) {
      if (p.kind !== "제출" || !p.hand_id) continue;
      if ((p.legacy_texts || []).some(text => state.handed[q.id + "|" + text])) {
        state.handed[handedKey(q, p)] = true;
      }
    }
  }
}
async function migrateHanded(data) {
  const changes = {};
  for (const kind of ["in_progress", "completed", "failed"]) {
    for (const q of data[kind]) for (const p of q.prep || []) {
      if (p.kind !== "제출" || !p.hand_id) continue;
      const key = handedKey(q, p);
      for (const text of p.legacy_texts || []) {
        const old = q.id + "|" + text;
        if (old === key || !Object.hasOwn(state.handed, old)) continue;
        if (state.handed[old]) changes[key] = true;
        changes[old] = null;
      }
    }
  }
  if (!Object.keys(changes).length) return;
  const res = await fetch("/api/check/handed", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ changes }),
  });
  if (!res.ok) throw new Error(t("quest.saveCheckFailed"));
  state.handed = await res.json();
}
async function reloadProgress() {
  await loadHanded();
  // 각 화면의 저장 대기열 뒤에서 다시 읽어 예전 응답이 새 회차를 덮지 않게 한다.
  if (typeof loadStory === "function" && !await loadStory()) throw new Error(t("story.saveFailed"));
  if (typeof loadHideout === "function" && !await loadHideout()) throw new Error(t("common.saveFailedShort"));
  const added = await fetch("/api/check/added");
  if (!added.ok) throw new Error(t("common.saveFailedShort"));
  await added.json();
  if (typeof setupQuests !== "undefined" && setupQuests.data) {
    await setupLoadQuests();
    renderSetupQuests();
  }
}
async function load() {
  const revision = ++questLoadRevision;
  const notice = document.getElementById("notice");
  try {
    const res = await fetch("/api/quests");
    const body = await res.json();
    if (revision !== questLoadRevision) return;
    if (!res.ok) throw new Error(body.error || t("common.unknownError"));
    if (!Object.keys(QUEST_CHANGE_LABEL).every((key) => Array.isArray(body[key]))) {
      throw new Error(t("common.unknownError"));
    }
    if (!checksLoaded) await loadHanded();
    const mark = body.prestige_since || "none";
    if (progressMark !== undefined && mark !== progressMark) await reloadProgress();
    if (revision !== questLoadRevision) return;
    applyHandedAliases(body);
    progressMark = mark;
    trackQuestChanges(body);
    state.data = body;
    notice.hidden = true;
    render();
    notifyChanged("quests-changed");
    try {
      await queueChecks(() => revision === questLoadRevision ? migrateHanded(body) : undefined);
      // 화면에는 applyHandedAliases로 이미 반영했다. 저장 키 이전만으로 다시 그리지 않는다.
    } catch (error) {
      // 목록은 유지하고 다음 불러오기에서 다시 이전한다. 옛 키는 성공 전까지 보존한다.
      if (revision === questLoadRevision) showCheckError("quest.migrationFailed");
    }
  } catch (err) {
    if (revision !== questLoadRevision) return;
    notice.textContent = t("quest.loadFailed", { error: err.message });
    notice.hidden = false;
    if (!state.data) {
      document.getElementById("quest-list").replaceChildren(el("li", "empty", t("quest.retryLater")));
    }
  }
}

// 설정에서 게임 설치 위치를 바꾸면 퀘스트·체크·스토리를 새 로그 기준으로 다시 불러옴
window.addEventListener("settings-changed", async () => {
  try { await reloadProgress(); await load(); }
  catch (error) { showCheckError("common.saveFailedShort"); }
});

function showCheckError(key) {
  const notice = document.getElementById("notice");
  notice.textContent = t(key);
  notice.hidden = false;
}

// 불러오기와 저장도 같은 대기열을 사용한다.
function loadHanded() {
  return queueChecks(async () => {
    const [handed, hidden, goals] = await Promise.all(["handed", "hidden", "goals"].map((kind) => fetch("/api/check/" + kind)));
    if (!handed.ok || !hidden.ok || !goals.ok) throw new Error(t("quest.saveCheckFailed"));
    state.handed = await handed.json();
    state.hidden = await hidden.json();
    state.goals = await goals.json();
    checksLoaded = true;
  });
}

function saveCheck(kind, key, value) {
  return queueChecks(async () => {
    const before = { ...state[kind] };
    const changes = { [key]: value };
    if (kind === "handed") {
      for (const old of handedLegacyKeys(key)) changes[old] = null;
    }
    for (const [changedKey, checked] of Object.entries(changes)) {
      if (checked) state[kind][changedKey] = true; else delete state[kind][changedKey];
    }
    render();
    notifyChanged("quests-changed");
    try {
      const res = await fetch("/api/check/" + kind, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(kind === "handed" ? { changes } : { key, value }),
      });
      if (!res.ok) throw new Error();
      state[kind] = await res.json();
    } catch (error) {
      state[kind] = before;
      showCheckError(kind === "hidden" ? "quest.saveHideFailed" : "quest.saveCheckFailed");
    }
    render();
    notifyChanged("quests-changed");
  });
}
function toggleHidden(key, value) { return saveCheck("hidden", key, value); }
function toggleHanded(key, value) { return saveCheck("handed", key, value); }

// ---------- 해낸 목표 체크 (2026-11-28 사용자: 레이드 중 임시 숨기기를 대신함) ----------
// 퀘스트 탭 목표 줄·지도 서랍 목표 줄의 동그라미. 같은 기록 하나(progress goals)라 어디서 눌러도 둘 다 바뀜.
// 체크한 목표: 취소선 + 지도 핀·구역 빠짐(번호는 그대로). 로그로는 목표 하나하나를 알 수 없어서 직접 누름.
// 건네주기 목표(o.hand)는 아래 '제출' 칩 체크를 따라감 → 누를 곳은 늘 하나
function goalKey(q, o) { return `${q.id}|${o.id || "#" + q.objectives.indexOf(o)}`; }
function goalDone(q, o) {
  return o.hand ? !!state.handed[q.id + "|" + o.hand] : !!state.goals[goalKey(q, o)];
}
function goalCheck(q, o) {
  const done = goalDone(q, o);
  if (o.hand) {
    const mark = el("span", "goal-check follow", done ? "✓" : "");
    mark.classList.toggle("on", done);
    mark.title = t("quest.goalFollowsHand");
    return mark;
  }
  const btn = el("button", "goal-check", done ? "✓" : "");
  btn.type = "button";
  btn.classList.toggle("on", done);
  btn.title = t(done ? "quest.goalUndo" : "quest.goalDo");
  btn.setAttribute("aria-label", btn.title);
  btn.setAttribute("aria-pressed", String(done));
  btn.addEventListener("click", (e) => {
    e.stopPropagation(); // 서랍 줄 누르기(지도 이동)로 번지지 않게
    saveCheck("goals", goalKey(q, o), !done);
  });
  return btn;
}

// ---------- 맨 위로 / 맨 아래로 ----------
document.getElementById("to-top").addEventListener("click", () => {
  window.scrollTo({ top: 0, behavior: reducedMotion() ? "instant" : "smooth" });
});
document.getElementById("to-bottom").addEventListener("click", () => {
  window.scrollTo({ top: document.documentElement.scrollHeight, behavior: reducedMotion() ? "instant" : "smooth" });
});

loadHanded().then(load).catch(() => { showCheckError("quest.saveCheckFailed"); load(); });

// 퀘스트 받기·완료는 로그가 늘어나는 즉시 반영 (상단바가 2초마다 확인 → quest-log-changed)
// 레이드 중(로딩 포함)에는 다시 계산하지 않고, 로비로 돌아오면 그때 한 번
const inRaidNow = () => ["loading", "raid"].includes((topbar.state || {}).status);
let questReloadPending = false;
window.addEventListener("quest-log-changed", () => {
  if (inRaidNow()) questReloadPending = true;
  else load();
});
window.addEventListener("game-state", () => {
  if (questReloadPending && !inRaidNow()) {
    questReloadPending = false;
    load();
  }
});
// 혹시 놓친 게 있을 때를 위한 느린 확인 (레이드 중에는 쉼)
setInterval(() => { if (!inRaidNow()) load(); }, 60000);
