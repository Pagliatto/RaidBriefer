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
document.addEventListener("tab-shown", () => { resetQuestHover(); observeQuestChanges(); });
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
    }, 1000));
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
  document.querySelectorAll(".tab").forEach((b) => {
    const on = b.dataset.tab === name;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on);
  });
  document.querySelectorAll("main > .panel").forEach((p) => {
    p.hidden = p.id !== `tab-${name}`;
  });
  // 지도 탭은 화면 전체를 지도로 씀 (스크롤 없음)
  document.body.classList.toggle("map-mode", name === "map");
  document.dispatchEvent(new CustomEvent("tab-shown", { detail: name }));
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
  history.replaceState({ ...(history.state || {}), tab: currentTab(), scroll: window.scrollY }, "");
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
function notifyChanged(name) {
  document.dispatchEvent(new Event(name));
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
    chip.append(el("span", "", counts.get(name)));
    chip.addEventListener("click", () => { state.map = name; render(); });
    box.append(chip);
  });
}

// 맵 태그. 야간에만 되는 맵이면 달 표시
function mapTag(name, nightMaps) {
  const tag = el("span", "tag", mapLabel(name));
  if (nightMaps && nightMaps.includes(name)) {
    tag.classList.add("night");
    tag.title = t("quest.nightOnly");
    tag.append(el("span", "moon", " ☾"));
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

function keyWikiLink(key, nameOnly = false) {
  if (!key.wiki) return null;
  const link = el("a", "wiki-link");
  link.href = key.wiki;
  link.target = "_blank";
  link.rel = "noopener";
  const label = t("quest.keyWiki", { name: key.name });
  link.title = nameOnly ? key.name : label;
  link.setAttribute("aria-label", label);
  link.append(icon("book-open", 14));
  return link;
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
  const name = el("p", "quest-name");
  if (q.wiki) {
    const a = el("a", "", q.name);
    a.href = q.wiki;
    a.target = "_blank";
    a.rel = "noopener";
    name.append(a);
  } else {
    name.textContent = q.name;
  }
  if (q.name_en) name.append(el("span", "quest-name-en", q.name_en)); // 한국어 화면: 영어 이름 작게
  name.append(el("span", "quest-trader", q.trader));
  decorateQuestChange(li, name, q.id);

  const meta = el("div", "quest-meta");
  q.maps.forEach((m) => meta.append(mapTag(m, q.night_maps)));
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
    q.objectives.forEach((o) => {
      const item = el("li", o.optional ? "optional" : "", o.text);
      if (o.optional) item.append(el("span", "opt-mark", t("quest.optional")));
      // 우리가 번역한 문장이면 영어 원문을 아래에 작게
      if (o.original) item.append(el("span", "original", o.original));
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
        const key = `${q.id}|${p.text}`;
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
    trackQuestChanges(body);
    state.data = body;
    notice.hidden = true;
    render();
    notifyChanged("quests-changed");
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
window.addEventListener("settings-changed", () => {
  load();
  loadHanded();
  if (typeof loadStory === "function") loadStory();
});

// ---------- 제출 체크 · 숨긴 퀘스트 불러오기 ----------
async function loadHanded() {
  try {
    const [handed, hidden] = await Promise.all([fetch("/api/check/handed"), fetch("/api/check/hidden")]);
    if (handed.ok) state.handed = await handed.json();
    if (hidden.ok) state.hidden = await hidden.json();
  } catch (err) {
    // 못 불러오면 체크 없이 보여줌
  }
}

// 안 할 퀘스트 숨기기 / 다시 보기
async function toggleHidden(questId, hide) {
  const before = state.hidden;
  state.hidden = { ...state.hidden };
  if (hide) state.hidden[questId] = true; else delete state.hidden[questId];
  render();
  notifyChanged("quests-changed");
  try {
    const res = await fetch("/api/check/hidden", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: questId, value: hide || null }),
    });
    if (!res.ok) throw new Error();
    state.hidden = await res.json();
  } catch (err) {
    state.hidden = before;
    const notice = document.getElementById("notice");
    notice.textContent = t("quest.saveHideFailed");
    notice.hidden = false;
  }
  render();
  notifyChanged("quests-changed");
}

async function toggleHanded(key, done) {
  const before = state.handed;
  // 먼저 화면부터 바꾸고(빠르게 보이게), 저장에 실패하면 되돌림
  state.handed = { ...state.handed };
  if (done) state.handed[key] = true; else delete state.handed[key];
  render();
  try {
    const res = await fetch("/api/check/handed", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, value: done }),
    });
    if (!res.ok) throw new Error();
    state.handed = await res.json();
  } catch (err) {
    state.handed = before;
    const notice = document.getElementById("notice");
    notice.textContent = t("quest.saveCheckFailed");
    notice.hidden = false;
  }
  render();
  notifyChanged("quests-changed");  // 홈의 "모아야 할 것"도 같이 바뀌게
}

// ---------- 맨 위로 / 맨 아래로 ----------
document.getElementById("to-top").addEventListener("click", () => {
  window.scrollTo({ top: 0, behavior: "smooth" });
});
document.getElementById("to-bottom").addEventListener("click", () => {
  window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
});

loadHanded().then(load);

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
