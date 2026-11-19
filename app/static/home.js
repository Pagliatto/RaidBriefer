// 홈 탭: 레이드 전에 한 번 보는 요약
// - 어디 갈까? : 맵마다 진행 중 퀘스트 수 + 스토리 다음 단계 수
// - 고른 맵    : 챙길 것(열쇠·가져가기·무기·착용) 모아 보기, 퀘스트·스토리 목록
// - 진행 중인 스토리 : 챕터별 진행률과 다음 맵
// state(app.js), story / allSteps / doneIndex(story.js), el / PREP_CLASS(app.js) 를 같이 씀

const home = { map: null };  // 고른 맵 이름 (null = 접힘, 처음에는 접혀 있음)

// 챕터별 "다음에 할 단계"와 그 맵 (홈에는 시작한 챕터만: 단계를 하나라도 체크한 것)
function storyNext(includeNotStarted = false) {
  if (!story.data) return [];
  return story.data.chapters.filter((ch) => includeNotStarted || doneIndex(ch) >= 0).map((ch) => {
    const { steps, pending } = visibleItems(ch);
    const last = doneIndex(ch);
    const next = steps[last + 1] || null;
    let section = null;
    if (next) section = ch.sections.find((s) => (s.steps || []).includes(next));
    return {
      ch, done: last + 1, total: steps.length, next, section,
      pending: next ? null : pending,  // 단계는 다 했고 갈림길 선택을 기다리는 중
      // 다음 단계의 맵 (여러 개일 수 있음, 없으면 구간의 맵)
      maps: next ? ((next.map_names && next.map_names.length) ? next.map_names : ((section && section.map_names) || [])) : [],
      map: next ? (next.map_name || (section && section.map_name)) : null,
    };
  });
}

// 맵 이름 → { quests: [...], stories: [...] }
function collectByMap() {
  const byMap = new Map();
  const slot = (name) => {
    if (!byMap.has(name)) byMap.set(name, { quests: [], stories: [] });
    return byMap.get(name);
  };
  if (state.data) {
    activeQuests().forEach((q) => q.maps.forEach((m) => {  // 숨긴(안 할) 퀘스트는 빼고
      if (!NOT_A_MAP.includes(m)) slot(m).quests.push(q);  // "모든 맵"·"레이드 밖"은 맵 카드에서 뺌
    }));
  }
  storyNext().forEach((s) => s.maps.forEach((m) => slot(m).stories.push(s)));
  // 할 일이 많은 맵이 앞으로
  return [...byMap.entries()].sort((a, b) =>
    (b[1].quests.length + b[1].stories.length) - (a[1].quests.length + a[1].stories.length));
}

// 고른 맵에서 챙길 것: 퀘스트 준비물 + 스토리 구간 준비물 (제출은 빼고 레이드에 들고 갈 것만)
function gatherPrep(info, mapName) {
  const merged = new Map();
  // from: 이 준비물이 필요한 곳 (누르면 그 퀘스트·챕터로 이동)
  const add = (p, from) => {
    if (p.kind === "제출") return;
    // 열쇠는 그 맵에서 필요한 것만 (여러 맵에 걸친 퀘스트의 다른 맵 열쇠는 빼기)
    if (p.kind === "열쇠" && mapName && p.maps && p.maps.length && !p.maps.includes(mapName)) return;
    const key = `${p.kind}|${p.text}`;
    const cur = merged.get(key);
    if (cur) {
      if (p.count) cur.count = (cur.count || 1) + p.count;
      if (!cur.from.some((f) => f.id === from.id)) cur.from.push(from);
    } else {
      merged.set(key, { ...p, from: [from] });
    }
  };
  const fromQuest = (q) => ({ type: "quest", id: q.id, name: q.name });
  info.quests.forEach((q) => (q.prep || []).forEach((p) => add(p, fromQuest(q))));
  anywhereQuests(activeQuests()).forEach((q) => (q.prep || []).forEach((p) => add(p, fromQuest(q))));  // 어느 맵에서나 퀘스트 준비물도
  info.stories.forEach((s) => (s.section ? visiblePrep(s.section) : []).forEach((p) => add(p, { type: "story", id: s.ch.id, name: s.ch.name })));
  const order = { "열쇠": 0, "가져가기": 1, "무기": 2, "착용": 3 };
  return [...merged.values()].sort((a, b) => order[a.kind] - order[b.kind]);
}

function renderHomeMaps(entries) {
  const box = document.getElementById("home-maps");
  box.replaceChildren();
  if (!entries.length) {
    box.append(el("p", "empty", t("home.noActiveQuests")));
    return;
  }
  entries.forEach(([name, info]) => {
    const card = el("button", "home-map" + (home.map === name ? " active" : ""));
    card.type = "button";
    card.append(el("span", "home-map-name", name));
    const counts = el("span", "home-map-count");
    counts.textContent = t("home.mapQuestCount", { n: info.quests.length }) + (info.stories.length ? t("home.mapStoryCount", { n: info.stories.length }) : "");
    card.append(counts);
    card.setAttribute("aria-expanded", home.map === name);
    // 같은 맵을 한 번 더 누르면 접기
    card.addEventListener("click", () => { home.map = home.map === name ? null : name; renderHome(); });
    box.append(card);
  });
}

function renderHomeDetail(name, info) {
  const box = document.getElementById("home-detail");
  box.replaceChildren();
  if (!name) return;

  box.append(el("h3", "home-detail-title", t("home.mapTodo", { name })));

  // 챙길 것
  const prep = gatherPrep(info, name);
  box.append(el("p", "home-label", t("home.prepLabel")));
  if (prep.length) {
    // 분류별로 한 줄씩 (열쇠 / 가져가기 / 무기 / 착용): 분류 이름은 줄 앞에 한 번만
    const groups = new Map();
    prep.forEach((p) => {
      if (!groups.has(p.kind)) groups.set(p.kind, []);
      groups.get(p.kind).push(p);
    });
    const rows = el("div", "prep-groups");
    groups.forEach((list, kind) => {
    const line = el("div", "prep-group");
    line.append(el("span", `prep-group-label kind-${PREP_CLASS[kind] || "etc"}`, t(`prepKind.${PREP_CLASS[kind] || "etc"}`)));
    const row = el("div", "prep");
    list.forEach((p) => {
      // 누르면 이 준비물이 필요한 퀘스트(여러 개면 모두 반짝임)·챕터로 이동
      const chip = el("button", `prep-item kind-${PREP_CLASS[p.kind] || "etc"} go-source`);
      chip.type = "button";
      chip.title = t("home.prepNeededBy", { list: p.from.map((f) => f.name).join(", ") });
      chip.append(document.createTextNode(p.text + (p.count ? ` ×${p.count.toLocaleString()}` : "")));
      if (p.any_of) chip.append(el("span", "key-any-of", ` (${t("quest.keyAnyOf")})`));
      chip.addEventListener("click", () => goToSource(p.from));
      appendPrepWithWiki(row, chip, p.keys, true);
    });
    line.append(row);
    rows.append(line);
    });
    box.append(rows);
  } else {
    box.append(el("p", "home-none", t("home.prepNone")));
  }

  // 스토리 다음 단계
  if (info.stories.length) {
    box.append(el("p", "home-label", t("home.storyLabel")));
    const ul = el("ul", "home-list");
    info.stories.forEach((s) => {
      const li = el("li", "go-source");
      li.title = t("home.storyChapterHint");
      li.append(el("b", "", s.ch.name), document.createTextNode(` — ${s.next.ko}`));
      li.addEventListener("click", () => goToSource([{ type: "story", id: s.ch.id }]));
      ul.append(li);
    });
    box.append(ul);
  }

  // 퀘스트
  box.append(el("p", "home-label", t("home.questCount", { n: info.quests.length })));
  const ul = el("ul", "home-list home-quests"); // 퀘스트 목록만 2열
  info.quests.forEach((q) => {
    const li = el("li", `trader-${q.trader_key || "etc"} go-source`);
    li.title = t("home.questDetailHint");
    li.addEventListener("click", () => goToQuests([q.id]));
    li.append(el("span", "home-quest-name", q.name), ...(q.name_en ? [el("span", "quest-name-en", q.name_en)] : []), el("span", "quest-trader", q.trader));
    if (q.night_maps && q.night_maps.includes(name)) li.append(mapTag(name, q.night_maps));
    ul.append(li);
  });
  box.append(ul);

  // 어느 맵에서나 할 수 있는 레이드 퀘스트 (준비물은 위 "챙길 것"에 같이 들어감)
  const anywhere = anywhereQuests(activeQuests());
  if (anywhere.length) {
    box.append(el("p", "home-label", t("home.anywhereCount", { label: mapLabel(ALL_MAPS), n: anywhere.length })));
    const ul2 = el("ul", "home-list home-quests");
    anywhere.forEach((q) => {
      const li = el("li", `trader-${q.trader_key || "etc"} go-source`);
      li.title = t("home.questDetailHint");
      li.addEventListener("click", () => goToQuests([q.id]));
      li.append(el("span", "home-quest-name", q.name), ...(q.name_en ? [el("span", "quest-name-en", q.name_en)] : []), el("span", "quest-trader", q.trader));
      ul2.append(li);
    });
    box.append(ul2);
  }
}

function renderHomeStory(list) {
  const box = document.getElementById("home-story");
  box.replaceChildren();
  if (!story.data) {
    box.append(el("p", "empty", t("common.loading")));
    return;
  }
  // 아직 시작 안 한 챕터는 한 줄로만 (스토리 탭에서 첫 단계를 체크하면 여기 나옴)
  const notStarted = story.data.chapters.length - list.length;
  if (!list.length) box.append(el("p", "home-none", t("home.noStory")));
  list.forEach((s) => {
    // 진행도 없이 이름 + 다음 할 일 + 맵만 짧게
    const row = el("button", "home-story-row" + (s.next || s.pending ? "" : " finished"));
    row.type = "button";
    row.title = t("home.storyRowHint");
    row.append(el("span", "home-story-name", s.ch.name));
    const next = el("span", "home-story-next");
    if (s.next) {
      next.textContent = s.section ? s.section.title : s.next.ko;
      if (s.map) next.append(el("span", "tag", s.map));
    } else if (s.pending) {
      next.append(el("span", "chapter-choice", t("home.storyChoice", { title: s.pending.title })));
    } else {
      next.textContent = t("home.storyDone");
    }
    row.append(next);
    row.addEventListener("click", () => {
      story.open.add(s.ch.id);
      renderStory();
      goTab("story");
    });
    box.append(row);
  });
  if (notStarted > 0) {
    const more = el("button", "home-story-more", t("home.storyNotStarted", { n: notStarted }));
    more.type = "button";
    more.addEventListener("click", () => goTab("story"));
    box.append(more);
  }
}

// 모아야 할 것: 진행 중 퀘스트 + 스토리 지금 구간의 "제출" 중 아직 제출 체크 안 한 것
// 아직 제출 안 한 아이템 (퀘스트 + 진행 중 스토리). 홈 "뭘 모아 올까?"와 지도 서랍 "먹을 것"이 같이 씀
// 돈(루블·달러·유로)은 레이드에서 먹는 물건이 아니라서 모아 올 목록에서 뺌 (퀘스트·스토리·은신처 모두)
const MONEY_IDS = new Set(["5449016a4bdc2d6f028b456f", "5696686a4bdc2da3298b456a", "569668774bdc2da2298b4568"]);
const MONEY_TEXT = /^(루블|달러|유로|roubles?|dollars?|euros?)(\s|\(|-|$)|\b(RUB|USD|EUR)\b/i; // i18n-ok: 데이터의 아이템 이름과 비교
function isMoney(p) {
  return (p.ids || []).some((id) => MONEY_IDS.has(id)) || MONEY_TEXT.test(p.text || "");
}

function neededItems() {
  const merged = new Map();
  // from: 이 아이템이 필요한 곳 [{ type: "quest"|"story", id, name }]
  const add = (p, from) => {
    if (isMoney(p)) return;
    const key = `${p.text}|${p.fir}`;
    const cur = merged.get(key) || { ...p, count: 0, from: [], ids: [] };
    cur.count += p.count ?? 1; // 개수가 없으면 1개, 0이면 0 (은신처 재료가 여러 시설에 걸칠 때 개수는 한 번만)
    if (!cur.from.some((f) => f.id === from.id)) cur.from.push(from);
    (p.ids || []).forEach((id) => { if (!cur.ids.includes(id)) cur.ids.push(id); });
    merged.set(key, cur);
  };
  if (state.data) {
    activeQuests().forEach((q) => (q.prep || []).forEach((p) => {  // 숨긴(안 할) 퀘스트는 빼고
      if (p.kind === "제출" && !state.handed[`${q.id}|${p.text}`]) add(p, { type: "quest", id: q.id, name: q.name });
    }));
  }
  storyNext().forEach((s) => (s.section ? visiblePrep(s.section) : []).forEach((p) => {
    // 퀘스트 아이템(진행하며 그 자리에서 줍는 물건)은 모아 올 전리품이 아니라서 뺌
    if (p.kind === "제출" && !p.quest_item) add(p, { type: "story", id: s.ch.id, name: s.ch.name });
  }));
  // 은신처 다음 레벨 재료 ('모았음' 체크한 건 이미 빠져 있음). 인레이드가 아니어도 전부 (사용자 결정)
  const hideoutTotals = (typeof hideout !== "undefined" && hideout.data && hideout.data.totals) || [];
  hideoutTotals.forEach((tot) => {
    const p = { kind: "제출", text: tot.name, count: tot.count, fir: tot.fir, ids: [tot.id] };
    tot.from.forEach((station, i) => add({ ...p, count: i === 0 ? tot.count : 0 }, { type: "hideout", id: `hideout:${station}`, name: t("home.hideoutSource", { station }) }));
  });
  return [...merged.values()];
}

function renderHomeCollect() {
  const box = document.getElementById("home-collect");
  box.replaceChildren();
  const items = neededItems();
  if (!items.length) {
    box.append(el("p", "home-none", t("home.nothingToCollect")));
    return;
  }
  // 인레이드(레이드에서 직접 찾아야 함) / 아무거나(사거나 가진 걸로 가능)
  [[t("home.groupFir"), items.filter((i) => i.fir)], [t("home.groupAny"), items.filter((i) => !i.fir)]]
    .forEach(([title, list]) => {
      if (!list.length) return;
      box.append(el("p", "home-label", t("home.groupCount", { title, n: list.length })));
      // 아이콘 + 개수로 짧게 (은신처 재료까지 넣으면 글자 목록은 화면 한 장이 넘음). 이름은 마우스를 올리면
      const row = el("div", "collect-icons");
      list.sort((a, b) => b.count - a.count).forEach((p) => {
        const chip = el("button", "collect-icon");
        chip.type = "button";
        chip.title = `${p.text}${p.count > 1 ? ` ×${p.count.toLocaleString()}` : ""}\n` +
          t("home.collectNeededBy", { list: p.from.map((f) => f.name).join(", ") });
        chip.addEventListener("click", () => goToSource(p.from));
        const id = (p.ids || [])[0];
        const short = p.text.split(" / ")[0].replace(/\s*\([^)]*\)/g, "").trim();
        if (id) {
          const img = el("img");
          img.src = `https://assets.tarkov.dev/${id}-icon.webp`;
          img.alt = short;
          img.onerror = () => img.replaceWith(el("span", "collect-text", short));
          chip.append(img);
        } else {
          chip.append(el("span", "collect-text", short)); // 아이콘을 모르면(아무 무기 등) 짧은 이름
        }
        if (p.count > 1) chip.append(el("b", "collect-count", `×${p.count.toLocaleString()}`));
        if (p.from.some((f) => f.type === "hideout")) chip.append(el("i", "collect-ho", t("home.hideoutBadge"))); // 은신처 재료 표시
        row.append(chip);
      });
      box.append(row);
    });
}

// 아이템을 누르면: 퀘스트에 필요하면 퀘스트 탭의 그 퀘스트들로, 스토리만이면 그 챕터로
function goToSource(from) {
  const questIds = from.filter((f) => f.type === "quest").map((f) => f.id);
  if (questIds.length) {
    goToQuests(questIds);
    return;
  }
  // 은신처 재료 → 은신처 탭의 그 시설 카드
  const stations = from.filter((f) => f.type === "hideout").map((f) => f.id.replace(/^hideout:/, ""));
  if (stations.length) {
    goToStations(stations);
    return;
  }
  const chapterId = from[0] && from[0].id;
  if (!chapterId) return;
  story.open.add(chapterId);
  renderStory();
  goTab("story");
  const card = [...document.querySelectorAll(".chapter")].find((c) => c.dataset.chapterId === chapterId);
  if (card) setTimeout(() => { card.scrollIntoView({ block: "start" }); rememberScroll(); }, 30);
}

function renderHome() {
  renderHomeCollect();
  const entries = collectByMap();
  // 고른 맵이 목록에서 사라졌으면 접기
  if (home.map && !entries.some(([name]) => name === home.map)) home.map = null;
  renderHomeMaps(entries);
  const picked = entries.find(([name]) => name === home.map);
  renderHomeDetail(home.map, picked ? picked[1] : null);
  renderHomeStory(storyNext());
}

document.addEventListener("quests-changed", renderHome);
document.addEventListener("hideout-changed", renderHome); // 은신처 체크·건설 완료하면 '뭘 모아 올까?'도 다시
document.addEventListener("story-changed", renderHome);
