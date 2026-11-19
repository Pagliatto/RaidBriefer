// 처음 설정 화면: 프로그램을 처음 켤 때만 뜸 (⚙ 설정의 [처음 설정 다시 하기]로 다시 열 수 있음)
// 1단계: 게임 설치 위치 (자동으로 찾고, 틀리면 직접 적기)
// 2단계: 진행 중인 퀘스트 (다음 작업에서 만듦)

const setupUI = {
  root: document.getElementById("setup"),
  get: (id) => document.getElementById(id),
};

function showSetupPage(step) {
  setupUI.root.querySelectorAll(".setup-card").forEach((card) => {
    card.hidden = card.dataset.page !== String(step);
  });
  setupUI.root.querySelectorAll(".setup-steps li").forEach((li) => {
    const n = Number(li.dataset.step);
    li.classList.toggle("current", n === step);
    li.classList.toggle("done", n < step);
  });
  window.scrollTo(0, 0);
}

// 설치 위치 확인 결과 그리기. ok: true(찾음) / false(못 찾음) / null(찾는 중)
function drawSetupFound(ok, title, path) {
  setupUI.get("setup-found").className = `setup-found ${ok === true ? "ok" : ok === false ? "bad" : ""}`;
  setupUI.get("setup-found-icon").textContent = ok === true ? "✔" : ok === false ? "!" : "…";
  setupUI.get("setup-found-title").textContent = title;
  setupUI.get("setup-found-path").textContent = path || "";
  setupUI.get("setup-next-1").disabled = ok !== true;
  // 못 찾았으면 직접 적는 칸을 펼쳐 둠
  if (ok === false) setupUI.get("setup-manual").open = true;
}

async function setupRequest(body) {
  const res = await fetch("/api/settings", {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  return { ok: res.ok, data: await res.json() };
}

// 1단계: 지금 저장된 위치가 맞으면 그대로, 아니면 모든 드라이브에서 자동으로 찾기
async function setupFindGame(force) {
  drawSetupFound(null, t("setup.finding"));
  try {
    if (!force) {
      const { data } = await setupRequest(null);
      if (data.check.ok) {
        setupUI.get("setup-game-dir").value = data.game_dir;
        drawSetupFound(true, t("setup.found", { message: data.check.message }), data.game_dir);
        return;
      }
    }
    const { ok, data } = await setupRequest({ find: true });
    if (ok) {
      setupUI.get("setup-game-dir").value = data.game_dir;
      drawSetupFound(true, t("setup.found", { message: data.check.message }), data.game_dir);
    } else {
      drawSetupFound(false, t("setup.notFound"), t("setup.notFoundHint"));
    }
  } catch (error) {
    drawSetupFound(false, t("setup.noServer"), "");
  }
}

async function setupCheckTyped() {
  const typed = setupUI.get("setup-game-dir").value;
  drawSetupFound(null, t("setup.checking"));
  try {
    const { ok, data } = await setupRequest({ game_dir: typed });
    if (ok) drawSetupFound(true, t("setup.found", { message: data.check.message }), data.game_dir);
    else drawSetupFound(false, data.error || t("setup.noLogs"), typed);
  } catch (error) {
    drawSetupFound(false, t("setup.noServer"), "");
  }
}

function openSetup() {
  document.body.classList.add("in-setup");
  setupUI.root.hidden = false;
  setupUI.fromGap = false;
  showSetupPage(1);
  setupFindGame(false);
}

async function finishSetup() {
  try {
    await setupRequest({ setup_done: true });
    // 2단계까지 확인했으면 '로그가 지워진 것 같아요' 단서들은 확인한 것으로 저장 (다시 안 띄움)
    const gap = setupQuests.data?.log_gap || [];
    if (gap.length) {
      await fetch("/api/log-gap/ok", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: gap }),
      });
    }
  } catch (error) {
    // 저장이 안 돼도 화면은 닫음 (다음에 켤 때 다시 뜸)
  }
  setupUI.root.hidden = true;
  document.body.classList.remove("in-setup");
  window.scrollTo(0, 0);
  // 위치가 바뀌었을 수 있으니 퀘스트·체크를 새로 불러옴
  window.dispatchEvent(new CustomEvent("settings-changed"));
}

setupUI.get("setup-next-1").addEventListener("click", () => openSetupQuests());
setupUI.get("setup-back-2").addEventListener("click", () => showSetupPage(1));
setupUI.get("setup-finish").addEventListener("click", () => afterQuestStep());
setupUI.get("setup-find").addEventListener("click", () => setupFindGame(true));
setupUI.get("setup-check").addEventListener("click", setupCheckTyped);
setupUI.get("setup-game-dir").addEventListener("keydown", (e) => {
  if (e.key === "Enter") setupCheckTyped();
});

// 켤 때: 처음 설정을 아직 안 했으면 띄움
(async () => {
  try {
    const { data } = await setupRequest(null);
    if (!data.setup_done) openSetup();
  } catch (error) {
    // 서버 연결이 안 되면 평소 화면 그대로 (상단바·퀘스트 쪽에서 오류 안내)
  }
})();

// ⚙ 설정 창의 [처음 설정 다시 하기]
document.getElementById("settings-setup").addEventListener("click", () => {
  document.getElementById("settings").close();
  openSetup();
});

// ---------- 2단계: 진행 중인 퀘스트 확인 + 빠진 것 추가 ----------
const setupQuests = {
  catalog: null,   // 고를 수 있는 전체 퀘스트 (/api/quest-catalog)
  data: null,      // 지금 퀘스트 상태 (/api/quests)
  trader: FILTER_ALL,  // 상인 거르기 (FILTER_ALL = 전체, app.js)
};
const SETUP_RESULT_LIMIT = 40; // 검색 결과는 너무 길지 않게

// 상인 색이 들어간 퀘스트 한 줄
function setupQuestRow(q, button) {
  const row = el("div", `setup-quest trader-${q.trader_key || "etc"}`);
  row.append(el("span", "setup-quest-name", q.name), el("span", "setup-quest-trader", q.trader));
  if (button) row.append(button);
  return row;
}

async function setupLoadQuests() {
  const [questsRes, catalogRes] = await Promise.all([
    fetch("/api/quests"),
    setupQuests.catalog ? null : fetch("/api/quest-catalog"),
  ]);
  setupQuests.data = await questsRes.json();
  if (catalogRes) setupQuests.catalog = await catalogRes.json();
  if (!questsRes.ok || setupQuests.data.error) throw new Error(setupQuests.data.error || "");
}

// ---------- 상인 칩 말풍선: 그 상인의 진행 중 퀘스트 이름 (게임 상인 화면과 맞춰 보기용) ----------
function closeTraderPop() {
  document.querySelectorAll(".setup-trader-pop").forEach((p) => p.remove());
  document.querySelectorAll(".setup-trader-count.open").forEach((c) => c.classList.remove("open"));
}

function toggleTraderPop(chip, quests) {
  const wasOpen = chip.classList.contains("open");
  closeTraderPop();
  if (wasOpen) return; // 같은 칩을 다시 누르면 닫기만
  chip.classList.add("open");
  const pop = el("div", "setup-trader-pop");
  pop.setAttribute("role", "dialog");
  pop.addEventListener("click", (e) => e.stopPropagation()); // 목록 안을 눌러도(휠·스크롤) 안 닫힘
  const list = el("ul");
  quests.slice().sort((a, b) => a.name.localeCompare(b.name, "ko"))
    .forEach((q) => list.append(el("li", q.added ? "added" : "", q.added ? t("setup.addedSuffix", { name: q.name }) : q.name)));
  pop.append(list);
  const wrap = chip.parentElement;
  wrap.append(pop);
  // 칩 바로 아래, 칸 오른쪽 끝을 넘지 않게
  const left = Math.min(chip.offsetLeft, Math.max(0, wrap.clientWidth - pop.offsetWidth));
  pop.style.left = `${left}px`;
  pop.style.top = `${chip.offsetTop + chip.offsetHeight + 4}px`;
}
document.addEventListener("click", closeTraderPop);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeTraderPop(); });

function renderSetupQuests() {
  const data = setupQuests.data;
  const inProgress = data.in_progress || [];
  const fromLog = inProgress.filter((q) => !q.added);
  const added = inProgress.filter((q) => q.added);

  // 목록 대신 개수로 물어봄 (직접 추가한 것도 포함 → 추가할 때마다 다시 비교)
  const ask = setupUI.get("setup-ask-count");
  ask.replaceChildren(...tNodes("setup.foundQuests", { n: el("b", "", t("setup.questCount", { n: inProgress.length })) }));
  if (added.length) ask.append(el("small", "", t("setup.foundDetail", { log: fromLog.length, added: added.length })));

  // 상인별 개수: 다른 상인만 게임에서 확인하면 됨
  const byTrader = {};
  inProgress.forEach((q) => {
    byTrader[q.trader] ||= { count: 0, key: q.trader_key };
    byTrader[q.trader].count += 1;
  });
  const countBox = setupUI.get("setup-trader-counts");
  countBox.replaceChildren();
  countBox.classList.add("setup-trader-wrap"); // 말풍선 목록의 기준 자리
  Object.entries(byTrader).sort((a, b) => b[1].count - a[1].count).forEach(([trader, info]) => {
    // 칩을 누르면 그 상인 퀘스트 이름이 작은 말풍선으로 뜸 (칸 크기는 그대로, 위에 겹쳐 뜸)
    const chip = el("button", `setup-trader-count trader-${info.key || "etc"}`, trader);
    chip.type = "button";
    chip.title = t("setup.traderHint");
    chip.append(el("b", "", String(info.count)), icon("chevron-down", 12));
    chip.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleTraderPop(chip, inProgress.filter((q) => q.trader === trader));
    });
    countBox.append(chip);
  });
  closeTraderPop();
  // 이미 직접 추가한 게 있으면 고치는 칸을 펼쳐 둠
  if (added.length) showSetupFix(true);

  // 직접 추가한 것: ✕ 로 빼기
  setupUI.get("setup-added-count").textContent = added.length;
  const addedBox = setupUI.get("setup-added-quests");
  addedBox.replaceChildren();
  if (!added.length) addedBox.append(el("p", "empty", t("setup.noneAdded")));
  added.forEach((q) => {
    const remove = el("button", "setup-quest-btn remove", t("setup.remove"));
    remove.type = "button";
    remove.addEventListener("click", () => setupSetAdded(q.id, false));
    addedBox.append(setupQuestRow(q, remove));
  });

  renderSetupSearch();
}

// 검색 결과: 로그로 이미 아는 퀘스트(진행 중·완료·실패)와 직접 추가한 것은 뺌
function renderSetupSearch() {
  const data = setupQuests.data;
  const known = new Set(["in_progress", "completed", "failed"].flatMap((k) => (data[k] || []).map((q) => q.id)));
  const words = setupUI.get("setup-search").value.trim().toLowerCase();
  const candidates = (setupQuests.catalog || []).filter((q) => !known.has(q.id));

  // 상인 거르기 칩
  const traders = [...new Set(candidates.map((q) => q.trader))].sort();
  const traderBox = setupUI.get("setup-traders");
  traderBox.replaceChildren();
  [FILTER_ALL, ...traders].forEach((name) => {
    const chip = el("button", "chip" + (setupQuests.trader === name ? " active" : ""), name === FILTER_ALL ? t("setup.all") : name);
    chip.type = "button";
    chip.addEventListener("click", () => { setupQuests.trader = name; renderSetupSearch(); });
    traderBox.append(chip);
  });

  const list = setupUI.get("setup-results");
  list.replaceChildren();
  const matches = candidates.filter((q) =>
    (setupQuests.trader === FILTER_ALL || q.trader === setupQuests.trader) &&
    (!words || q.name.toLowerCase().includes(words) || q.en.toLowerCase().includes(words)));
  if (!words && setupQuests.trader === FILTER_ALL) {
    list.append(el("li", "empty", t("setup.searchPrompt")));
    return;
  }
  if (!matches.length) {
    list.append(el("li", "empty", t("setup.noMatch")));
    return;
  }
  matches.slice(0, SETUP_RESULT_LIMIT).forEach((q) => {
    const add = el("button", "setup-quest-btn add", t("setup.add"));
    add.type = "button";
    add.addEventListener("click", () => setupSetAdded(q.id, true));
    const li = el("li");
    const row = setupQuestRow(q, add);
    if (q.level) row.insertBefore(el("span", "setup-quest-level", `Lv${q.level}`), add);
    if (q.en) row.title = q.en;
    li.append(row);
    list.append(li);
  });
  if (matches.length > SETUP_RESULT_LIMIT) {
    list.append(el("li", "empty", t("setup.moreResults", { n: matches.length - SETUP_RESULT_LIMIT })));
  }
}

async function setupSetAdded(questId, value) {
  try {
    await fetch("/api/check/added", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: questId, value }),
    });
    await setupLoadQuests();
    renderSetupQuests();
  } catch (error) {
    alert(t("common.saveFailedShort"));
  }
}

async function openSetupQuests() {
  showSetupPage(2);
  showSetupFix(false);
  setupUI.get("setup-ask-count").textContent = t("common.loading");
  setupUI.get("setup-trader-counts").replaceChildren();
  try {
    await setupLoadQuests();
    renderSetupQuests();
  } catch (error) {
    setupUI.get("setup-ask-count").textContent = t("setup.loadQuestsFailed", { error: error.message });
  }
}

setupUI.get("setup-search").addEventListener("input", renderSetupSearch);
setupUI.get("setup-yes").addEventListener("click", () => afterQuestStep());
setupUI.get("setup-no").addEventListener("click", () => {
  showSetupFix(true);
  setupUI.get("setup-search").focus();
});

// 빠진 것 고치는 칸 열기/닫기. 열려 있을 때만 맨 아래 [설정 끝내기]를 보여줌
function showSetupFix(open) {
  setupUI.get("setup-fix").hidden = !open;
  setupUI.get("setup-finish").hidden = !open;
  setupUI.get("setup-finish").textContent = setupUI.fromGap ? t("setup.finishCheck") : t("setup.next");
}

// 2단계(퀘스트)를 마친 뒤: 처음 설정이면 3단계(은신처)로, '로그가 지워진 것 같아요' 안내에서 왔으면 바로 끝
function afterQuestStep() {
  if (setupUI.fromGap) finishSetup();
  else openSetupHideout();
}

// ---------- 3단계: 은신처 시설 레벨 ----------
async function openSetupHideout() {
  showSetupPage(3);
  if (typeof loadHideout === "function" && !hideout.data) await loadHideout();
  renderSetupHideout();
}

function renderSetupHideout() {
  // 맨 위: 게임 에디션 (창고 등 시작 레벨이 달라짐)
  if (typeof renderEditionPicker === "function") renderEditionPicker(setupUI.get("setup-edition"));
  const box = setupUI.get("setup-ho");
  box.replaceChildren();
  const data = typeof hideout !== "undefined" && hideout.data;
  if (!data) {
    box.append(el("p", "empty", t("setup.hideoutFailed")));
    return;
  }
  // 이름순으로 한 줄씩: 그림 · 이름 · 레벨 숫자 버튼 (누르면 바로 저장)
  [...data.stations].sort((a, b) => a.name.localeCompare(b.name, "ko")).forEach((s) => {
    const row = el("div", "setup-ho-row");
    if (s.image) {
      const img = el("img", "setup-ho-img");
      img.src = s.image;
      img.alt = "";
      img.onerror = () => img.remove();
      row.append(img);
    }
    row.append(el("span", "setup-ho-name", s.name));
    const levels = el("div", "setup-ho-levels");
    for (let lv = s.default_level; lv <= s.max_level; lv++) {
      const btn = el("button", lv === s.level ? "on" : "", String(lv));
      btn.type = "button";
      btn.title = lv === 0 ? t("setup.notBuilt") : t("setup.levelN", { n: lv });
      btn.addEventListener("click", async () => {
        if (lv === s.level) return;
        await saveHideout("hideout", s.id, lv);
        renderSetupHideout();
      });
      levels.append(btn);
    }
    row.append(levels);
    box.append(row);
  });
}

setupUI.get("setup-back-3").addEventListener("click", () => showSetupPage(2));
setupUI.get("setup-finish-3").addEventListener("click", finishSetup);

// ---------- '로그가 지워진 것 같아요' 안내 (홈 맨 위) ----------
document.addEventListener("quests-changed", () => {
  const gap = (typeof state !== "undefined" && state.data && state.data.log_gap) || [];
  document.getElementById("gap-banner").hidden = !gap.length;
  document.getElementById("gap-count").textContent = gap.length;
});
// [확인하기] → 처음 설정의 2단계(진행 중인 퀘스트)로 바로
document.getElementById("gap-check").addEventListener("click", () => {
  document.body.classList.add("in-setup");
  setupUI.root.hidden = false;
  setupUI.fromGap = true; // 퀘스트만 확인하고 끝 (은신처 단계는 건너뜀)
  openSetupQuests();
});
