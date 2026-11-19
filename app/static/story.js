// 스토리 탭
// - 단계를 누르면 "여기까지 완료" → 그 위 단계는 전부 완료, 아래는 미완료
// - 끝난 구간은 한 줄로 접고, 지금 할 구간만 크게 펼침
// - 갈림길(choice): 선택지를 고르면 그 길의 구간만 보여줌 (The Ticket 등)
// el(), PREP_CLASS 는 app.js 에 있는 것을 같이 씀
//
// 저장하는 값 (story.pos)
//   "tour"                     : "tour-21"  ← 챕터별 마지막으로 끝낸 단계
//   "the-ticket@kerman"        : "accept"   ← 챕터@갈림길 : 고른 선택지
//   "the-ticket-j-17!"         : "done"     ← "해당될 때만" 단계를 직접 체크한 것
//
// 조건(when) 종류 — 구간, 단계, 준비물에 달 수 있음
//   "kerman": "accept"                      ← 이 챕터에서 고른 갈림길
//   "falling-skies@case": "kept"            ← 다른 챕터에서 고른 갈림길
//   "done:falling-skies": false             ← 그 챕터를 끝냈나 (스토리 체크 기록)
//   "quest:6744...,6745...": true           ← 그 퀘스트(들 중 하나)를 끝냈나 (게임 로그)
// 단계에 "manual": true → 미리 알 수 없는 조건이라 연쇄 체크에서 빼고 직접 체크

const story = {
  data: null,
  pos: {},
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
    const { steps, pending } = visibleItems(ch);
    return !pending && steps.length > 0 && doneIndex(ch) === steps.length - 1;
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
// 돌려주는 값: { items: [{type:"section", sec, steps, manual, start, end} | {type:"choice", choice, picked}], steps, pending }
//   steps  = 연쇄 체크("여기까지 완료")에 들어가는 단계
//   manual = "해당될 때만" 단계 (연쇄 체크에서 빠짐)
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
    items.push({ type: "section", sec, steps: normal, manual, start: steps.length, end: steps.length + normal.length - 1 });
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

// 몇 번째 단계까지 끝냈나 (-1 = 아직 없음, 저장된 단계가 지금 길에 없으면 -1)
function doneIndex(ch) {
  const id = story.pos[ch.id];
  return id ? allSteps(ch).findIndex((s) => s.id === id) : -1;
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

function renderStep(ch, step, index, last) {
  const done = index <= last;
  const li = el("li", "step" + (step.optional ? " optional" : "") + (done ? " done" : ""));
  const label = el("label");
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = done;
  box.title = done ? t("story.stepUndo") : t("story.stepDone");
  box.addEventListener("change", () => clickStep(ch, index, last));
  label.append(box, el("span", "step-text", step.ko));
  if (step.optional) label.append(el("span", "opt-mark", t("story.optional")));
  if (step.map_name) label.append(el("span", "tag", step.map_name));
  li.append(label);
  return li;
}

// "해당될 때만" 단계: 연쇄 체크와 상관없이 따로 체크 (미리 알 수 없는 조건)
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
  if (step.map_name) label.append(el("span", "tag", step.map_name));
  li.append(label);
  return li;
}

// 구간 내용 (팁·준비물·사진·단계·보상)
function sectionBody(ch, item, last) {
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
  item.steps.forEach((s, i) => ul.append(renderStep(ch, s, item.start + i, last)));
  item.manual.forEach((s) => ul.append(renderManualStep(s)));
  box.append(ul);
  if (sec.reward) box.append(el("p", "story-reward", t("story.reward", { reward: sec.reward })));
  return box;
}

function sectionHead(sec, extraClass) {
  const head = el("div", "story-section-head" + (extraClass ? " " + extraClass : ""));
  head.append(el("h3", "", sec.title));
  if (sec.map_name) head.append(el("span", "tag", sec.map_name));
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
  const last = doneIndex(ch);
  const doneCount = last + 1;

  // 지금 할 구간 (없으면: 안 고른 갈림길이 있거나, 챕터 완료)
  const current = items.find((it) => it.type === "section" && it.end > last) || null;
  const finishedAll = !current && !pending;

  const card = document.createElement("details");
  card.className = "chapter" + (finishedAll ? " finished" : "");
  card.dataset.chapterId = ch.id;
  card.open = story.open.has(ch.id);
  card.addEventListener("toggle", () => {
    if (card.open) story.open.add(ch.id); else story.open.delete(ch.id);
  });

  // 접혀 있어도 보이는 줄: 이름 · 진행률 · 다음 할 일
  const summary = el("summary");
  const top = el("div", "chapter-top");
  top.append(el("span", "chapter-name", ch.name));
  const bar = el("span", "chapter-bar");
  const fill = el("span");
  fill.style.width = steps.length ? `${Math.round((doneCount / steps.length) * 100)}%` : "0%";
  bar.append(fill);
  // 갈림길을 아직 안 고르면 뒤 단계 수를 모르니 "+" 표시
  top.append(bar, el("span", "chapter-count", `${doneCount}/${steps.length}${pending ? "+" : ""}`));
  summary.append(top);
  const next = el("div", "chapter-next");
  if (current) {
    next.append(document.createTextNode(t("story.next", { title: current.sec.title })));
    if (current.sec.map_name) next.append(el("span", "tag", current.sec.map_name));
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
  [[t("story.library"), ch.library], [t("story.wiki"), ch.wiki]].forEach(([text, href], i) => {
    if (!href) return;
    if (i) links.append(document.createTextNode(" · "));
    const a = el("a", "", text);
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener";
    links.append(a);
  });
  body.append(links);

  // 지금 위치 (구간이면 그 구간, 아니면 안 고른 갈림길) 앞/뒤로 나누기
  const curIdx = current ? items.indexOf(current) : items.length;
  const before = items.slice(0, curIdx);
  const after = items.slice(curIdx + 1);

  // ① 끝난 구간 + 이미 고른 갈림길: 한 줄로 모아서 접어 둠
  const finishedSecs = before.filter((it) => it.type === "section");
  if (before.length) {
    const group = document.createElement("details");
    group.className = "finished-group";
    const names = finishedSecs.length
      ? ` (${finishedSecs[0].sec.title} ~ ${finishedSecs[finishedSecs.length - 1].sec.title})` : "";
    const choicesMade = before.filter((it) => it.type === "choice" && it.picked).length;
    group.append(el("summary", "",
      t("story.finished", { n: finishedSecs.length, names }) + (choicesMade ? t("story.finishedChoices", { n: choicesMade }) : "")));
    before.forEach((it) => {
      if (it.type === "notice") return;  // 안내 카드는 접지 않고 아래에 따로 보여줌
      if (it.type === "choice") {
        if (it.picked) group.append(renderChoice(ch, it, false));
        return;
      }
      const box = el("div", "story-section");
      box.append(sectionHead(it.sec), sectionBody(ch, it, last));
      group.append(box);
    });
    if (before.some((it) => it.type !== "notice")) body.append(group);
    // 안내 카드(예: 구원자 길 막힘)는 항상 눈에 보이게
    before.filter((it) => it.type === "notice").forEach((it) => body.append(renderNotice(it)));
  }

  // ② 지금 할 구간: 크게 펼침
  if (current) {
    const box = el("div", "story-section current");
    box.append(el("p", "current-label", t("story.currentLabel")));
    box.append(sectionHead(current.sec), sectionBody(ch, current, last));
    body.append(box);
  }

  // ③ 남은 구간: 제목만, 누르면 펼침 / 갈림길은 카드로
  const rest = current ? after : [];
  rest.forEach((it) => {
    if (it.type === "choice") {
      body.append(renderChoice(ch, it, false));
      return;
    }
    if (it.type === "notice") {
      body.append(renderNotice(it));
      return;
    }
    const item = document.createElement("details");
    item.className = "story-section upcoming";
    const sum = el("summary");
    sum.append(sectionHead(it.sec));
    item.append(sum, sectionBody(ch, it, last));
    body.append(item);
  });

  // 지금 할 구간이 없고 안 고른 갈림길이 있으면 → 그 갈림길이 지금 할 일
  if (!current && pending) {
    const pendingItem = items[items.length - 1];
    body.append(renderChoice(ch, pendingItem, true));
  }

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

// 단계를 눌렀을 때: 그 단계까지 완료.
// 이미 끝낸 마지막 단계를 다시 누르면 한 단계 되돌림.
function clickStep(ch, index, last) {
  const steps = allSteps(ch);
  const target = index === last ? index - 1 : index;
  saveStory(ch.id, target >= 0 ? steps[target].id : null);
}

// 스토리 값 하나 저장 (단계 위치 또는 갈림길 선택). value 가 null 이면 지움.
async function saveStory(key, value) {
  const before = story.pos;
  story.pos = { ...story.pos };
  if (value) story.pos[key] = value; else delete story.pos[key];
  renderStory();
  try {
    const res = await fetch("/api/check/story", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, value }),
    });
    if (!res.ok) throw new Error();
    story.pos = await res.json();
  } catch (err) {
    story.pos = before;
    const notice = document.getElementById("story-notice");
    notice.textContent = t("story.saveFailed");
    notice.hidden = false;
  }
  renderStory();
}

async function loadStory() {
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
function openPhoto(im) {
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
  const img = box.querySelector("img");
  img.alt = im.caption;
  img.src = im.thumb; // 작은 그림을 먼저 보여주고
  const full = new Image();
  full.referrerPolicy = "no-referrer";
  full.onload = () => { if (box.classList.contains("open")) img.src = im.full; }; // 다 받으면 선명한 그림으로
  full.src = im.full;
  box.querySelector(".photo-caption").textContent = t("story.photoCaption", { caption: im.caption });
  box.classList.add("open");
}

function closePhoto() {
  const box = document.getElementById("photo-popup");
  if (box) box.classList.remove("open");
}
