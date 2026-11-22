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

function storyPlacesFor(id, map = null) {
  const places = story.data?.places?.[id];
  return Array.isArray(places) ? places.filter((p) => p && typeof p.map === "string"
    && (!map || p.map === map) && [p.x, p.y, p.z].every(Number.isFinite)) : [];
}

function appendStoryPlacesButton(li, step) {
  if (!storyPlacesFor(step.id).length) return;
  const button = el("button", "story-places-button", t("story.viewPlaces"));
  button.type = "button";
  button.addEventListener("click", () => openStoryPlaces(step.id));
  li.append(button);
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
  appendStoryPlacesButton(li, step);
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
  appendStoryPlacesButton(li, step);
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
  // 구간 진행 표시: 다 했으면 '✓ 완료', 아니면 '3/9' (2026-11-21 사용자: 했나 안 했나 구별이 안 감)
  const required = item.steps.filter(isRequiredStep);
  if (required.length) {
    const done = required.filter(isStepDone).length;
    const finished = done === required.length;
    head.append(el("span", "section-progress" + (finished ? " finished" : done ? " started" : ""),
      finished ? t("story.sectionComplete") : `${done}/${required.length}`));
  }
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
  box.dataset.storySection = ch.sections.findIndex((sec) => sec.choice === choice);
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
  // 펼치기 효과는 뺌 (2026-11-21 사용자: 다시 그릴 때마다 여러 번 깜빡임)
  card.addEventListener("toggle", () => {
    if (card.open) story.open.add(ch.id); else story.open.delete(ch.id);
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
      box.dataset.storySection = ch.sections.indexOf(it.sec);
      box.append(sectionHead(it), sectionBody(it));
      group.append(box);
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
      box.dataset.storySection = ch.sections.indexOf(it.sec);
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
    if (!posRes.ok) throw new Error((await posRes.json()).error || t("common.unknownError"));
    if (posRes.ok) {
      // 예전 방식(단계id: true) 기록은 무시
      const pos = await posRes.json();
      story.pos = Object.fromEntries(Object.entries(pos).filter(([, v]) => typeof v === "string"));
    }
    notice.hidden = true;
    if (posRes.ok) await migrateStory();
    renderStory();
    return true;
  } catch (err) {
    notice.textContent = t("story.loadFailed", { error: err.message });
    notice.hidden = false;
    return false;
  }
}

// ---------- 엔딩 흐름도 (기존 조건·단계만 읽고 진행 기록은 변경하지 않음) ----------
const storyFlow = { view: "progress", open: null, expanded: new Set(), tips: new Set() };
try {
  if (localStorage.getItem("raidbriefer.story.view") === "endings") storyFlow.view = "endings";
} catch (_) { /* 저장소를 막은 뷰어에서도 기본 화면으로 계속 사용 */ }

function setStoryView(view) {
  storyFlow.view = view === "endings" ? "endings" : "progress";
  document.getElementById("story-list").hidden = storyFlow.view !== "progress";
  document.getElementById("story-endings").hidden = storyFlow.view !== "endings";
  document.querySelectorAll("[data-story-view]").forEach((btn) => {
    const selected = btn.dataset.storyView === storyFlow.view;
    btn.classList.toggle("active", selected);
    btn.setAttribute("aria-selected", String(selected));
    btn.tabIndex = selected ? 0 : -1;
  });
  try { localStorage.setItem("raidbriefer.story.view", storyFlow.view); } catch (_) { /* 저장 실패는 진행에 영향 없음 */ }
}

// 챕터 진행 ↔ 엔딩 분기 이동도 뒤로 가기 기록에 남김 (2026-11-21 사용자: 마우스 뒤로 버튼이 엉뚱한 곳으로 감)
function goStoryView(view) {
  if (storyFlow.view === view) return;
  rememberScroll();
  setStoryView(view);
  window.scrollTo(0, 0);
  history.pushState({ tab: "story", storyView: storyFlow.view, scroll: 0 }, "");
}

document.querySelectorAll("[data-story-view]").forEach((btn, index, buttons) => {
  btn.addEventListener("click", () => goStoryView(btn.dataset.storyView));
  btn.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
      : (index + (event.key === "ArrowLeft" ? -1 : 1) + buttons.length) % buttons.length;
    buttons[next].click();
    buttons[next].focus();
  });
});
setStoryView(storyFlow.view);

function storyFlowModel(ch) {
  const visible = visibleItems(ch);
  const active = Object.fromEntries(visible.items.filter((it) => it.type === "choice" && it.picked)
    .map((it) => [it.choice.id, it.picked]));
  const required = visible.steps.filter(isRequiredStep);
  const ending = !visible.pending && ch.endings.find((e) => itemVisible(active, e));
  const current = visible.items.find((it) => it.type === "section" && !isSectionDone(it));
  const last = ch.sections[ch.sections.length - 1];
  const common = last && !last.choice && !last.when && !last.when_any ? last : null;
  const done = required.filter(isStepDone).length;
  const currentIndex = current ? ch.sections.indexOf(current.sec) : Infinity;
  const finished = new Set(visible.items.filter((it) => it.type === "section" && it.sec !== common
    && ch.sections.indexOf(it.sec) < currentIndex && isSectionDone(it)).map((it) => it.sec));
  // 이전 챕터가 없거나 오래된 기록이면 완료로 추측하지 않는다.
  const caseChapter = story.data.chapters.find((c) => c.id === "falling-skies");
  const notStarted = !isChapterStarted(ch) && !isChapterDone(caseChapter?.id);
  const banner = notStarted ? t("story.flow.notStarted", { requirement: ch.requirement || "" })
    : visible.pending ? t("story.flow.nextFork", { n: required.length - done, title: visible.pending.title })
    : t(current ? "story.flow.towardEnding" : "story.flow.endingDone", {
      ending: ending?.name || t("story.flow.undecided"), done, total: required.length,
    });
  // 각 후보 경로도 원본 구간 순서와 같은 조건 판정으로 구성한다.
  function walk(start, picks, path) {
    const nodes = [];
    let notice = null;
    for (let i = start; i < ch.sections.length; i++) {
      const sec = ch.sections[i];
      if (sec === common || !itemVisible(picks, sec)) continue;
      if (sec.choice) {
        const branches = sec.choice.options.map((option) => ({ option,
          tree: walk(i + 1, { ...picks, [sec.choice.id]: option.id }, path + "/" + option.id),
        }));
        nodes.push({ type: "fork", sec, index: i, branches });
        return { nodes, picks, path };
      }
      if (sec.notice) { notice = sec; break; }
      const steps = (sec.steps || []).filter((st) => itemVisible(picks, st) && isRequiredStep(st));
      if (!(sec.steps || []).some((st) => itemVisible(picks, st))) continue;
      nodes.push({ type: "section", sec, index: i, steps });
    }
    const end = ch.endings.find((e) => itemVisible(picks, e));
    // 차단 안내가 있는 길은 기존 조건의 목적지로 연결하고 해당 구간은 목적지 열에서 한 번만 표시한다.
    const blocked = notice && ch.endings.find((e) => e.when && Object.entries(e.when)
      .some(([key, value]) => !key.includes(":") && !key.includes("@") && picks[key] === value));
    return { nodes, picks, path, ending: blocked || end, redirect: blocked && end, notice };
  }
  return { ch, visible, active, required, ending, current, common, finished, banner, tree: walk(0, {}, ch.id) };
}

function flowButton(text, className, action) {
  const btn = el("button", className, text);
  btn.type = "button";
  btn.title = text;
  btn.addEventListener("click", action);
  return btn;
}

function flowToggle(key, label, className, set) {
  const btn = flowButton(label, className, () => {
    if (set.has(key)) {
      set.delete(key);
      if (key.endsWith("/branch") && storyFlow.open?.startsWith(key.slice(0, -7) + "/")) storyFlow.open = null;
    } else set.add(key);
    renderStoryEndings();
  });
  btn.dataset.flowKey = key;
  btn.setAttribute("aria-expanded", String(set.has(key)));
  return btn;
}

// 다른 탭(지도·홈)에서 챕터 열기: 항상 '챕터 진행' 화면으로, 뒤로 가기 한 번에 출발 화면으로 (Codex 41 #2)
function openStoryChapter(chapterId) {
  story.open.add(chapterId);
  setStoryView("progress");
  renderStory();
  goTab("story");
  history.replaceState({ ...(history.state || {}), storyView: "progress" }, "");
  const card = [...document.querySelectorAll(".chapter")].find((c) => c.dataset.chapterId === chapterId);
  if (card) setTimeout(() => card.scrollIntoView({ block: "start" }), 30);
}

function jumpStorySection(ch, index) {
  story.open.add(ch.id);
  goStoryView("progress"); // 뒤로 가기로 흐름도의 원래 자리로 돌아옴
  renderStory();
  const card = [...document.querySelectorAll(".chapter")].find((node) => node.dataset.chapterId === ch.id);
  if (!card) return;
  // 아직 선택 전인 미래 구간이면 그 구간을 여는 갈림길로 안내한다. 선택은 자동으로 바꾸지 않는다.
  const target = card.querySelector('[data-story-section="' + index + '"]')
    || card.querySelector(".story-choice:not(.picked)") || card;
  for (let parent = target.parentElement; parent && parent !== card; parent = parent.parentElement) {
    if (parent.tagName === "DETAILS") parent.open = true;
  }
  target.scrollIntoView({ block: "center", behavior: "auto" });
  target.tabIndex = -1;
  target.focus({ preventScroll: true });
  playActionEffect(target);
}

function flowNode(model, key, title, className, tags, detail) {
  const wrapper = el("div", "flow-node-wrap");
  const btn = flowButton("", "flow-node " + className, () => {
    storyFlow.open = storyFlow.open === key ? null : key;
    renderStoryEndings();
  });
  btn.title = title;
  btn.dataset.flowKey = key;
  btn.setAttribute("aria-expanded", String(storyFlow.open === key));
  btn.append(el("span", "flow-label", title));
  if (tags.length) {
    const row = el("span", "flow-tags");
    tags.forEach((tag) => row.append(el("span", "flow-tag", tag)));
    btn.append(row);
  }
  wrapper.append(btn);
  if (storyFlow.open === key) {
    const body = el("div", "flow-detail");
    detail(body);
    wrapper.append(body);
  }
  return wrapper;
}

function flowSection(model, node, path, onPath, common = false) {
  const { ch, current } = model;
  const { sec, index, steps } = node;
  const count = steps.filter(isStepDone).length;
  const done = onPath && count === steps.length;
  const now = onPath && current?.sec === sec;
  const key = path + "/section/" + index;
  const title = common ? t("story.flow.common", { title: sec.title }) : sec.title;
  const tags = [count + "/" + steps.length];
  if (now) tags.push(t("story.flow.here"));
  if (common) tags.push(t("story.flow.terminalTime"));
  const wrapper = flowNode(model, key, (done ? "✓ " : now ? "● " : "○ ") + title,
    done ? "done" : now ? "current" : "future", tags, (body) => {
      if (sec.tip) {
        const tip = el("p", "flow-tip" + (storyFlow.tips.has(key) ? "" : " clamped"), sec.tip);
        body.append(tip, flowToggle(key, t(storyFlow.tips.has(key) ? "story.flow.less" : "story.flow.more"), "flow-more", storyFlow.tips));
      }
      body.append(flowButton(t("story.flow.viewProgress"), "flow-action", () => jumpStorySection(ch, index)));
    });
  if (now) {
    const bar = el("span", "flow-progress");
    const fill = el("span");
    fill.style.width = (steps.length ? count / steps.length * 100 : 0) + "%";
    bar.append(fill);
    wrapper.firstElementChild.append(bar);
  }
  return wrapper;
}

function flowEnding(model, ending, path, blocked = false) {
  const box = flowNode(model, path + "/ending/" + ending.id, (blocked ? "⛔ " : "") + ending.name,
    "ending" + (blocked ? " blocked" : model.ending?.id === ending.id ? " selected" : ""),
    [t("story.flow.achievement")], (body) => {
      [["route", ending.route], ["achievement", ending.achievement], ["limits", ending.limits.join(" · ")],
        ["rewards", ending.rewards]].forEach(([label, text]) => {
        const line = el("p");
        line.append(el("b", "", t(`story.flow.${label}`) + ": "), document.createTextNode(text));
        body.append(line);
      });
    });
  if (!blocked) box.dataset.endingId = ending.id;
  return box;
}

function flowTree(model, tree, onPath) {
  const lane = el("div", "flow-lane");
  const groupKey = model.ch.id + "/finished";
  tree.nodes.forEach((node) => {
    if (onPath && model.finished.has(node.sec) && !storyFlow.expanded.has(groupKey)
      && storyFlow.open !== tree.path + "/section/" + node.index) return;
    if (node.type === "section") {
      lane.append(flowSection(model, node, tree.path, onPath));
      return;
    }
    const choice = node.sec.choice;
    lane.append(flowNode(model, tree.path + "/fork/" + choice.id, choice.title, "fork",
      [t("story.flow.irreversible")], (body) => {
        if (choice.question) body.append(el("p", "", choice.question));
        choice.options.forEach((option) => {
          const line = el("p");
          line.append(el("b", "", option.label + ": "), document.createTextNode(option.desc || ""));
          body.append(line);
        });
        body.append(flowButton(t("story.flow.pickProgress"), "flow-action", () => jumpStorySection(model.ch, node.index)));
      }));
    const columns = el("div", "flow-columns");
    const weights = [];
    node.branches.forEach(({ option, tree: branch }) => {
      const picked = onPath && model.active[choice.id];
      // 차단되어 옮겨 간 목적지 열은 실제 선택과 달라도 펼쳐서 현재 구간을 보여 준다.
      const redirected = branch.ending && model.ending?.id === branch.ending.id && !branch.redirect;
      const selected = !!(picked === option.id || redirected);
      const other = !!picked && !selected;
      // 고르지 않은 길도 엔딩 분기 중 하나라 접지 않고 항상 펼쳐 둔다 (2026-11-21 사용자). 흐리게만 표시
      const column = el("div", "flow-branch" + (selected ? " selected" : other ? " other" : " future"));
      const label = el("div", "flow-branch-label flow-label", option.label);
      label.title = option.label;
      column.append(label);
      column.append(flowTree(model, branch, onPath && selected));
      columns.append(column);
      weights.push(flowBranchWidth(model, branch, onPath && selected));
    });
    columns.style.gridTemplateColumns = weights.map((weight) => "minmax(0, " + weight + "fr)").join(" ");
    lane.append(columns);
  });
  if (tree.ending) lane.append(flowEnding(model, tree.ending, tree.path, !!tree.redirect));
  if (tree.redirect) {
    const target = tree.redirect;
    const link = flowButton(t("story.flow.blocked", { from: tree.ending.name, to: target.name }), "flow-redirect", () => {
      // 접힌 다른 길도 목적지를 찾아 펼친다. 진행 선택은 그대로 둔다.
      function expand(branch) {
        if (branch.ending?.id === target.id && !branch.redirect) { storyFlow.expanded.add(branch.path + "/branch"); return true; }
        return branch.nodes.filter((n) => n.type === "fork").some((n) => n.branches.some((b) => {
          if (!expand(b.tree)) return false;
          storyFlow.expanded.add(b.tree.path + "/branch"); return true;
        }));
      }
      expand(model.tree);
      renderStoryEndings();
      const chart = [...document.querySelectorAll(".story-flow")].find((n) => n.dataset.chapterId === model.ch.id);
      const dest = [...chart.querySelectorAll("[data-ending-id]")].find((n) => n.dataset.endingId === target.id);
      dest?.scrollIntoView({ block: "center", behavior: "auto" });
      dest?.querySelector("button")?.focus({ preventScroll: true });
    });
    link.dataset.redirectTo = target.id;
    lane.append(link);
  }
  return lane;
}

function flowBranchWidth(model, tree, onPath) {
  const fork = tree.nodes.find((n) => n.type === "fork");
  if (!fork) return 1;
  const picked = onPath && model.active[fork.sec.choice.id];
  return fork.branches.reduce((width, b) => {
    const selected = picked === b.option.id || (b.tree.ending && model.ending?.id === b.tree.ending.id && !b.tree.redirect);
    return width + flowBranchWidth(model, b.tree, onPath && !!selected);
  }, 0) || 1;
}

function renderStoryEndings() {
  const host = document.getElementById("story-endings");
  if (!story.data || !host) return;
  const focusKey = host.contains(document.activeElement) ? document.activeElement.dataset.flowKey : null;
  host.replaceChildren();
  story.data.chapters.filter((ch) => Array.isArray(ch.endings) && ch.endings.length).forEach((ch) => {
    const model = storyFlowModel(ch);
    const chart = el("article", "story-flow");
    chart.dataset.chapterId = ch.id;
    chart.append(el("h2", "", ch.name));
    const banner = el("p", "flow-banner", model.banner);
    banner.setAttribute("role", "status");
    chart.append(banner);
    const caseChoice = story.data.chapters.find((c) => c.id === "falling-skies")?.sections.find((s) => s.choice?.id === "case")?.choice;
    const caseLabel = caseChoice?.options.find((o) => o.id === story.pos["falling-skies@case"])?.label;
    chart.append(el("p", "flow-case", t("story.flow.case", { label: caseLabel || t("story.flow.unknown") })));
    if (model.finished.size) chart.append(flowToggle(ch.id + "/finished",
      t("story.flow.finished", { n: model.finished.size }), "flow-node flow-finished done", storyFlow.expanded));
    chart.append(flowTree(model, model.tree, true));
    if (model.common) {
      const steps = model.common.steps.filter((st) => itemVisible(model.active, st) && isRequiredStep(st));
      chart.append(flowSection(model, { sec: model.common, index: ch.sections.indexOf(model.common), steps }, ch.id + "/common", !model.visible.pending, true));
    }
    if (ch.endings_note) chart.append(el("p", "flow-note", ch.endings_note));
    host.append(chart);
  });
  if (!host.childElementCount) host.append(el("p", "empty", t("story.flow.empty")));
  if (focusKey) [...host.querySelectorAll("[data-flow-key]")].find((n) => n.dataset.flowKey === focusKey)?.focus({ preventScroll: true });
  drawFlowRedirects();
}

// 열 높이가 달라도 실제 목적지까지 이어지는 선. 관찰자 하나만 사용하여 오래 켜도 누적되지 않게 한다.
function drawFlowRedirects() {
  document.querySelectorAll(".story-flow").forEach((chart) => {
    chart.querySelector(".flow-links")?.remove();
    if (!chart.getClientRects().length) return;
    const rect = chart.getBoundingClientRect();
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.classList.add("flow-links");
    svg.setAttribute("aria-hidden", "true");
    chart.querySelectorAll("[data-redirect-to]").forEach((source) => {
      const target = [...chart.querySelectorAll("[data-ending-id]")].find((n) => n.dataset.endingId === source.dataset.redirectTo);
      if (!target) return;
      const a = source.getBoundingClientRect(), b = target.getBoundingClientRect();
      const line = document.createElementNS(svg.namespaceURI, "path");
      const x = a.left + a.width / 2 - rect.left, y = a.bottom - rect.top;
      const tx = b.left + b.width / 2 - rect.left, ty = b.bottom - rect.top;
      const bottom = Math.max(y, ty) + 8;
      line.setAttribute("d", "M " + x + " " + y + " V " + bottom + " H " + tx + " V " + ty);
      svg.append(line);
    });
    chart.append(svg);
  });
}
const flowResizeObserver = new ResizeObserver(drawFlowRedirects);
flowResizeObserver.observe(document.getElementById("story-endings"));
document.addEventListener("story-changed", renderStoryEndings);

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
