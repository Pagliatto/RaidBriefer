// 홈 탭: 레이드 전에 한 번 보는 요약
// - 어디 갈까? : 맵마다 진행 중 퀘스트 수 + 스토리 다음 단계 수
// - 고른 맵    : 챙길 것(열쇠·가져가기·무기·착용) 모아 보기, 퀘스트·스토리 목록
// - 진행 중인 스토리 : 챕터별 진행률과 다음 맵
// state(app.js), story / visibleItems / 체크 판정(story.js), el / PREP_CLASS(app.js) 를 같이 씀

const home = { map: null };  // 고른 맵 이름 (null = 접힘, 처음에는 접혀 있음)

// 챕터별 첫 미완료 필수 단계 (홈에는 단계 체크나 갈림길 선택으로 시작한 챕터만)
function storyNext(includeNotStarted = false) {
  if (!story.data) return [];
  return story.data.chapters.filter((ch) => includeNotStarted || isChapterStarted(ch)).map((ch) => {
    const { items, steps, pending } = visibleItems(ch);
    const required = steps.filter(isRequiredStep);
    const next = required.find((s) => !isStepDone(s)) || null;
    const unfinished = items.filter((it) => it.type === "section" && !isSectionDone(it));
    const section = next ? unfinished.find((it) => it.steps.includes(next)).sec : null;
    // 첫 미완료 구간 + 따로 시작해 둔 미완료 구간의 필수 단계 맵을 모은다.
    const mapSteps = unfinished.filter((it, index) => index === 0 || isSectionStarted(it))
      .flatMap((it) => it.steps.filter((s) => isRequiredStep(s) && !isStepDone(s)).map((step) => ({
        step, section: it.sec,
        maps: step.map_names && step.map_names.length ? step.map_names : (it.sec.map_names || []),
      })));
    return {
      ch, done: required.filter(isStepDone).length, total: required.length, next, section,
      pending: next ? null : pending,
      maps: [...new Set(mapSteps.flatMap((entry) => entry.maps))],
      mapSteps,
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
  storyNext().forEach((s) => s.maps.forEach((m) => {
    const entry = s.mapSteps.find((candidate) => candidate.maps.includes(m));
    slot(m).stories.push({ ...s, next: entry.step });
  }));
  // 할 일이 많은 맵이 앞으로
  return [...byMap.entries()].sort((a, b) =>
    (b[1].quests.length + b[1].stories.length) - (a[1].quests.length + a[1].stories.length));
}

// 고른 맵에서 챙길 것: 퀘스트 준비물 + 스토리 구간 준비물 (제출은 빼고 레이드에 들고 갈 것만)
// only: 퀘스트 id 모음이면 그 퀘스트만 ('이번 판' 고르기). 스토리는 그대로
function gatherPrep(info, mapName, only = null) {
  const merged = new Map();
  // from: 이 준비물이 필요한 곳 (누르면 그 퀘스트·챕터로 이동)
  const add = (p, from) => {
    if (p.kind === "제출") return;
    // 열쇠는 그 맵에서 필요한 것만 (여러 맵에 걸친 퀘스트의 다른 맵 열쇠는 빼기)
    if (p.kind === "열쇠" && mapName && p.maps && p.maps.length && !p.maps.includes(mapName)) return;
    const key = `${p.kind}|${p.text}`;
    const src = `${from.type}:${from.id}`;
    const cur = merged.get(key);
    if (cur) {
      if (p.count) cur.count = (cur.count || 1) + p.count;
      if (!cur.from.some((f) => f.type === from.type && f.id === from.id)) cur.from.push(from);
    } else {
      merged.set(key, { ...p, from: [from], per: new Map() });
    }
    // per: 퀘스트·챕터마다 몇 개 필요한지 (퀘스트별 줄에 그 퀘스트 몫만 보여 주려고)
    const per = merged.get(key).per;
    per.set(src, (per.get(src) || 0) + (p.count || 1));
  };
  // nightMaps: 밤에만 되는 맵 표시를 이름 옆에 붙이려고 (예전 퀘스트 목록에 있던 것)
  const fromQuest = (q) => questSource(q);
  const keep = (q) => !only || only.has(q.id);
  info.quests.filter(keep).forEach((q) => (q.prep || []).forEach((p) => add(p, fromQuest(q))));
  // 실제 맵과 '모든 맵'에 같이 걸린 퀘스트는 위에서 이미 더했으니 빼기 (개수 두 번 세지 않게)
  anywhereQuests(activeQuests()).filter((q) => !info.quests.includes(q) && keep(q))
    .forEach((q) => (q.prep || []).forEach((p) => add(p, questSource(q, true))));  // 어느 맵에서나 퀘스트 준비물도
  info.stories.forEach((s) => (s.section ? visiblePrep(s.section) : []).forEach((p) => add(p, { type: "story", id: s.ch.id, name: s.ch.name })));
  const order = { "열쇠": 0, "가져가기": 1, "무기": 2, "착용": 3 };
  return [...merged.values()].sort((a, b) => order[a.kind] - order[b.kind]);
}

// 준비물을 '퀘스트(챕터)별 줄'로 나누기
//  - shared: 여러 곳에 필요한 물건 (합계를 한 줄에, 무기·착용은 빼고)
//  - carry: 그냥 들고 가면 되는 퀘스트 (열쇠·지참만)
//  - pick: 무기·착용 조건이 있는 퀘스트 → 그 퀘스트 준비물 전부를 한 줄에 (장비 맞추기)
const GEAR_KINDS = ["무기", "착용"];
function planPrep(prep) {
  const sources = new Map();  // "quest:id" → { from, items: [{ p, count }] }
  prep.forEach((p) => p.from.forEach((f) => {
    const key = `${f.type}:${f.id}`;
    if (!sources.has(key)) sources.set(key, { from: f, items: [] });
    sources.get(key).items.push({ p, count: p.per.get(key) || 1 });
  }));
  const pick = [...sources.values()].filter((s) => s.items.some((it) => GEAR_KINDS.includes(it.p.kind)));
  const isShared = (p) => p.from.length > 1 && !GEAR_KINDS.includes(p.kind);
  const shared = prep.filter(isShared);
  const carry = [...sources.values()]
    .filter((s) => !pick.includes(s))
    .map((s) => ({ ...s, items: s.items.filter((it) => !isShared(it.p)) }));
  // 준비물이 모두 '여러 퀘스트 공통' 줄에 있는 퀘스트도 이름 줄은 남김 ('이번 판' 버튼을 누를 곳, 2026-11-28)
  return { shared, carry, pick };
}

// 준비물 칩 하나: 분류 이름(색) + 이름 + 개수. 누르면 필요한 퀘스트·챕터로
function homePrepChip(parent, p, count, from, note) {
  const cls = PREP_CLASS[p.kind] || "etc";
  const chip = el("button", `prep-item kind-${cls} go-source`);
  chip.type = "button";
  chip.title = t("home.prepNeededBy", { list: from.map((f) => f.name).join(", ") });
  chip.append(el("b", "", t(`prepKind.${cls}`)), document.createTextNode(" " + p.text + (count > 1 ? ` ×${count.toLocaleString()}` : "")));
  if (p.any_of) chip.append(el("span", "key-any-of", ` (${t("quest.keyAnyOf")})`));
  if (note) chip.append(note);
  chip.addEventListener("click", () => goToSource(from));
  appendPrepWithWiki(parent, chip, p.keys, true);
}

// 퀘스트 → 이름표 정보 (상인 색 띠·밤 표시·'모든 맵' 표시에 씀)
function questSource(q, anywhere = false) {
  return { type: "quest", id: q.id, name: q.name, trader: q.trader, traderKey: q.trader_key, nightMaps: q.night_maps, anywhere };
}

// 상인 순서 (게임 상인 화면 순서): 같은 색 띠끼리 붙어 보이게. 스토리·모르는 상인은 맨 뒤
const TRADER_ORDER = ["prapor", "therapist", "fence", "skier", "peacekeeper", "mechanic", "ragman", "jaeger", "ref", "lightkeeper", "btr-driver"];
function byTrader(a, b) {
  const rank = (f) => {
    const i = f.type === "quest" ? TRADER_ORDER.indexOf(f.traderKey) : -1;
    return i < 0 ? TRADER_ORDER.length + (f.type === "story" ? 1 : 0) : i;
  };
  return rank(a) - rank(b);
}

// '할 일' 칸의 묶음 제목: 아이콘 + 이름 + 개수 + 오른쪽 끝까지 가는 선 (다 챙기기·장비 맞추기·바로 출발·스토리 모두 같은 모양)
function homeHead(iconName, label, count, hint) {
  const head = el("p", "home-prep-head");
  head.append(icon(iconName, 14), el("span", "", label), el("span", "home-prep-head-count", String(count)));
  if (hint) head.title = hint;  // 설명은 마우스를 올리면 (제목 형식을 셋 다 같게)
  return head;
}

// 퀘스트·챕터 이름표: 퀘스트 탭·예전 목록과 같은 모양 (왼쪽 상인 색 띠 + 이름 + 상인 이름). 누르면 그 퀘스트·챕터로
function homePrepSource(from, mapName) {
  const name = el("button", `home-prep-src trader-${from.traderKey || "etc"} go-source`);
  name.type = "button";
  name.title = t(from.type === "story" ? "home.storyChapterHint" : "home.questDetailHint");
  name.append(el("span", "home-quest-name", from.name));
  if (from.type === "story") name.append(el("span", "home-prep-story", t("home.storyLabel")));
  else if (from.trader) name.append(el("span", "quest-trader", from.trader));
  if (from.nightMaps && from.nightMaps.includes(mapName)) name.append(mapTag(mapName, from.nightMaps));
  if (from.anywhere) name.append(el("span", "home-prep-any", mapLabel(ALL_MAPS)));  // 어느 맵에서나 하는 퀘스트
  name.addEventListener("click", () => goToSource([from]));
  return name;
}

// 준비물이 하나도 없는 퀘스트(바로 출발): 예전 퀘스트 목록처럼 2열 (모든 퀘스트가 화면에 한 번씩은 보이게)
function renderHomeNoPrep(box, sources, mapName) {
  if (!sources.length) return;
  box.append(homeHead("arrow-right", t("home.prepNoneLabel"), sources.length));
  const names = el("div", "home-prep-names");
  sources.sort(byTrader).forEach((f) => {
    const item = el("div", "home-prep-name" + (isPicked(f.id) ? " picked" : ""));
    item.append(homePrepSource(f, mapName), pickButton(f.id));
    names.append(item);
  });
  box.append(names);
}

// 퀘스트(챕터) 줄 하나: 이름표 + 그 몫의 준비물 칩 + (퀘스트면) '이번 판' 버튼. 고른 줄은 옅은 배경
function homePrepRow(s, mapName) {
  const line = el("div", "home-prep-row");
  const chips = el("div", "prep");
  s.items.forEach((it) => homePrepChip(chips, it.p, it.count, [s.from]));
  if (!s.items.length && !s.none) chips.append(el("span", "home-prep-shared-only", t("home.prepSharedOnly")));
  line.append(homePrepSource(s.from, mapName), chips);
  if (s.from.type === "quest") {
    line.append(pickButton(s.from.id));
    line.classList.toggle("picked", isPicked(s.from.id));
  }
  return line;
}

function renderHomePrep(box, prep, mapName) {
  const { shared, carry, pick } = planPrep(prep);
  carry.sort((a, b) => byTrader(a.from, b.from));
  pick.sort((a, b) => byTrader(a.from, b.from));
  if (shared.length || carry.length) {
    // 개수 = 이 묶음에 나오는 퀘스트·챕터 수 (공통 줄에만 걸린 것 포함)
    const ids = new Set([...carry.map((s) => `${s.from.type}:${s.from.id}`), ...shared.flatMap((p) => p.from.map((f) => `${f.type}:${f.id}`))]);
    box.append(homeHead("backpack", t("home.prepCarry"), ids.size));
    const rows = el("div", "home-prep-rows");
    if (shared.length) {
      const line = el("div", "home-prep-row shared");
      line.append(el("span", "home-prep-label", t("home.prepShared")));
      const chips = el("div", "prep");
      shared.forEach((p) => {
        const total = [...p.per.values()].reduce((a, b) => a + b, 0);
        // 어느 퀘스트들 몫인지 이름을 칩 안에 (이 퀘스트들은 아래 줄에 따로 안 나올 수 있어서 밤·모든 맵 표시도 같이)
        const note = el("span", "home-prep-note");
        p.from.forEach((f, i) => {
          if (i) note.append(" · ");
          note.append(f.name);
          if (f.nightMaps && f.nightMaps.includes(mapName)) note.append(" ", mapTag(mapName, f.nightMaps));
          if (f.anywhere) note.append(el("span", "home-prep-any", mapLabel(ALL_MAPS)));
        });
        homePrepChip(chips, p, total, p.from, note);
      });
      line.append(chips);
      rows.append(line);
    }
    carry.forEach((s) => rows.append(homePrepRow(s, mapName)));
    box.append(rows);
  }
  if (pick.length) {
    box.append(homeHead("shield", t("home.prepPick"), pick.length, t("home.prepPickHint")));
    // '다 챙기기'와 같은 줄 모양, 묶음 전체에만 옅은 배경 (상자 안 상자로 답답해 보이지 않게)
    const rows = el("div", "home-prep-rows pick");
    pick.forEach((s) => rows.append(homePrepRow(s, mapName)));
    box.append(rows);
  }
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
    card.dataset.mapName = name;
    const mark = el("span", "home-raid-mark");
    mark.hidden = true;
    mark.setAttribute("aria-hidden", "true");
    mark.append(el("i"), el("i", "home-ripple"), el("i", "home-ripple second"));
    card.append(mark);
    const title = el("span", "home-map-name");
    const dot = el("i", "home-map-dot");
    dot.setAttribute("aria-hidden", "true");
    applyMapColor(dot, name);
    title.append(dot, document.createTextNode(name));
    card.append(title);
    const counts = el("span", "home-map-count");
    counts.textContent = t("home.mapQuestCount", { n: info.quests.length }) + (info.stories.length ? t("home.mapStoryCount", { n: info.stories.length }) : "");
    card.append(counts);
    // 이 맵에서 '이번 판'으로 고른 퀘스트 수 (맵 카드를 바꿔 가며 골라도 어디에 골랐는지 보이게)
    const picked = info.quests.filter((q) => isPicked(q.id)).length;
    if (picked) {
      const badge = el("span", "home-map-picked");
      badge.title = t("pick.cardHint", { n: picked });
      badge.append(icon("check", 11), document.createTextNode(String(picked)));
      title.append(badge);
    }
    card.setAttribute("aria-expanded", home.map === name);
    // 같은 맵을 한 번 더 누르면 접기
    card.addEventListener("click", () => {
      home.map = home.map === name ? null : name;
      renderHome();
      // 실제 선택 때 제목만 한 번 나타내고, 자동 갱신에는 재생하지 않는다.
      const title = document.querySelector(".home-detail-title");
      if (home.map) playActionEffect(title, "home-title-appear");
    });
    box.append(card);
  });
  updateHomeRaidMarks();
}

function renderHomeDetail(name, info) {
  const box = document.getElementById("home-detail");
  box.replaceChildren();
  if (!name) return;

  // '이번 판' 고르기: 이 맵의 퀘스트를 하나라도 골랐으면 고른 것만 (어느 맵에서나 퀘스트만 고른 건 세지 않음,
  // 다른 맵에 들어가 지도가 텅 비지 않게 하는 규칙과 같음). 안 골랐으면 지금과 같고 줄마다 버튼만 붙음
  const anywhere = anywhereQuests(activeQuests()).filter((q) => !info.quests.includes(q));
  const mapQuests = [...info.quests, ...anywhere];
  const pickMode = info.quests.some((q) => isPicked(q.id));
  const only = pickMode ? new Set(mapQuests.filter((q) => isPicked(q.id)).map((q) => q.id)) : null;

  const head = el("div", "home-detail-head");
  head.append(el("h3", "home-detail-title", t("home.mapTodo", { name })));
  if (raidPick.quests.length) {
    const clear = el("button", "home-pick-clear", t("pick.clearAll"));
    clear.type = "button";
    clear.addEventListener("click", () => saveRaidPick({ clear: true }));
    head.append(clear);
  }
  box.append(head);
  // 안내 줄: 레이드가 끝나 풀렸으면 그 말을 (다음에 고를 때까지), 아니면 지금 상태
  const others = raidPick.quests.length - (only ? only.size : 0);
  const hint = raidPick.cleared && !raidPick.quests.length ? t("pick.hintCleared")
    : pickMode ? t("pick.hintPicked", { n: only.size })
    : raidPick.quests.length ? t("pick.hintOtherMap", { n: others })
    : t("pick.hintNone");
  box.append(el("p", "home-pick-hint" + (pickMode ? " on" : ""), hint));

  // 보스 등장 확률: 들어가기 전에 챙길 것과 같이 보도록 '할 일' 칸에만 (카드는 좁아서 잘림)
  const map = typeof mapUI !== "undefined" && mapUI.maps.find((m) => m.name === name);
  const bosses = (map && map.bosses) || [];
  if (bosses.length) {
    const line = el("div", "prep-group home-bosses");
    line.append(el("span", "prep-group-label home-bosses-label", t("home.bossLabel")));
    const row = el("div", "prep");
    bosses.forEach((boss) => {
      const chance = Math.round(boss.spawnChance * 1000) / 10;
      const tone = boss.spawnChance >= 1 ? "bad" : boss.spawnChance >= 0.5 ? "warn" : "dim";
      const chip = el("span", "prep-item home-boss");
      chip.append(el("span", "", boss.name), el("b", "boss-chance " + tone, chance + "%"));
      if (boss.members && boss.members.length) chip.title = boss.members.join(" · ");  // 군즈: 마우스를 올리면 세 명
      row.append(chip);
    });
    line.append(row);
    box.append(line);
  }

  // 챙길 것 ('준비물' 제목은 없음: 아래 묶음 제목들이 그 역할)
  const prep = gatherPrep(info, name, only);
  if (prep.length) {
    renderHomePrep(box, prep, name);
  } else {
    box.append(el("p", "home-none", t("home.prepNone")));
  }
  // 위 줄·카드에 한 번도 안 나온 퀘스트 (= 이 맵에서 챙길 게 없는 퀘스트)
  const shown = new Set(prep.flatMap((p) => p.from).filter((f) => f.type === "quest").map((f) => f.id));
  const noPrep = [...info.quests.map((q) => questSource(q)), ...anywhere.map((q) => questSource(q, true))]
    .filter((f) => !shown.has(f.id) && (!only || only.has(f.id)));
  renderHomeNoPrep(box, noPrep, name);

  // 스토리 다음 단계
  if (info.stories.length) {
    box.append(homeHead("book-open", t("home.storyLabel"), info.stories.length));
    // 위 묶음들과 같은 줄 모양: 왼쪽 챕터 이름표, 오른쪽 이 맵에서 할 다음 단계 (둘 다 누르면 스토리 탭의 그 챕터)
    const rows = el("div", "home-prep-rows");
    info.stories.forEach((s) => {
      const from = { type: "story", id: s.ch.id, name: s.ch.name };
      const line = el("div", "home-prep-row");
      const step = el("button", "home-prep-step go-source", s.next.ko);
      step.type = "button";
      step.title = t("home.storyChapterHint");
      step.addEventListener("click", () => goToSource([from]));
      line.append(homePrepSource(from, name), step);
      rows.append(line);
    });
    box.append(rows);
  }

  if (pickMode) renderHomeUnpicked(box, info, name, mapQuests.filter((q) => !only.has(q.id)), anywhere);
}

// 고르지 않은 퀘스트: 맨 아래 접힌 칸 (2026-11-28 사용자). 펼치면 흐린 줄 + 그 퀘스트 몫 준비물 + '＋ 이번 판'
const HOME_UNPICKED_KEY = "tarkovhelper.homeUnpickedOpen";
function renderHomeUnpicked(box, info, mapName, quests, anywhere) {
  if (!quests.length) return;
  let open = false;
  try { open = localStorage.getItem(HOME_UNPICKED_KEY) === "1"; } catch (error) { /* 저장소를 못 쓰면 접힘 */ }
  const head = el("button", "home-prep-head home-unpicked-head");
  head.type = "button";
  head.setAttribute("aria-expanded", open);
  head.append(icon(open ? "chevron-down" : "chevron-right", 14), el("span", "", t("pick.unpicked")), el("span", "home-prep-head-count", String(quests.length)));
  head.addEventListener("click", () => {
    try { localStorage.setItem(HOME_UNPICKED_KEY, open ? "0" : "1"); } catch (error) { /* 이번만 */ }
    renderHome();
  });
  box.append(head);
  if (!open) return;
  // 그 퀘스트 몫 준비물: 고르지 않은 퀘스트만 모아서 같은 규칙으로 (열쇠는 이 맵 것만, 제출 빼기)
  const prep = gatherPrep({ quests: info.quests, stories: [] }, mapName, new Set(quests.map((q) => q.id)));
  const rows = el("div", "home-prep-rows home-unpicked");
  quests.slice().map((q) => questSource(q, anywhere.includes(q))).sort(byTrader).forEach((from) => {
    const key = `quest:${from.id}`;
    const items = prep.filter((p) => p.per.has(key)).map((p) => ({ p, count: p.per.get(key) }));
    rows.append(homePrepRow({ from, items, none: !items.length }, mapName));
  });
  box.append(rows);
}

function renderHomeStory(list) {
  const box = document.getElementById("home-story");
  box.replaceChildren();
  if (!story.data) {
    box.append(el("p", "empty", t("common.loading")));
    return;
  }
  // 할 일이 남은 챕터만 보임. 다 끝낸 챕터는 숨기고 아래 한 줄에 개수만 (2026-11-27 사용자: 제목이 '진행 중'인데 완료가 섞여 있음)
  // 아직 시작 안 한 챕터도 한 줄로만 (스토리 탭에서 첫 단계를 체크하면 여기 나옴)
  const total = story.data.chapters.length;
  const active = list.filter((s) => s.next || s.pending);
  const done = list.length - active.length;
  const notStarted = total - list.length;
  if (total > 0 && done === total) {
    box.append(el("p", "home-none", t("home.storyAllDone"))); // 모든 챕터를 끝냄: 이 한 줄만
    return;
  }
  if (!active.length) box.append(el("p", "home-none", t(list.length ? "home.noActiveStory" : "home.noStory")));
  active.forEach((s) => {
    // 진행도 없이 이름 + 다음 할 일 + 맵만 짧게
    const row = el("button", "home-story-row");
    row.type = "button";
    row.title = t("home.storyRowHint");
    row.append(el("span", "home-story-name", s.ch.name));
    const next = el("span", "home-story-next");
    if (s.next) {
      next.textContent = s.next.ko;
      if (s.map) next.append(el("span", "tag", s.map));
    } else {
      next.append(el("span", "chapter-choice", t("home.storyChoice", { title: s.pending.title })));
    }
    row.append(next);
    row.addEventListener("click", () => openStoryChapter(s.ch.id));
    box.append(row);
  });
  if (notStarted > 0 || done > 0) {
    const key = notStarted > 0 && done > 0 ? "home.storyRest" : notStarted > 0 ? "home.storyNotStarted" : "home.storyFinished";
    const more = el("button", "home-story-more", t(key, { n: notStarted, done }));
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
      if (p.kind === "제출" && !state.handed[handedKey(q, p)]) add(p, { type: "quest", id: q.id, name: q.name });
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
document.addEventListener("raid-pick-changed", renderHome); // '이번 판' 고르기 (지도 서랍에서 고르거나 레이드가 끝나 풀려도)

function updateHomeRaidMarks() {
  const s = typeof topbar !== "undefined" ? topbar.state : {};
  const current = typeof mapUI !== "undefined" && mapUI.maps.find((m) => m.ids.includes(s.map_id));
  document.querySelectorAll(".home-map").forEach((card) => {
    const active = ["raid", "loading"].includes(s.status) && card.dataset.mapName === (current ? current.name : s.map_name);
    const mark = card.querySelector(".home-raid-mark");
    if (mark) mark.hidden = !active;
  });
}
window.addEventListener("game-state", updateHomeRaidMarks);
document.addEventListener("maps-changed", () => renderHomeMaps(collectByMap()));
