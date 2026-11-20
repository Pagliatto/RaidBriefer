// 스토리 탭
// - 각 단계는 독립 체크. 구간 버튼은 그 구간의 일반·선택 단계만 한 번에 체크
// - 끝난 구간은 위치와 관계없이 접고, 미완료 구간은 펼침
// - 갈림길(choice): 선택지를 고르면 그 길의 구간만 보여줌 (The Ticket 등)
// el(), PREP_CLASS 는 app.js 에 있는 것을 같이 씀
//
// 저장하는 값 (story.pos)
//   "the-ticket@kerman"        : "accept"   ← 챕터@갈림길 : 고른 선택지
//   "the-ticket-j-17!"         : "done"     ← 일반·선택·해당될 때만 단계의 개별 완료
//
// 조건(when) 종류 — 구간, 단계, 준비물에 달 수 있음
//   "kerman": "accept"                      ← 이 챕터에서 고른 갈림길
//   "falling-skies@case": "kept"            ← 다른 챕터에서 고른 갈림길
//   "done:falling-skies": false             ← 그 챕터를 끝냈나 (스토리 체크 기록)
//   "quest:6744...,6745...": true           ← 그 퀘스트(들 중 하나)를 끝냈나 (게임 로그)
// 단계에 "manual": true → 해당될 때만 직접 체크 (완료 판정·구간 일괄 체크에서 제외)

const story = {
  data: null,
  pos: {},
  queue: Promise.resolve(),  // 저장과 다시 불러오기를 직렬화해 늦은 응답의 덮어쓰기를 막음
  migrating: false,
  open: new Set(),     // 펼쳐 둔 챕터 id
};

function choiceKey(ch, choiceId) {
  return `${ch.id}@${choiceId}`;
}

// 로그로 알아낸 완료 퀘스트 id (app.js 의 state)
function completedQuestIds() {
  return new Set(state.data ? state.data.completed.map((q) => q.id) : []);
}

// 챕터를 끝냈나 (보이는 단계를 다 했고, 안 고른 갈림길도 없음)
const _doneCheck = new Set();  // 서로를 조건으로 물고 도는 것 막기
function isChapterDone(chapterId) {
  const ch = story.data && story.data.chapters.find((c) => c.id === chapterId);
  if (!ch || _doneCheck.has(chapterId)) return false;
  _doneCheck.add(chapterId);
  try {
    const { items, steps, pending } = visibleItems(ch);
    // 변환 경로를 계산할 때만 옛 완료 조건을 유지한다 (다른 챕터의 done: 조건).
    if (story.migrating && Object.hasOwn(story.pos, ch.id)) {
      return !pending && steps.length > 0 && story.pos[ch.id] === steps[steps.length - 1].id;
    }
    return !pending && items.filter((it) => it.type === "section").every(isSectionDone);
  } finally {
    _doneCheck.delete(chapterId);
  }
}

// 조건 하나의 지금 값
function conditionValue(active, key) {
  if (key.startsWith("done:")) return isChapterDone(key.slice(5));
  if (key.startsWith("quest:")) {
    const done = completedQuestIds();
    return key.slice(6).split(",").some((id) => done.has(id));
  }
  if (key.includes("@")) return story.pos[key] || null;  // 다른 챕터의 갈림길
  return active[key] || null;                             // 이 챕터의 갈림길 (지금 길에서 고른 것만)
}

// when 조건이 다 맞나
function whenMatches(active, when) {
  return Object.entries(when || {}).every(([key, want]) => {
    const value = conditionValue(active, key);
    return Array.isArray(want) ? want.includes(value) : value === want;
  });
}

// 항목(구간·단계·준비물)이 보여야 하나
//   when     : 조건이 전부 맞아야 함
//   when_any : [조건묶음, 조건묶음 …] 중 하나라도 맞으면 됨
//   예) 생존자 길 = "제안 거절" 또는 "구원자인데 Choose Your Friends Wisely 완료(길 막힘)"
function itemVisible(active, item) {
  if (!whenMatches(active, item.when)) return false;
  return !item.when_any || item.when_any.some((w) => whenMatches(active, w));
}

// 지금 보여줄 항목들 (고른 길·맞는 조건만). 아직 안 고른 갈림길이 나오면 거기서 멈춤.
// 돌려주는 값: { items: [{type:"section", sec, steps, manual} | {type:"choice", choice, picked}], steps, pending }
//   steps  = 일반·선택 단계 (옛 기록 변환에도 같은 목록을 씀)
//   manual = "해당될 때만" 단계 (완료 판정·구간 일괄 체크에서 제외)
function visibleItems(ch) {
  const items = [];
  const steps = [];
  const active = {};  // 지금 길에서 실제로 지나온 갈림길의 선택만 (다른 길에 남은 옛 선택은 무시)
  let pending = null;
  for (const sec of ch.sections) {
    if (!itemVisible(active, sec)) continue;
    if (sec.notice) {  // 안내 카드 (단계 없음): 예) "구원자 길이 끝났어요"
      items.push({ type: "notice", sec });
      continue;
    }
    if (sec.choice) {
      const picked = story.pos[choiceKey(ch, sec.choice.id)] || null;
      items.push({ type: "choice", choice: sec.choice, picked });
      if (!picked) { pending = sec.choice; break; }
      active[sec.choice.id] = picked;
      continue;
    }
    const shown = sec.steps.filter((st) => itemVisible(active, st));
    const normal = shown.filter((st) => !st.manual);
    const manual = shown.filter((st) => st.manual);
    if (!normal.length && !manual.length) continue;  // 조건에 맞는 단계가 하나도 없는 구간은 숨김
    items.push({ type: "section", sec, steps: normal, manual });
    steps.push(...normal);
  }
  return { items, steps, pending };
}

// 준비물 중 조건에 맞는 것만 (다른 챕터 선택·챕터 완료·퀘스트 완료 조건)
function visiblePrep(sec) {
  return (sec.prep || []).filter((p) => itemVisible({}, p));
}

// 안내 카드 (예: 구원자 길이 막혀서 생존자로 바뀜)
function renderNotice(item) {
  const box = el("div", "story-notice-card");
  box.append(el("h3", "", item.sec.title));
  if (item.sec.tip) box.append(el("p", "story-tip", item.sec.tip));
  return box;
}

// 챕터의 (보이는) 모든 단계를 순서대로 한 줄로
function allSteps(ch) {
  return visibleItems(ch).steps;
}

// 일반·선택·해당될 때만 단계가 같은 저장 규칙을 쓴다.
function isStepDone(step) {
  return story.pos[`${step.id}!`] === "done";
}

function isRequiredStep(step) {
  return !step.optional && !step.manual;
}

function isSectionDone(item) {
  return item.steps.filter(isRequiredStep).every(isStepDone);
}

function isSectionStarted(item) {
  return [...item.steps, ...item.manual].some(isStepDone);
}

function isChapterStarted(ch) {
  // 현재 길에서 찾을 수 없는 옛 위치는 보존하되 시작 기록으로 해석하지 않는다.
  if (Object.hasOwn(story.pos, ch.id) && !allSteps(ch).some((s) => s.id === story.pos[ch.id])) return false;
  return ch.sections.some((sec) => sec.choice
    ? !!story.pos[choiceKey(ch, sec.choice.id)]
    : (sec.steps || []).some(isStepDone));
}

function storyChip(p) {
  const chip = el("span", `prep-item kind-${PREP_CLASS[p.kind] || "etc"}`);
  chip.append(el("b", "", t(`prepKind.${PREP_CLASS[p.kind] || "etc"}`)));
  let text = p.text;
  if (p.count) text += ` ×${p.count.toLocaleString()}`;
  chip.append(document.createTextNode(text));
  if (p.fir) chip.append(el("span", "fir", t("quest.fir")));
  return chip;
}

// 여러 맵에 걸친 단계도 맵마다 별도 색 태그로 표시한다.
function appendStoryMapTags(parent, item) {
  const names = item.map_names && item.map_names.length ? item.map_names : (item.map_name ? item.map_name.split(" / ") : []);
  const keys = Array.isArray(item.map) ? item.map : [item.map];
  names.forEach((name, index) => parent.append(mapTag(name, null, false, keys[index] || name)));
}

function renderStep(step, current = false) {
  const done = isStepDone(step);
  const li = el("li", "step" + (step.optional ? " optional" : "") + (done ? " done" : ""));
  const label = el("label");
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = done;
  box.title = done ? t("story.stepUndo") : t("story.stepDone");
  box.addEventListener("change", () => saveStory(`${step.id}!`, box.checked ? "done" : null));
  label.append(box, el("span", "step-text", step.ko));
  if (step.optional) label.append(el("span", "opt-mark", t("story.optional")));
  appendStoryMapTags(label, step);
  if (current) {
    li.classList.add("current-step");
    label.append(el("span", "step-now", t("story.stepNow")));
  }
  li.append(label);
  return li;
}

// "해당될 때만" 단계: 개별 체크만 가능 (미리 알 수 없는 조건)
function renderManualStep(step) {
  const key = `${step.id}!`;
  const done = story.pos[key] === "done";
  const li = el("li", "step manual" + (done ? " done" : ""));
  const label = el("label");
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = done;
  box.title = t("story.manualHint");
  box.addEventListener("change", () => saveStory(key, box.checked ? "done" : null));
  label.append(box, el("span", "step-text", step.ko), el("span", "manual-mark", t("story.manualMark")));
  appendStoryMapTags(label, step);
  li.append(label);
  return li;
}

// 구간 내용 (팁·준비물·사진·단계·보상)
function sectionBody(item, current = false) {
  const { sec } = item;
  const box = el("div", "section-body");
  if (sec.tip) box.append(el("p", "story-tip", sec.tip));
  const prepList = visiblePrep(sec);
  if (prepList.length) {
    const prep = el("div", "prep");
    prepList.forEach((p) => prep.append(storyChip(p)));
    box.append(prep);
  }
  if (sec.images && sec.images.length) {
    const row = el("div", "story-images");
    sec.images.forEach((im) => {
      const a = el("a");
      a.href = im.full;
      a.target = "_blank";
      a.rel = "noopener";
      a.title = t("story.photoHint", { caption: im.caption });
      // 새 창 대신 화면 가운데 팝업으로 (다시 누르거나 바깥을 누르면 닫힘)
      a.addEventListener("click", (e) => {
        e.preventDefault();
        openPhoto(im);
      });
      const img = document.createElement("img");
      img.src = im.thumb;
      img.alt = im.caption;
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      a.append(img, el("span", "", im.caption));
      row.append(a);
    });
    box.append(row);
  }
  const ul = el("ul", "steps");
  const next = current && item.steps.find((s) => isRequiredStep(s) && !isStepDone(s));
  item.steps.forEach((s) => ul.append(renderStep(s, s === next)));
  item.manual.forEach((s) => ul.append(renderManualStep(s)));
  box.append(ul);
  if (sec.reward) box.append(el("p", "story-reward", t("story.reward", { reward: sec.reward })));
  return box;
}

function sectionHead(item, extraClass) {
  const { sec } = item;
  const head = el("div", "story-section-head" + (extraClass ? " " + extraClass : ""));
  head.append(el("h3", "", sec.title));
  appendStoryMapTags(head, sec);
  // 오른쪽 위 버튼 두 개: 하나라도 안 했으면 '전체 체크', 하나라도 했으면 '전체 해제' (둘 다 보일 수 있음)
  // 태그와 헷갈리지 않게 아이콘 + 네모 버튼. "해당될 때만" 단계는 건드리지 않음
  const actions = el("span", "section-actions");
  const addAction = (iconName, labelKey, hintKey, value, show) => {
    if (!show) return;
    const btn = el("button", `section-action ${value ? "check" : "uncheck"}`);
    btn.type = "button";
    btn.title = t(hintKey);
    btn.append(icon(iconName, 14), el("span", "", t(labelKey)));
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      saveStoryChanges(Object.fromEntries(item.steps.map((s) => [`${s.id}!`, value])));
    });
    actions.append(btn);
  };
  addAction("check", "story.sectionDone", "story.sectionDoneHint", "done", item.steps.some((s) => !isStepDone(s)));
  addAction("x", "story.sectionUndo", "story.sectionUndoHint", null, item.steps.some(isStepDone));
  if (actions.childElementCount) head.append(actions);
  return head;
}

// 갈림길 카드: 아직 안 골랐으면 선택 버튼들, 골랐으면 "선택함: ○○ · 바꾸기"
function renderChoice(ch, item, isCurrent) {
  const { choice, picked } = item;
  const box = el("div", "story-choice" + (picked ? " picked" : "") + (isCurrent ? " current" : ""));
  if (isCurrent) box.append(el("p", "current-label", t("story.choiceNow")));
  box.append(el("h3", "", choice.title));
  if (!picked) {
    if (choice.question) box.append(el("p", "story-tip", choice.question));
    const opts = el("div", "choice-options");
    choice.options.forEach((o) => {
      // blocked_when 조건이 맞으면 이 선택지는 막힘 (예: Choose Your Friends Wisely 완료 → 구원자 불가)
      const blocked = o.blocked_when && whenMatches({}, o.blocked_when);
      const btn = el("button", "choice-option" + (blocked ? " blocked" : ""));
      btn.type = "button";
      btn.append(el("b", "", (blocked ? "⛔ " : "") + o.label));
      if (o.desc) btn.append(el("span", "", o.desc));
      if (blocked && o.blocked_note) btn.append(el("span", "blocked-note", o.blocked_note));
      btn.addEventListener("click", () => saveStory(choiceKey(ch, choice.id), o.id));
      opts.append(btn);
    });
    box.append(opts);
  } else {
    const opt = choice.options.find((o) => o.id === picked);
    const line = el("p", "choice-picked");
    line.append(document.createTextNode(t("story.choicePicked", { label: opt ? opt.label : picked })));
    const change = el("button", "choice-change", t("story.choiceChange"));
    change.type = "button";
    change.title = t("story.choiceChangeHint");
    change.addEventListener("click", () => saveStory(choiceKey(ch, choice.id), null));
    line.append(change);
    box.append(line);
  }
  return box;
}

function renderChapter(ch) {
  const { items, steps, pending } = visibleItems(ch);
  const required = steps.filter(isRequiredStep);
  const doneCount = required.filter(isStepDone).length;
  const nextStep = required.find((s) => !isStepDone(s));

  // 지금 할 구간 (없으면: 안 고른 갈림길이 있거나, 챕터 완료)
  const current = items.find((it) => it.type === "section" && !isSectionDone(it)) || null;
  const finishedAll = !current && !pending;

  const card = document.createElement("details");
  card.className = "chapter" + (finishedAll ? " finished" : "");
  card.dataset.chapterId = ch.id;
  card.open = story.open.has(ch.id);
  let wasOpen = card.open;
  card.addEventListener("toggle", () => {
    if (card.open) story.open.add(ch.id); else story.open.delete(ch.id);
    if (card.open && !wasOpen) animateOpen(card.querySelector(".chapter-body"), "fade", 150);
    else if (!card.open) cancelWindowMotion(card.querySelector(".chapter-body"));
    wasOpen = card.open;
  });

  // 접혀 있어도 보이는 줄: 이름 · 진행률 · 다음 할 일
  const summary = el("summary");
  const top = el("div", "chapter-top");
  top.append(el("span", "chapter-name", ch.name));
  const bar = el("span", "chapter-bar");
  const fill = el("span");
  fill.style.width = required.length ? `${Math.round((doneCount / required.length) * 100)}%` : (finishedAll ? "100%" : "0%");
  bar.append(fill);
  // 갈림길을 아직 안 고르면 뒤 단계 수를 모르니 "+" 표시
  top.append(bar, el("span", "chapter-count", `${doneCount}/${required.length}${pending ? "+" : ""}`));
  summary.append(top);
  const next = el("div", "chapter-next");
  if (current) {
    next.append(document.createTextNode(t("story.next", { title: nextStep.ko })));
    appendStoryMapTags(next, nextStep.map_names && nextStep.map_names.length ? nextStep : current.sec);
  } else if (pending) {
    next.append(el("span", "chapter-choice", t("story.choice", { title: pending.title })));
  } else {
    next.append(el("span", "chapter-done", t("story.chapterDone")));
  }
  summary.append(next);
  card.append(summary);

  const body = el("div", "chapter-body");
  const links = el("p", "chapter-links");
  links.append(document.createTextNode(ch.requirement ? t("story.requirement", { text: ch.requirement }) : ""));
  if (ch.library) {
    const library = el("a", "", t("story.library"));
    library.href = ch.library;
    library.target = "_blank";
    library.rel = "noopener";
    links.append(library);
  }
  const wiki = wikiLink(ch.wiki, t("story.wiki"));
  if (wiki) links.append(wiki);
  body.append(links);

  // 완료한 구간은 앞뒤 위치에 관계없이 한 그룹에 모은다.
  const finishedSecs = items.filter((it) => it.type === "section" && isSectionDone(it));
  if (finishedSecs.length) {
    const group = document.createElement("details");
    group.className = "finished-group";
    group.append(el("summary", "", t("story.finished", { n: finishedSecs.length, names: "" })));
    finishedSecs.forEach((it) => {
      const box = el("div", "story-section");
      box.append(sectionHead(it), sectionBody(it));
      group.append(box);
    });
    group.addEventListener("toggle", () => {
      [...group.children].filter((child) => child.tagName !== "SUMMARY").forEach((child) => {
        if (group.open) animateOpen(child, "fade", 150);
        else cancelWindowMotion(child);
      });
    });
    body.append(group);
  }

  // 미완료 구간은 모두 펼치고 갈림길·안내 카드는 경로 순서대로 유지한다.
  items.forEach((it) => {
    if (it.type === "choice") {
      body.append(renderChoice(ch, it, !current && !it.picked));
    } else if (it.type === "notice") {
      body.append(renderNotice(it));
    } else if (!isSectionDone(it)) {
      const box = el("div", "story-section" + (it === current ? " current" : ""));
      if (it === current) box.append(el("p", "current-label", t("story.currentLabel")));
      box.append(sectionHead(it), sectionBody(it, it === current));
      body.append(box);
    }
  });

  card.append(body);
  return card;
}

function renderStory() {
  const list = document.getElementById("story-list");
  if (!story.data) return;
  list.replaceChildren();
  story.data.chapters.forEach((ch) => list.append(renderChapter(ch)));
  if (story.data.coming.length) {
    list.append(el("p", "story-coming", t("story.coming", { n: story.data.coming.length })));
  }
  document.getElementById("story-credit").textContent = t("story.credit");
  notifyChanged("story-changed");
}

// 저장·불러오기는 순서대로 처리해 실패 복구가 뒤의 체크를 지우지 않게 한다.
function queueStory(task) {
  const result = story.queue.then(task);
  story.queue = result.catch(() => {});
  return result;
}

function saveStory(key, value) {
  return saveStoryChanges({ [key]: value });
}

function saveStoryChanges(changes) {
  return queueStory(() => persistStoryChanges(changes));
}

// 마이그레이션은 서버가 성공하기 전까지 메모리의 옛 키도 건드리지 않는다.
async function persistStoryChanges(changes, optimistic = true) {
  if (!Object.keys(changes).length) return true;
  const before = story.pos;
  // 저장 전 미완료였던 구간만 비교한다. 마이그레이션·재조회는 강조하지 않는다.
  const unfinished = new Map(optimistic ? story.data.chapters.map((ch) => [ch.id,
    new Set(visibleItems(ch).items.filter((it) => it.type === "section" && !isSectionDone(it)).map((it) => it.sec)),
  ]) : []);
  if (optimistic) {
    story.pos = { ...before };
    Object.entries(changes).forEach(([key, value]) => {
      if (value) story.pos[key] = value; else delete story.pos[key];
    });
    renderStory();
  }
  try {
    const res = await fetch("/api/check/story", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes }),
    });
    if (!res.ok) throw new Error();
    story.pos = await res.json();
    if (optimistic) {
      renderStory();
      story.data.chapters.forEach((ch) => {
        const completed = visibleItems(ch).items.some((it) => it.type === "section" && unfinished.get(ch.id).has(it.sec) && isSectionDone(it));
        if (!completed) return;
        const card = [...document.querySelectorAll(".chapter")].find((node) => node.dataset.chapterId === ch.id);
        playActionEffect(card && card.querySelector(".finished-group > summary"));
        playActionEffect(card && card.querySelector(".story-section.current .story-section-head h3"));
      });
    }
    return true;
  } catch (err) {
    story.pos = before;
    const notice = document.getElementById("story-notice");
    notice.textContent = t("story.saveFailed");
    notice.hidden = false;
    if (optimistic) renderStory();
    return false;
  }
}

async function migrateStory() {
  const changes = {};
  story.migrating = true;
  try {
    // 모두 같은 옛 상태로 경로를 계산한 뒤 모든 챕터를 한 번에 저장한다.
    story.data.chapters.forEach((ch) => {
      if (!Object.hasOwn(story.pos, ch.id)) return;
      const steps = allSteps(ch);
      const savedIndex = steps.findIndex((s) => s.id === story.pos[ch.id]);
      if (savedIndex < 0) return;  // 숨겨졌거나 사라진 위치: 옛 키를 그대로 보존
      steps.slice(0, savedIndex + 1).forEach((s) => { changes[`${s.id}!`] = "done"; });
      changes[ch.id] = null;
    });
  } finally {
    story.migrating = false;
  }
  await persistStoryChanges(changes, false);
}

function loadStory() {
  return queueStory(loadStoryData);
}

async function loadStoryData() {
  const notice = document.getElementById("story-notice");
  try {
    const [dataRes, posRes] = await Promise.all([fetch("/api/story"), fetch("/api/check/story")]);
    const data = await dataRes.json();
    if (!dataRes.ok) throw new Error(data.error || t("common.unknownError"));
    story.data = data;
    if (posRes.ok) {
      // 예전 방식(단계id: true) 기록은 무시
      const pos = await posRes.json();
      story.pos = Object.fromEntries(Object.entries(pos).filter(([, v]) => typeof v === "string"));
    }
    notice.hidden = true;
    if (posRes.ok) await migrateStory();
    renderStory();
  } catch (err) {
    notice.textContent = t("story.loadFailed", { error: err.message });
    notice.hidden = false;
  }
}

loadStory();
// 퀘스트 정보(로그)가 들어오거나 바뀌면 "퀘스트 완료" 조건을 다시 계산
document.addEventListener("quests-changed", () => { if (story.data) renderStory(); });


// ---------- 사진 크게 보기 (팝업) ----------
// 가운데에 크게 띄우고, 어디를 누르든(사진·바깥) 또는 Esc 로 닫힘. 잠깐잠깐 확인하는 용도
let photoRevision = 0;
let photoFocus = null;
function openPhoto(im) {
  const revision = ++photoRevision;
  let box = document.getElementById("photo-popup");
  if (!box) {
    box = el("div", "photo-popup");
    box.id = "photo-popup";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    const img = document.createElement("img");
    img.referrerPolicy = "no-referrer"; // 위키 그림은 이 설정이어야 열림
    box.append(img, el("p", "photo-caption"));
    box.addEventListener("click", closePhoto);
    document.body.append(box);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePhoto(); });
  }
  if (!box.classList.contains("open") || isWindowClosing(box)) photoFocus = document.activeElement;
  cancelWindowMotion(box);
  const img = box.querySelector("img");
  img.alt = im.caption;
  img.src = im.thumb; // 작은 그림을 먼저 보여주고
  const full = new Image();
  full.referrerPolicy = "no-referrer";
  full.onload = () => { if (revision === photoRevision && box.classList.contains("open") && !isWindowClosing(box)) img.src = im.full; }; // 다 받으면 선명한 그림으로
  full.src = im.full;
  box.querySelector(".photo-caption").textContent = t("story.photoCaption", { caption: im.caption });
  box.classList.add("open");
  animateOpen(box, "photo", 200);
  box.tabIndex = -1;
  box.focus({ preventScroll: true });
}

function closePhoto() {
  const box = document.getElementById("photo-popup");
  if (!box?.classList.contains("open") || isWindowClosing(box)) return;
  ++photoRevision;
  animateClose(box, () => box.classList.remove("open"), "photo");
  if (photoFocus?.isConnected) photoFocus.focus({ preventScroll: true });
}
