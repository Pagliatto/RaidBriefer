// 은신처 탭: 시설마다 다음 레벨 재료·조건, 재료 '모았음' 체크, 현재 레벨 조정
// - 합계는 다음 레벨만 (사용자 결정)

const hideout = {
  data: null,          // /api/hideout
  showTotals: false,   // 다음 레벨 재료 합계 펼침
  showDone: false,     // 다 지은 시설 펼침
};
const HO_ICON = (id) => `https://assets.tarkov.dev/${id}-icon.webp`;

async function loadHideout() {
  try {
    const res = await fetch("/api/hideout");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "");
    hideout.data = data;
    renderHideout();
    // 지도 '먹을 것'·홈 '뭘 모아 올까?' 가 은신처 재료를 같이 쓰게 알림 (4단계에서 연결)
    document.dispatchEvent(new Event("hideout-changed"));
  } catch (error) {
    document.getElementById("ho-summary").replaceChildren(el("p", "notice", t("hideout.loadFailed", { error: error.message })));
  }
}

async function saveHideout(kind, key, value) {
  try {
    await fetch(`/api/check/${kind}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, value }),
    });
  } catch (error) {
    alert(t("common.saveFailedShort"));
  }
  await loadHideout();
}

// 숫자: 100000 → 100,000
const hoCount = (n) => Number(n).toLocaleString();

// 건설 시간: 초 → "6시간" / "1일 6시간" / "30분"
function hoTime(seconds) {
  if (!seconds) return "";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return [d && t("hideout.days", { n: d }), h && t("hideout.hours", { n: h }), !d && m && t("hideout.minutes", { n: m })].filter(Boolean).join(" ");
}

// 재료 아이콘 (누르면 '모았음' 체크)
function hoItem(item) {
  const btn = el("button", `ho-item${item.got ? " got" : ""}`);
  btn.type = "button";
  btn.title = t("hideout.itemHint", { name: item.name, count: hoCount(item.count), fir: item.fir ? t("hideout.itemFir") : "", action: item.got ? t("hideout.itemUncheck") : t("hideout.itemCheck") });
  const img = el("img");
  img.src = HO_ICON(item.id);
  img.alt = item.name;
  img.onerror = () => img.replaceWith(el("span", "ho-item-text", item.name.split(" (")[0]));
  btn.append(img, el("b", "ho-count", item.count > 1 ? `×${hoCount(item.count)}` : ""));
  if (item.fir) btn.append(el("span", "ho-fir", "FIR")); // 인레이드(Found in Raid). 칸이 좁아 짧게
  if (item.got) btn.append(el("span", "ho-check", "✓"));
  btn.addEventListener("click", () => saveHideout("hideout_got", item.key, !item.got));
  return btn;
}

function hoCard(s) {
  const card = el("article", `ho-card${s.next ? (s.buildable ? " buildable" : " locked") : " done"}`);
  card.dataset.station = s.name; // 홈·지도에서 '이 재료가 필요한 시설'로 갈 때 찾는 이름표
  const head = el("div", "ho-head");
  if (s.image) {
    const img = el("img", "ho-image");
    img.src = s.image;
    img.alt = "";
    img.onerror = () => img.remove();
    head.append(img);
  }
  const title = el("div", "ho-title");
  title.append(el("b", "", s.name));
  // 레벨 점: ●●○ (지금 레벨 / 최고 레벨)
  const dots = el("span", "ho-dots");
  for (let i = 1; i <= s.max_level; i++) dots.append(el("i", i <= s.level ? "on" : ""));
  title.append(dots);
  head.append(title);

  // 시설 레벨은 버튼을 한 번 누를 때마다 바로 저장한다.
  const fix = el("div", "ho-fix");
  fix.setAttribute("role", "group");
  fix.setAttribute("aria-label", t("hideout.levelGroup", { name: s.name }));
  const minus = el("button", "", "−");
  minus.type = "button";
  minus.title = t("hideout.levelDown");
  minus.setAttribute("aria-label", t("hideout.levelDownLabel", { name: s.name }));
  minus.disabled = s.level <= s.default_level;
  minus.addEventListener("click", () => saveHideout("hideout", s.id, s.level - 1));
  const plus = el("button", "", "+");
  plus.type = "button";
  plus.title = t("hideout.levelUp");
  plus.setAttribute("aria-label", t("hideout.levelUpLabel", { name: s.name }));
  plus.disabled = s.level >= s.max_level;
  plus.addEventListener("click", () => saveHideout("hideout", s.id, s.level + 1));
  fix.append(minus, el("b", "ho-level", String(s.level)), plus);
  head.append(fix);
  card.append(head);

  if (!s.next) return card;
  const n = s.next;

  // 재료
  if (n.items.length) {
    const items = el("div", "ho-items");
    n.items.forEach((item) => items.append(hoItem(item)));
    card.append(items);
  }

  // 조건: 다른 시설(충족 ✓ / 부족 ✗), 상인·스킬(참고), 건설 시간
  const conds = el("div", "ho-conds");
  n.stations.forEach((x) => conds.append(el("span", x.ok ? "ok" : "bad", `${x.ok ? "✓" : "✗"} ${x.name} ${x.level}`)));
  n.traders.forEach((x) => conds.append(el("span", "info", `${x.name} Lv${x.level}`)));
  n.skills.forEach((x) => conds.append(el("span", "info", `${x.name} ${x.level}`)));
  if (n.seconds) conds.append(el("span", "time", `⏱ ${hoTime(n.seconds)}`));
  if (conds.childNodes.length) card.append(conds);

  return card;
}

function renderHideout() {
  const d = hideout.data;
  if (!d) return;
  const can = d.stations.filter((s) => s.next && s.buildable);
  const locked = d.stations.filter((s) => s.next && !s.buildable);
  const done = d.stations.filter((s) => !s.next);

  // 요약 + 합계 버튼
  const summary = document.getElementById("ho-summary");
  summary.replaceChildren();
  summary.append(
    el("span", "ho-stat can", t("hideout.statCan", { n: can.length })),
    el("span", "ho-stat locked", t("hideout.statLocked", { n: locked.length })),
    el("span", "ho-stat done", t("hideout.statDone", { n: done.length })),
  );
  // 스위치: 조건 남은 시설 재료도 합치기 (은신처 합계·홈 '뭘 모아 올까?'·지도 '먹을 것'이 같이 바뀜)
  const toggle = el("label", "ho-switch");
  const input = el("input");
  input.type = "checkbox";
  input.checked = !!d.include_locked;
  input.addEventListener("change", async () => {
    try {
      await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hideout_all: input.checked }),
      });
    } catch (error) {
      alert(t("common.saveFailedShort"));
    }
    await loadHideout();
  });
  const label = el("span", "", t("hideout.switchLabel"));
  label.id = "ho-switch-label";
  const hint = el("small", "ho-switch-hint", t("hideout.switchHint"));
  hint.id = "ho-switch-hint";
  input.setAttribute("aria-labelledby", label.id);
  input.setAttribute("aria-describedby", hint.id);
  const caption = el("span", "ho-switch-text");
  caption.append(label, hint);
  toggle.append(input, el("span", "ho-switch-track"), caption);
  summary.append(toggle);
  const totalsBtn = el("button", "ho-totals-btn", hideout.showTotals ? t("hideout.totalsHide") : t("hideout.totalsShow", { n: d.totals.length }));
  totalsBtn.type = "button";
  totalsBtn.addEventListener("click", () => { hideout.showTotals = !hideout.showTotals; renderHideout(); });
  summary.append(totalsBtn);

  // 다음 레벨 재료 합계 (모았음 체크한 건 빠짐)
  const totals = document.getElementById("ho-totals");
  totals.hidden = !hideout.showTotals;
  totals.replaceChildren();
  if (hideout.showTotals) {
    if (!d.totals.length) totals.append(el("p", "empty", t("hideout.totalsEmpty")));
    d.totals.forEach((tot) => {
      const chip = el("span", "ho-total");
      chip.title = t("hideout.totalNeededBy", { name: tot.name, list: tot.from.join(", ") });
      const img = el("img");
      img.src = HO_ICON(tot.id);
      img.alt = tot.name;
      img.onerror = () => img.replaceWith(el("span", "ho-item-text", tot.name.split(" (")[0]));
      chip.append(img, el("b", "ho-count", `×${hoCount(tot.count)}`));
      totals.append(chip);
    });
  }

  // 시설 목록: 지을 수 있음 → 조건 남음 → 다 지음(접어 둠)
  const list = document.getElementById("ho-list");
  list.replaceChildren();
  const group = (title, stations) => {
    if (!stations.length) return;
    list.append(el("h3", "ho-group", `${title} (${stations.length})`));
    const grid = el("div", "ho-grid");
    stations.forEach((s) => grid.append(hoCard(s)));
    list.append(grid);
  };
  group(t("hideout.groupCan"), can);
  group(t("hideout.groupLocked"), locked);
  if (done.length) {
    const btn = el("button", "ho-done-btn", hideout.showDone ? t("hideout.doneHide", { n: done.length }) : t("hideout.doneShow", { n: done.length }));
    btn.type = "button";
    btn.addEventListener("click", () => { hideout.showDone = !hideout.showDone; renderHideout(); });
    list.append(btn);
    if (hideout.showDone) {
      const grid = el("div", "ho-grid");
      done.forEach((s) => grid.append(hoCard(s)));
      list.append(grid);
    }
  }
}

// 홈 '뭘 모아 올까?'·지도 '먹을 것'에서 은신처 재료를 누르면: 은신처 탭에서 그 시설 카드로 (반짝임)
function goToStations(names) {
  goTab("hideout");
  const cards = names.map((n) => document.querySelector(`.ho-card[data-station="${CSS.escape(n)}"]`)).filter(Boolean);
  if (!cards.length) return;
  setTimeout(() => {
    cards[0].scrollIntoView({ block: "center" });
    rememberScroll();
  }, 30);
  cards.forEach((c) => {
    c.classList.remove("flash");
    void c.offsetWidth; // 반짝임을 다시 시작하게
    c.classList.add("flash");
  });
}

// ---------- 게임 에디션 (처음 설정 3단계·⚙ 설정 창에서 고름) ----------
const EDITIONS = [
  ["standard", "Standard"],
  ["left_behind", "Left Behind"],
  ["prepare_for_escape", "Prepare for Escape"],
  ["edge_of_darkness", "Edge of Darkness"],
  ["unheard", "The Unheard"],
];

// 에디션 버튼 줄 그리기 (box: 넣을 곳)
function renderEditionPicker(box) {
  if (!box) return;
  box.replaceChildren();
  const current = (hideout.data && hideout.data.edition) || "standard";
  EDITIONS.forEach(([key, label]) => {
    const btn = el("button", key === current ? "on" : "", label);
    btn.type = "button";
    btn.addEventListener("click", async () => {
      if (key === current) return;
      try {
        await fetch("/api/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ edition: key }),
        });
      } catch (error) {
        alert(t("common.saveFailedShort"));
      }
      await loadHideout();
    });
    box.append(btn);
  });
}

// 은신처 정보가 바뀔 때마다 두 곳의 에디션 버튼도 다시
document.addEventListener("hideout-changed", () => {
  renderEditionPicker(document.getElementById("settings-edition"));
  if (!document.getElementById("setup").hidden && typeof renderSetupHideout === "function") renderSetupHideout();
});

// 프레스티지·처음 설정 등으로 바뀌면 다시
window.addEventListener("settings-changed", loadHideout);
loadHideout();
