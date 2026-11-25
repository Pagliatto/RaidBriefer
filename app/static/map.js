// 지도 탭 + 레이드에 들어가면 자동 전환
// - 지도: tarkov.dev 지도 그림 (SVG Maps, CC BY-NC-SA 4.0) / 좌표 맞추는 방법은 tarkov.dev 코드(MIT)를 따름
// - 오른쪽 서랍: 고른 맵의 진행 중 퀘스트 (+ 모든 맵 퀘스트). 열면 지도가 그만큼 좁아짐
// - 목표 위치는 번호 핀으로 지도에 표시, 서랍의 같은 번호를 누르면 그 위치로 이동

const mapUI = {
  maps: [],          // /api/maps : [{key, name, ids, view}]
  current: null,     // 지금 보고 있는 맵
  leaflet: null,     // Leaflet 지도
  pins: {},          // 번호 → 지도 위 표시들 (서랍에서 누르면 이동)
  drawerOpen: true,
  autoMapId: null,   // 자동 전환으로 연 레이드 맵 id
  guideRaid: null,   // 이번 레이드의 공략 서랍 자동 열기 상태
  lobbyTimer: null,  // 로비로 돌아갈 때 잠깐 기다리는 타이머 (트랜짓 대비)
  dirty: true,       // 퀘스트가 바뀌어서 다시 그려야 함
};
const MAP_KEY_STORAGE = "tarkovhelper.map";
const TRANSITION_MS = 1600;   // 전환 안내를 최소한 보여주는 시간
const LOBBY_WAIT_MS = 30000;  // 레이드가 끝나고 이만큼 새 맵이 안 나오면 로비로 (트랜짓은 실제 로그에서 16~18초, 넉넉하게)

// SVG 지도의 표시 방향만 바꾸는 시계 방향 각도 (90도 단위). dev 원본 좌표는 유지한다.
const DISPLAY_ROTATION = { terminal: 90 };

// Pending moves belong to data keys, not Leaflet instances: map/layer redraws never lose them.
const mapEditor = { active: false, pending: new Map(), history: [], bindings: new Map(), saving: false };
const editIdentity = (key) => JSON.stringify([key.file, key.path, key.seed?.add || null]);

function cancelMarkerDraft() {
  const wasPlacing = mapEditor.placing || mapEditor.form;
  mapEditor.placing = false;
  mapEditor.draft?.remove();
  mapEditor.form?.remove();
  mapEditor.draft = mapEditor.form = null;
  if (wasPlacing && mapEditor.status) updatePositionEditor();
}

function refreshNewMarkers() {
  if (!mapEditor.active || !mapUI.leaflet) return;
  mapEditor.newLayer?.clearLayers();
  for (const [id, binding] of mapEditor.bindings) {
    if (binding.draft) {
      mapUI.floorItems = mapUI.floorItems.filter((entry) => entry.layer !== binding.marker);
      mapEditor.bindings.delete(id);
    }
  }
  if (!mapEditor.newLayer || mapEditor.newLeaf !== mapUI.leaflet) {
    mapEditor.newLeaf = mapUI.leaflet;
    mapEditor.newLayer = L.layerGroup().addTo(mapUI.leaflet);
  }
  for (const [id, pending] of mapEditor.pending) {
    if (!pending.fields || pending.map !== mapUI.current?.key) continue;
    const kind = pending.edit.seed.add;
    if (kind === "noplace" || kind === "maps" || kind === "unnoplace") continue; // 위치 없는 항목 (스토리 연속 입력의 '장소 불필요'·맵 고치기)
    const pin = pending.fields.kind;
    const icons = { bd: ["enemy", "crosshair"], boss: ["boss", "skull"], extract: [`extract-${pending.fields.faction}`, "door-open"], btr: ["btr", "bus"], story: ["story", "book-open"], guide: [pin === "lock" ? "lock" : "guide-" + pin, GUIDE_ICONS[pin] || "door-open"] };
    // 저장 전 핀도 저장된 핀과 같은 겹침 순서로 (Codex 검수 76)
    const z = { bd: MARKER_Z.blackDivision, boss: MARKER_Z.bossZones, extract: MARKER_Z.extracts, btr: MARKER_Z.btrStops,
      story: STORY_PIN_Z, guide: pin === "lock" ? MARKER_Z.keys : GUIDE_PIN_Z }[kind] || 0;
    const marker = L.marker(gamePos(pending.pos), { icon: markerIcon(...icons[kind], 22), zIndexOffset: z });
    marker.baseZ = z;
    bindPositionEditor(marker, { edit: pending.edit, pos: pending.pos, draftId: id }, pending.label);
    mapEditor.bindings.get(id).draft = true;
    marker.addTo(mapEditor.newLayer);
    mapUI.floorItems.push({ layer: marker, floor: floorOf(pending.pos) });
  }
}

// 편집 폼 글자: 이름표를 문자열로 이어 붙이지 않고 그대로 적어 둔다 (글자 사전 검사가 찾을 수 있게)
const EDITOR_TEXT = {
  kind: () => t("editor.kind"), count: () => t("editor.count"), bosses: () => t("editor.bosses"),
  name: () => t("editor.name"), nameEn: () => t("editor.nameEn"), nameKo: () => t("editor.nameKo"),
  faction: () => t("editor.faction"), pinKind: () => t("editor.pinKind"), candidate: () => t("editor.candidate"),
  steps: () => t("editor.steps"), note: () => t("editor.note"),
  kind_bd: () => t("editor.kind_bd"), kind_boss: () => t("editor.kind_boss"), kind_extract: () => t("editor.kind_extract"),
  kind_btr: () => t("editor.kind_btr"), kind_guide: () => t("editor.kind_guide"), kind_story: () => t("editor.kind_story"),
  storyStep: () => t("editor.storyStep"),
  faction_pmc: () => t("editor.faction_pmc"), faction_scav: () => t("editor.faction_scav"), faction_shared: () => t("editor.faction_shared"),
  pin_key_spawn: () => t("editor.pin_key_spawn"), pin_panel: () => t("editor.pin_panel"), pin_toolset: () => t("editor.pin_toolset"),
  pin_explosive: () => t("editor.pin_explosive"), pin_door: () => t("editor.pin_door"), pin_lock: () => t("editor.pin_lock"),
  pin_building: () => t("editor.pin_building"), pin_start: () => t("editor.pin_start"),
};
const editorText = (key) => (EDITOR_TEXT[key] ? EDITOR_TEXT[key]() : key);

function openMarkerForm(latlng) {
  const options = mapEditor.options;
  const map = mapUI.current.key;
  const picked = mapEditor.placing; // 막대에서 누른 종류 (cancelMarkerDraft 가 지우기 전에 기억)
  cancelMarkerDraft();
  mapEditor.draft = L.marker(latlng, { icon: markerIcon("lock", "crosshair", 22) }).addTo(mapUI.leaflet);
  const form = el("form", "position-add-form");
  mapEditor.form = form;
  const field = (parent, key, input) => {
    const label = el("label", "", editorText(key));
    label.append(input);
    parent.append(label);
    return input;
  };
  const select = (values, multiple = false) => {
    const input = el("select");
    input.multiple = multiple;
    for (const [value, label] of values) {
      const option = el("option", "", label);
      option.value = value;
      input.append(option);
    }
    return input;
  };
  const kind = field(form, "kind", select(Object.keys(options).filter((key) => !["noplace", "maps", "unnoplace"].includes(key))
    .map((key) => [key, editorText("kind_" + key)])));
  if (typeof picked === "string" && options[picked]) {
    kind.value = picked;
    kind.parentElement.hidden = true; // 막대의 버튼으로 이미 골랐음
  }
  const details = el("div", "position-add-fields");
  form.append(details);
  let inputs;
  const render = () => {
    details.replaceChildren();
    inputs = {};
    const input = (name, key, type = "text", required = true) => {
      const node = el("input");
      node.type = type;
      node.required = required;
      node.maxLength = 1000;
      inputs[name] = field(details, key, node);
      return node;
    };
    const choice = (name, key, values, multiple = false) => {
      inputs[name] = field(details, key, select(values, multiple));
    };
    if (kind.value === "bd") {
      const count = input("count", "count", "number");
      count.min = 1; count.max = 20; count.step = 1; count.value = 1;
    } else if (kind.value === "boss") {
      choice("bosses", "bosses", options.boss.seed.bosses.map((id) => [id, id]), true);
      inputs.bosses.required = true;
    } else if (kind.value === "extract" || kind.value === "btr") {
      input("name", kind.value === "extract" ? "nameEn" : "name");
      if (kind.value === "extract") choice("faction", "faction", ["pmc", "scav", "shared"].map((id) => [id, editorText("faction_" + id)]));
    } else if (kind.value === "story") {
      // 이 맵이 적힌 스토리 단계만 (이미 핀이 있는 단계는 ✓)
      const placed = new Set(options.story.seed.placed || []);
      choice("step", "storyStep", storyStepsNeedingPlace(options.story.seed.steps).map((s) =>
        [s.id, (placed.has(s.id) ? "✓ " : "") + s.chapter + " · " + s.ko + " / " + s.en]));
      input("candidate", "candidate", "checkbox", false);
    } else {
      choice("kind", "pinKind", ["key_spawn", "panel", "toolset", "explosive", "door", "lock", "building", "start"].map((id) => [id, editorText("pin_" + id)]));
      input("label_ko", "nameKo"); input("label_en", "nameEn");
      input("candidate", "candidate", "checkbox", false);
      choice("steps", "steps", options.guide.seed.steps.map((s) => [s.id, s.ko + " / " + s.en]), true);
    }
    input("note", "note", "text", false);
  };
  kind.addEventListener("change", render);
  render();
  const actions = el("div", "position-add-actions");
  const submit = el("button", "layer-toggle-btn", t("editor.add"));
  submit.type = "submit";
  const cancel = el("button", "layer-toggle-btn", t("editor.dismiss"));
  cancel.type = "button";
  cancel.addEventListener("click", cancelMarkerDraft);
  actions.append(submit, cancel); form.append(actions);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (mapEditor.saving || mapUI.current?.key !== map || !form.reportValidity()) return;
    const fields = {};
    for (const [name, input] of Object.entries(inputs)) {
      fields[name] = input.multiple ? [...input.selectedOptions].map((o) => o.value) :
        input.type === "checkbox" ? input.checked : input.type === "number" ? Number(input.value) : input.value.trim();
    }
    const edit = options[kind.value];
    const near = (mapEditor.heights || []).reduce((best, p) =>
      !best || Math.hypot(p[0] - latlng.lng, p[1] - latlng.lat) < Math.hypot(best[0] - latlng.lng, best[1] - latlng.lat) ? p : best, null);
    const pos = [Math.round(latlng.lng * 10) / 10, Math.round(latlng.lat * 10) / 10, near?.[2] || 0];
    mapEditor.serial = (mapEditor.serial || 0) + 1;
    const id = "draft:" + mapEditor.serial;
    const label = fields.name || fields.label_ko || editorText("kind_" + kind.value);
    mapEditor.pending.set(id, { edit, fields, pos, base: pos.slice(), map, label });
    mapEditor.history.push({ id });
    cancelMarkerDraft();
    refreshPositionEdits();
  });
  document.getElementById("tab-map").append(form);
  kind.focus();
}

async function initializePositionEditor() {
  if (new URLSearchParams(location.search).get("edit") !== "1") return;
  try {
    const response = await fetch("/api/editor", { cache: "no-store" });
    if (!response.ok || !(await response.json()).enabled) return;
  } catch (error) { return; }
  mapEditor.active = true;
  clearTimeout(mapUI.lobbyTimer);
  document.body.classList.add("position-edit");
  const banner = el("div", "position-editor");
  banner.append(el("strong", "", t("editor.title")));
  mapEditor.counter = el("span");
  banner.append(mapEditor.counter);
  mapEditor.storyCount = el("span", "position-story-count"); // 이 맵 스토리 장소 N/M (마우스를 올리면 핀 없는 단계)
  mapEditor.storyCount.hidden = true;
  banner.append(mapEditor.storyCount);
  const button = (key, action) => {
    const node = el("button", "layer-toggle-btn", t(key));
    node.type = "button";
    node.addEventListener("click", action);
    banner.append(node);
    return node;
  };
  mapEditor.undo = button("editor.undo", undoPositionEdit);
  // 종류별 추가 버튼: 누르고 지도를 클릭하면 그 종류로 바로 (종류 고르기 단계 없음)
  mapEditor.addButtons = {};
  for (const key of ["bd", "boss", "extract", "btr", "guide", "story"]) {
    const node = el("button", "layer-toggle-btn", "+ " + editorText("kind_" + key));
    node.type = "button";
    node.addEventListener("click", () => {
      if (mapEditor.saving || !mapEditor.options?.[key]) return;
      if (mapEditor.walk) stopStoryWalk(); // 연속 입력 중이면 끝내고 일반 추가로
      cancelMarkerDraft();
      mapEditor.placing = key;
      updatePositionEditor(t("editor.clickMap"));
    });
    banner.append(node);
    mapEditor.addButtons[key] = node;
  }
  mapEditor.walkButton = button("editor.walk", () => (mapEditor.walk ? stopStoryWalk() : startStoryWalk()));
  mapEditor.reviewButton = button("editor.review", () => (mapEditor.walk ? stopStoryWalk() : startStoryReview()));
  window.addEventListener("keydown", (event) => { if (event.key === "Escape") cancelMarkerDraft(); });
  mapEditor.cancel = button("editor.cancel", () => {
    if (mapEditor.saving) return;
    mapEditor.pending.clear();
    mapEditor.history = [];
    cancelMarkerDraft();
    refreshPositionEdits();
    if (mapEditor.walk) startStoryWalk(); // 연속 입력: 취소한 단계까지 다시 목록에
  });
  mapEditor.save = button("editor.save", savePositionEdits);
  mapEditor.status = el("span", "position-editor-status");
  mapEditor.status.setAttribute("role", "status");
  banner.append(mapEditor.status);
  document.getElementById("tab-map").prepend(banner);
  // 편집 모드에서는 '바뀐 점' 같은 안내 창을 띄우지 않는다 (편집을 가림). 본 것으로 기록하지 않고 닫기만 → 평소 실행 때 다시 나옴
  const whatsNew = document.getElementById("whats-new");
  if (whatsNew) {
    const closeNotice = () => { if (whatsNew.open) whatsNew.close(); };
    closeNotice();
    new MutationObserver(closeNotice).observe(whatsNew, { attributes: true, attributeFilter: ["open"] });
  }
  // 편집 모드는 화면 배치가 바뀌어 지도 칸이 커진다 → 크기가 바뀌면 전체 보기를 다시 맞춘다 (관찰자는 하나만)
  const mapBox = document.getElementById("map-view");
  if (mapBox && window.ResizeObserver) {
    new ResizeObserver(() => { if (mapUI.leaflet) fitFull(mapUI.leaflet, false); }).observe(mapBox);
  }
  updatePositionEditor();
  window.addEventListener("beforeunload", (event) => {
    if (!mapEditor.pending.size) return;
    event.preventDefault();
    event.returnValue = ""; // Browsers supply their own localized confirmation text.
  });
  goTab("map");
}

function updatePositionEditor(message = "") {
  if (!mapEditor.active) return;
  mapEditor.counter.textContent = t("editor.changed", { n: mapEditor.pending.size });
  const storySeed = mapEditor.options?.story?.seed;
  if (mapEditor.storyCount) {
    mapEditor.storyCount.hidden = !storySeed;
    if (storySeed) {
      const placed = new Set(storySeed.placed || []);
      const need = storyStepsNeedingPlace(storySeed.steps);
      const missing = need.filter((s) => !placed.has(s.id));
      mapEditor.storyCount.textContent = t("editor.storyProgress", { n: need.length - missing.length, total: need.length });
      mapEditor.storyCount.title = missing.length ? [t("editor.storyMissing"), ...missing.map((s) => s.chapter + " · " + s.ko)].join(String.fromCharCode(10)) : "";
    }
  }
  mapEditor.undo.disabled = mapEditor.saving || !mapEditor.history.length;
  mapEditor.cancel.disabled = mapEditor.save.disabled = mapEditor.saving || !mapEditor.pending.size;
  mapEditor.status.textContent = message;
  if (mapEditor.walkButton) {
    mapEditor.walkButton.classList.toggle("on", Boolean(mapEditor.walk && !mapEditor.walk.review));
    mapEditor.walkButton.disabled = mapEditor.saving || !story.data;
  }
  if (mapEditor.reviewButton) { // 모순(장소도 있고 '장소 불필요'에도 있음) 검토: 있을 때만
    const n = storyConflicts().length;
    mapEditor.reviewButton.hidden = !n && !mapEditor.walk?.review;
    mapEditor.reviewButton.textContent = t("editor.review") + (n ? ` (${n})` : "");
    mapEditor.reviewButton.classList.toggle("on", Boolean(mapEditor.walk?.review));
    mapEditor.reviewButton.disabled = mapEditor.saving || !story.data;
  }
  Object.entries(mapEditor.addButtons || {}).forEach(([key, node]) => {
    node.hidden = !mapEditor.options?.[key]; // 이 맵에서 쓸 수 없는 종류는 숨김 (공략·스토리)
    node.disabled = mapEditor.saving;
    node.classList.toggle("on", mapEditor.placing === key);
  });
  document.body.classList.toggle("position-saving", mapEditor.saving);
}

function refreshPositionEdits(message = "") {
  refreshNewMarkers();
  mapEditor.bindings.forEach((binding) => binding.refresh());
  if (mapUI.leaflet) applyFloor();
  updatePositionEditor(message);
}

function undoPositionEdit() {
  if (mapEditor.saving) return;
  const previous = mapEditor.history.pop();
  if (!previous) return;
  const step = mapEditor.pending.get(previous.id)?.fields?.step;
  if (previous.before) mapEditor.pending.set(previous.id, previous.before);
  else mapEditor.pending.delete(previous.id);
  refreshPositionEdits();
  const back = mapEditor.walk && !previous.before && step ? mapEditor.walk.queue.findIndex((i) => i.step.id === step) : -1;
  if (back >= 0) { mapEditor.walk.index = back; showStoryWalk(); } // 연속 입력: 되돌린 단계로
  else if (mapEditor.walk) showStoryWalk();
}

function bindPositionEditor(marker, item, label, guide = false) {
  if (!mapEditor.active || !item.edit) return;
  const id = item.draftId || editIdentity(item.edit);
  const binding = { marker, guide, base: item.pos.slice() };
  const position = () => mapEditor.pending.get(id)?.pos || binding.base;
  if (item.draftId || item.edit.seed?.created) {
    const content = el("div");
    content.append(el("div", "", label));
    const remove = el("button", "layer-toggle-btn", t("editor.delete"));
    remove.type = "button";
    remove.addEventListener("click", () => {
      if (mapEditor.saving) return;
      const before = mapEditor.pending.get(id);
      mapEditor.history.push({ id, before });
      if (item.draftId) mapEditor.pending.delete(id);
      else mapEditor.pending.set(id, { edit: before?.edit || item.edit, pos: position().slice(), delete: true });
      marker.closePopup();
      refreshPositionEdits();
    });
    content.append(remove);
    marker.bindPopup(content);
  }
  marker.options.draggable = true;
  marker.options.icon.options.className += " position-editable";
  marker.unbindTooltip();
  marker.bindTooltip("", { direction: "top", offset: [0, -12] });
  const tooltip = (pos) => {
    const text = label + " · " + t("editor.coordinates", { x: pos[0].toFixed(1), z: pos[1].toFixed(1) });
    marker.setTooltipContent(escapeHtml(text));
    if (marker.getElement()) marker.getElement().title = text;
  };
  binding.refresh = () => {
    const pos = position();
    marker.setLatLng(gamePos(pos));
    const node = marker.getElement();
    if (node) {
      node.classList.add("position-editable");
      node.classList.toggle("position-deleted", !!mapEditor.pending.get(id)?.delete);
      let badge = node.querySelector(".position-moved");
      const fresh = item.draftId || item.edit.seed?.created;
      if ((mapEditor.pending.has(id) || fresh) && !badge) {
        badge = el("small", "position-moved", t(fresh ? "editor.new" : "editor.moved"));
        node.append(badge);
      } else if (!mapEditor.pending.has(id) && !fresh) badge?.remove();
    }
    tooltip(pos);
    const floorItem = mapUI.floorItems.find((entry) => entry.layer === marker);
    if (floorItem) floorItem.floor = floorOf(pos);
    if (mapEditor.saving || mapEditor.pending.get(id)?.delete) marker.dragging?.disable();
    else marker.dragging?.enable();
  };
  mapEditor.bindings.set(id, binding);
  marker.on("add", binding.refresh);
  marker.on("click", () => goToFloorOf(floorOf(position())));
  marker.on("drag", () => {
    const ll = marker.getLatLng();
    tooltip([ll.lng, ll.lat]);
  });
  marker.on("dragend", () => {
    if (mapEditor.saving) { binding.refresh(); return; }
    // Leaflet already used tdCRS.unproject, including Terminal's display rotation.
    // lng is game x, lat is game z. Applying another inverse rotation would be wrong.
    const ll = marker.getLatLng();
    const pos = [Math.round(ll.lng * 10) / 10, Math.round(ll.lat * 10) / 10, binding.base[2]];
    if (!pos.every(Number.isFinite)) { binding.refresh(); return; }
    const before = mapEditor.pending.get(id);
    const old = position();
    if (pos[0] === old[0] && pos[1] === old[1]) { binding.refresh(); return; }
    mapEditor.history.push({ id, before });
    const base = before?.base || binding.base;
    if (!before?.fields && pos[0] === Math.round(base[0] * 10) / 10 && pos[1] === Math.round(base[1] * 10) / 10) mapEditor.pending.delete(id);
    else mapEditor.pending.set(id, { ...before, edit: before?.edit || item.edit, pos, base });
    refreshPositionEdits();
  });
  binding.refresh();
}

async function savePositionEdits() {
  if (mapEditor.saving || !mapEditor.pending.size) return;
  mapEditor.saving = true;
  refreshPositionEdits(t("editor.saving"));
  let message = t("editor.saveFailed");
  let wrote = false;
  try {
    // 같은 글 단계(twins)는 같은 장소·같은 열쇠로 한 번씩 더 넣음
    const ordered = [...mapEditor.pending.values()].sort((a, b) =>
      (b.edit?.seed?.add === "maps") - (a.edit?.seed?.add === "maps")); // 맵 고치기 먼저
    const changes = ordered.flatMap(({ edit, pos, fields, delete: removed, twins }) =>
      [fields?.step, ...(fields && twins ? twins : [])].map((step, i) => ({ edit, x: pos[0], z: pos[1],
        ...(fields ? { fields: i ? { ...fields, step } : fields } : {}), ...(removed ? { delete: true } : {}) })));
    const response = await fetch("/api/editor", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ changes }),
    });
    const result = await response.json();
    const saved = new Set((result.saved || []).map(editIdentity));
    const acknowledged = new Set();
    for (const [id, pending] of mapEditor.pending) {
      if (!saved.has(editIdentity(pending.edit))) continue;
      acknowledged.add(id);
      const moved = mapEditor.pending.get(id);
      const binding = mapEditor.bindings.get(id);
      if (binding && moved) binding.base = moved.pos.slice();
      mapEditor.pending.delete(id);
    }
    mapEditor.history = mapEditor.history.filter((entry) => !acknowledged.has(entry.id));
    wrote = saved.size > 0;
    message = response.ok && result.ok ? t("editor.saved") : t("editor.saveFailed");
  } catch (error) { /* Keep every unacknowledged move for retry or undo. */ }
  finally {
    mapEditor.saving = false;
    refreshPositionEdits(message);
  }
  if (wrote) {
    // Get fresh signatures/revisions; never retain old guide coordinates after saving.
    mapGuide.controller?.abort();
    mapGuide.key = null;
    if (typeof loadStory === "function") await loadStory(); // 스토리 장소의 새 순번·서명
    showStoryWalk(); // 연속 입력 카드도 새 데이터로
    await drawMap();
  }
}

// ---------- 좌표 계산 (tarkov.dev src/pages/map/index.jsx 와 같은 방식) ----------
function tdRotate(latLng, rotation) {
  if (!latLng.lng && !latLng.lat) return L.latLng(0, 0);
  if (!rotation) return latLng;
  const rad = (rotation * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const { lng: x, lat: y } = latLng;
  return L.latLng(x * sin + y * cos, x * cos - y * sin);
}

function rotateDisplayPoint(point, degrees) {
  const rad = degrees * Math.PI / 180;
  const cos = Math.round(Math.cos(rad));
  const sin = Math.round(Math.sin(rad));
  return L.point(point.x * cos - point.y * sin, point.x * sin + point.y * cos);
}

function tdCRS(view, displayRotation = 0) {
  const tf = view.transform || [1, 0, -1, 0];
  const rotation = Number(view.rotation) || 0;
  const crs = L.extend({}, L.CRS.Simple, {
    transformation: new L.Transformation(tf[0], tf[1], tf[2] * -1, tf[3]),
    projection: L.extend({}, L.Projection.LonLat, {
      project: (latLng) => L.Projection.LonLat.project(tdRotate(latLng, rotation)),
      unproject: (point) => tdRotate(L.Projection.LonLat.unproject(point), rotation * -1),
    }),
  });
  if (!displayRotation) return crs; // 다른 맵은 기존 CRS 그대로
  // dev 회전·비율·이동을 적용한 화면 좌표를 회전한다. 역변환도 같아서 클릭·fitArea가 맞는다.
  const { projection, transformation } = crs;
  crs.projection = L.extend({}, projection, {
    project: (ll) => rotateDisplayPoint(transformation.transform(projection.project(ll)), displayRotation),
    unproject: (p) => projection.unproject(transformation.untransform(rotateDisplayPoint(p, -displayRotation))),
  });
  crs.transformation = new L.Transformation(1, 0, 1, 0);
  return crs;
}

function rotateMapSvg(svg, inner, degrees) {
  if (!degrees) return;
  const [x, y, w, h] = inner.getAttribute("viewBox").trim().split(/[\s,]+/).map(Number);
  if (![x, y, w, h].every(Number.isFinite) || w <= 0 || h <= 0) throw new Error("invalid-viewbox");
  const corners = [[x, y], [x + w, y], [x, y + h], [x + w, y + h]]
    .map(([px, py]) => rotateDisplayPoint(L.point(px, py), degrees));
  const xs = corners.map((p) => p.x), ys = corners.map((p) => p.y);
  svg.setAttribute("viewBox", [Math.min(...xs), Math.min(...ys), Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)].join(" "));
  // 내부 SVG의 크기를 고정한 뒤 층·나침반을 포함한 전체 그림을 함께 돌린다.
  inner.setAttribute("x", x);
  inner.setAttribute("y", y);
  inner.setAttribute("width", w);
  inner.setAttribute("height", h);
  const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
  group.setAttribute("transform", "rotate(" + degrees + ")");
  group.append(inner);
  svg.replaceChildren(group);
}

function tdBounds(b) {
  return L.latLngBounds([b[0][1], b[0][0]], [b[1][1], b[1][0]]);
}

// 범위가 화면에 꽉 차게 맞추기.
// 회전된 지도(세관 180도 등)에서는 Leaflet 기본 fitBounds 가 뒤집힌 범위로 계산해서 틀림 → 네 모서리를 직접 계산
function fitArea(leaf, bounds, { maxZoom = leaf.getMaxZoom(), padding = 20, animate = false } = {}) {
  const corners = [bounds.getSouthWest(), bounds.getNorthEast(), bounds.getNorthWest(), bounds.getSouthEast()]
    .map((ll) => leaf.project(ll, 0));
  const xs = corners.map((p) => p.x);
  const ys = corners.map((p) => p.y);
  const w = Math.max(Math.max(...xs) - Math.min(...xs), 1e-6);
  const h = Math.max(Math.max(...ys) - Math.min(...ys), 1e-6);
  const size = leaf.getSize();
  let zoom = Math.log2(Math.min((size.x - padding * 2) / w, (size.y - padding * 2) / h));
  zoom = Math.max(leaf.getMinZoom(), Math.min(maxZoom, zoom));
  const center = leaf.unproject(L.point((Math.max(...xs) + Math.min(...xs)) / 2, (Math.max(...ys) + Math.min(...ys)) / 2), 0);
  if (animate && !reducedMotion()) leaf.flyTo(center, zoom, { duration: 0.6 });
  else leaf.setView(center, zoom, { animate: false });
}

// 가장 멀리 본 화면 = 지도가 꽉 찬 화면. 그보다 더 축소는 안 되게 (작은 지도 + 검은 여백 방지)
// reset: true 면 전체 보기로 돌아감. false 면 지금 전체 보기였을 때만 다시 맞춤 (서랍 열고 닫기·창 크기)
function fitFull(leaf, reset) {
  if (!mapUI.bounds) return;
  const wasFull = leaf.getZoom() <= leaf.getMinZoom() + 0.05;
  leaf.invalidateSize({ pan: !reducedMotion(), animate: false });
  leaf.setMinZoom(0);
  const center = leaf.getCenter();
  const zoom = leaf.getZoom();
  fitArea(leaf, mapUI.bounds, { padding: 6 });
  const fullZoom = leaf.getZoom();
  leaf.setMinZoom(fullZoom);
  if (!reset && !wasFull) leaf.setView(center, Math.max(zoom, fullZoom), { animate: false });
}

// 게임 좌표 [x, z] → 지도 위치
function gamePos(p) {
  return [p[1], p[0]];
}

// 잠긴 문·열쇠 자리의 '마우스 올림 창' (2026-11-20 사용자)
// 아이콘에 올리면 뜨고, 창 위로 마우스를 옮기면 그대로 있음(위키 📖 를 누를 수 있게).
// 창·아이콘 밖으로 나가면 잠깐 뒤 닫히고, 다른 아이콘에 올리면 그 창으로 바뀜
const hoverCard = { popup: null, timer: null };
function closeHoverCardSoon() {
  clearTimeout(hoverCard.timer);
  hoverCard.timer = setTimeout(() => {
    const popup = hoverCard.popup;
    const inner = popup?.getElement()?.querySelector(".hover-card-motion");
    if (!inner) return;
    animateClose(inner, () => {
      popup.remove();
      if (hoverCard.popup === popup) hoverCard.popup = null;
    });
  }, 250);
}
function openHoverCard(marker, html) {
  clearTimeout(hoverCard.timer);
  if (hoverCard.popup && hoverCard.popup.source === marker && hoverCard.popup.isOpen()) {
    const inner = hoverCard.popup.getElement().querySelector(".hover-card-motion");
    if (isWindowClosing(inner)) animateOpen(inner, "rise");
    return;
  }
  const previous = hoverCard.popup?.getElement()?.querySelector(".hover-card-motion");
  if (previous) cancelWindowMotion(previous);
  const popup = L.popup({ closeButton: false, autoPan: false, className: "hover-card", offset: [0, -6] })
    .setLatLng(marker.getLatLng()).setContent(html);
  popup.source = marker;
  popup.openOn(mapUI.leaflet);  // 열려 있던 다른 창은 Leaflet 이 닫음
  hoverCard.popup = popup;
  const box = popup.getElement();
  // Leaflet 본체의 위치 transform은 건드리지 않고 내용과 꼬리만 감싼다.
  const inner = el("div", "hover-card-motion");
  inner.append(...box.childNodes);
  box.append(inner);
  animateOpen(inner, "rise");
  popup.on("remove", () => {
    cancelWindowMotion(inner);
    if (hoverCard.popup === popup) hoverCard.popup = null;
  });
  box.addEventListener("mouseenter", () => {
    clearTimeout(hoverCard.timer);
    if (isWindowClosing(inner)) animateOpen(inner, "rise");
  });
  box.addEventListener("mouseleave", closeHoverCardSoon);
}
// 열쇠 이름 + 위키 📖 (퀘스트 열쇠 링크와 같은 모양)
function keyWithWiki(name, wiki) {
  if (!wiki) return escapeHtml(name);
  return `${escapeHtml(name)} ${keyWikiLink({ name, wiki }).outerHTML}`;
}

// 지도 표시 공통 모양: 색 네모 + 아이콘 (2026-11-20 사용자). 퀘스트 목표만 번호 동그라미로 따로
// kind: 색 (CSS .mk-<kind>), icon: icons.js 이름, size: 한 변 px
// 지도 전용 해골 그림. 공통 아이콘 파일을 바꾸지 않고 같은 선 그림 규격을 사용한다.
function mapIconSvg(name, size, stroke = 2.4) {
  if (name !== "skull") return iconSvg(name, size, stroke);
  return `<svg class="icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 19H6v-4a8 8 0 1 1 12 0v4h-2v3H8z"/><circle cx="9" cy="11" r="1"/><circle cx="15" cy="11" r="1"/><path d="m11 16 1-2 1 2M12 19v3"/></svg>`;
}

function markerIcon(kind, icon, size) {
  const glyph = Math.round(size * 0.68);
  return L.divIcon({ className: `feature mk mk-${kind}`, html: `<span>${mapIconSvg(icon, glyph, 2.4)}</span>`, iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
}

// 목록 안의 작은 지도 기호 (표시 서랍 기호와 같은 모양)
function markerSymbol(kind, icon) {
  const mark = el("span", "layer-mark");
  mark.innerHTML = `<span class="mk mk-${kind} mk-legend"><span>${mapIconSvg(icon, 11, 2.4)}</span></span>`;
  return mark;
}

// 상인 색 (CSS .trader-○○ 의 --trader 값)
const traderColorCache = {};
function traderColor(key) {
  if (!(key in traderColorCache)) {
    const probe = el("span", `trader-${key || "etc"}`);
    document.body.append(probe);
    traderColorCache[key] = getComputedStyle(probe).getPropertyValue("--trader").trim() || "#c8b27a";
    probe.remove();
  }
  return traderColorCache[key];
}

// ---------- 맵 목록 ----------
let mapListRetry = 1000;
async function loadMapList() {
  try {
    const res = await fetch("/api/maps");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "");
    mapUI.maps = data;
    mapListRetry = 1000;
    registerMapColors(data);
    document.dispatchEvent(new Event("maps-changed"));
  } catch (error) {
    document.getElementById("map-view").replaceChildren(el("p", "empty", t("map.loadFailed", { error: error.message })));
    setTimeout(loadMapList, mapListRetry);
    mapListRetry = Math.min(mapListRetry * 2, 60000);
    return;
  }
  let saved = null;
  try { saved = localStorage.getItem(MAP_KEY_STORAGE); } catch (e) { /* 저장소를 못 쓰는 창 */ }
  mapUI.current = mapUI.maps.find((m) => m.key === saved) || mapUI.maps[0];
  fillMapSelect();
  if (currentTab() === "map") drawMap();
  drawBtrTrackerLink();  // 처음 켤 때 마지막 맵이 등대·삼림·시내면 바로 보이게 (전에는 맵을 바꿔야 나왔음)
  // 페이지를 열었을 때 이미 레이드 중이면 바로 그 맵으로
  if (typeof topbar !== "undefined") onGameState(topbar.state);
}

function fillMapSelect() {
  const select = document.getElementById("map-select");
  select.replaceChildren();
  mapUI.maps.forEach((m) => {
    const count = mapQuests(m.name).length;
    const option = el("option", "", count ? `${m.name} (${count})` : m.name);
    option.value = m.key;
    option.selected = mapUI.current && m.key === mapUI.current.key;
    select.append(option);
  });
}

function chooseMap(key, remember = true) {
  const next = mapUI.maps.find((m) => m.key === key);
  if (!next) return;
  if (mapUI.guideRaid) mapUI.guideRaid.pending = false; // 직접 고른 맵은 서랍을 강제로 열지 않음
  mapUI.current = next;
  if (remember) {
    try { localStorage.setItem(MAP_KEY_STORAGE, key); } catch (e) { /* 무시 */ }
  }
  document.getElementById("map-select").value = key;
  drawBtrTrackerLink();
  drawMap();
}

// BTR 트래커 링크: tarkovbtr.com 이 다루는 맵(시내·삼림·등대)에서만 (2026-11-20 사용자)
const BTR_TRACKER_MAPS = { "streets-of-tarkov": "streets", woods: "woods", lighthouse: "lighthouse" };
function drawBtrTrackerLink() {
  const link = document.getElementById("btr-tracker-link");
  const site = mapUI.current && BTR_TRACKER_MAPS[mapUI.current.key];
  link.hidden = !site;
  if (site) link.href = `https://tarkovbtr.com/?map=${site}`;
  drawWikiMapLink();
}

// 위키 지도 링크: 우리 지도로는 안을 보기 어려운 맵만 (쇄빙선: 층별 배 내부, 2026-11-23 사용자)
const WIKI_MAP_PAGES = { icebreaker: "Map:Icebreaker" };
function drawWikiMapLink() {
  const link = document.getElementById("wiki-map-link");
  const page = mapUI.current && WIKI_MAP_PAGES[mapUI.current.key];
  if (!link) return;
  link.hidden = !page;
  if (page) link.href = `https://escapefromtarkov.fandom.com/wiki/${page}`;
}

// 이 맵의 진행 중 퀘스트 (숨긴 것 제외) → 그다음 '모든 맵' 퀘스트
function mapQuests(name) {
  const active = activeQuests();
  return active.filter((q) => q.maps.includes(name));
}

// ---------- 지도 그리기 ----------
const mapStory = { focused: null, explicit: null, markers: [] };

// Use exactly the current steps shown in the drawer, including its guide de-duplication.
// 지도 핀용: 모든 챕터(시작 안 한 챕터 포함)의 현재 구간에서 체크 안 된 단계 (선택 단계 포함, 고른 갈림길만).
// 챕터마다 이 맵에 장소가 있는 첫 단계가 current=true (진하게 = 다음 갈 곳), 나머지는 흐리게.
// (2026-11-23 사용자: 첫 단계에 장소가 없으면 핀이 안 보이던 문제)
function storyPinSteps(m = mapUI.current) {
  if (!m || typeof visibleItems !== "function" || typeof story === "undefined" || !story.data) return [];
  const covered = new Set((currentGuide()?.steps || []).map((step) => step.story_step).filter(Boolean));
  const out = [];
  for (const ch of story.data.chapters || []) {
    const { items } = visibleItems(ch);
    let first = true;
    const unfinished = items.filter((it) => it.type === "section" && !isSectionDone(it));
    unfinished.filter((it, index) => index === 0 || isSectionStarted(it)).forEach((it) => it.steps.forEach((step) => {
      if (isStepDone(step) || covered.has(step.id)) return;
      const maps = step.map_names && step.map_names.length ? step.map_names : (it.sec.map_names || []);
      if (!maps.includes(m.name)) return;
      const here = storyPlacesFor(step.id, m.key).length > 0;
      out.push({ s: { ch }, entry: { step, section: it.sec }, current: here && first });
      if (here) first = false;
    }));
  }
  return out;
}

// 서랍 스토리 칸도 핀과 같은 규칙 (챕터별로 묶음). 시작 안 한 챕터도 보임 — 어디서 시작할지 모르는 뉴비용 (2026-11-23 사용자)
function storyDrawerChapters(m = mapUI.current) {
  const byChapter = new Map();
  storyPinSteps(m).forEach(({ s, entry }) => {
    if (!byChapter.has(s.ch.id)) byChapter.set(s.ch.id, { s, entries: [] });
    byChapter.get(s.ch.id).entries.push(entry);
  });
  return [...byChapter.values()];
}

function storyPlaceEntry(id) {
  for (const ch of story.data?.chapters || []) {
    for (const section of ch.sections || []) {
      const step = (section.steps || []).find((st) => st.id === id);
      if (step) return { s: { ch }, entry: { step, section } };
    }
  }
  return null;
}

// ---------- 스토리 장소 연속 입력 (편집 모드) ----------
// 장소가 필요한 단계를 차례로 보여 주고, 지도를 한 번 클릭하면 그 단계 장소로 넣고 다음으로 넘어감.
// 진행 중인 챕터 먼저. 이미 장소가 있거나 대기 중인 단계, 장소 불필요 단계는 뺌.
function storyWalkQueue() {
  const skip = new Set(story.data?.noPlace || []);
  const links = story.data?.extractLinks || {};
  const waiting = new Set([...mapEditor.pending.values()].filter((p) => p.fields?.step && !["maps", "unnoplace"].includes(p.edit?.seed?.add))
    .flatMap((p) => [p.fields.step, ...(p.twins || [])]));
  const chapters = story.data?.chapters || [];
  const ordered = [...chapters.filter((ch) => isChapterStarted(ch)), ...chapters.filter((ch) => !isChapterStarted(ch))];
  const queue = [];
  const byText = new Map(); // 같은 글·같은 맵 (갈림길마다 반복되는 같은 행동) → 한 카드에 묶음
  for (const ch of ordered) {
    for (const section of ch.sections || []) {
      for (const step of section.steps || []) {
        const raw = step.map || section.map;
        const keys = (Array.isArray(raw) ? raw : [raw]).filter(Boolean);
        const maps = keys.filter((key) => mapUI.maps.some((m) => m.key === key));
        if (!maps.length || skip.has(step.id) || links[step.id] || waiting.has(step.id) || storyPlacesFor(step.id).length) continue;
        const same = byText.get(step.ko + "|" + maps.join(","));
        if (same) { same.twins.push(step.id); continue; }
        const item = { ch, section, step, map: maps[0], maps, twins: [] };
        byText.set(step.ko + "|" + maps.join(","), item);
        queue.push(item);
      }
    }
  }
  return queue;
}

// 장소가 있는데 '장소 불필요'에도 들어간 단계 (예전에 둘 다 저장된 것)
function storyConflicts() {
  return (story.data?.noPlace || []).filter((id) => storyPlacesFor(id).length);
}

function startStoryReview() {
  cancelMarkerDraft();
  const queue = [];
  for (const id of storyConflicts()) {
    for (const ch of story.data?.chapters || []) {
      for (const section of ch.sections || []) {
        const step = (section.steps || []).find((st) => st.id === id);
        if (!step) continue;
        const maps = [...new Set(storyPlacesFor(id).map((p) => p.map))].filter((key) => mapUI.maps.some((m) => m.key === key));
        if (maps.length) queue.push({ ch, section, step, map: maps[0], maps, twins: [], review: true });
      }
    }
  }
  mapEditor.walk = { queue, index: 0, review: true };
  showStoryWalk();
}

// 검토 카드: 핀을 보여 주고 [장소 맞음]/[장소 불필요 맞음]
function showStoryReview(card, item, act) {
  const walk = mapEditor.walk;
  const decided = mapEditor.pending.get("unnoplace:" + item.step.id) ? "place"
    : storyPlacesFor(item.step.id).some((p) => p.edit && mapEditor.pending.get(editIdentity(p.edit))?.delete) ? "none" : null;
  card.append(el("div", "story-walk-head", `${walk.index + 1} / ${walk.queue.length} · ${item.ch.name} · ` +
    item.maps.map((key) => mapUI.maps.find((m) => m.key === key)?.name || key).join(" / ")));
  card.append(el("strong", "", item.step.ko));
  if (item.step.en && item.step.en !== item.step.ko) card.append(el("small", "", item.step.en));
  card.append(el("p", "story-walk-hint", decided ? t(decided === "place" ? "editor.reviewPlaceDone" : "editor.reviewNoneDone")
    : t("editor.reviewHint", { n: storyPlacesFor(item.step.id).length })));
  const row = el("div", "story-walk-actions");
  row.append(
    act("editor.walkPrev", () => { walk.index = Math.max(0, walk.index - 1); showStoryWalk(); }),
    act("editor.reviewPlace", () => storyReviewDecide(item, "place"), decided === "place" ? "on" : ""),
    act("editor.reviewNone", () => storyReviewDecide(item, "none"), decided === "none" ? "on" : ""),
    act("editor.walkNext", () => advanceStoryWalk()),
    act("editor.walkStop", stopStoryWalk),
  );
  card.append(row);
  if (item.maps.length > 1) {
    const pick = el("div", "story-walk-actions");
    item.maps.forEach((key) => pick.append(act("", () => storyReviewFocus(item, key),
      key === mapUI.current?.key ? "on" : "", mapUI.maps.find((m) => m.key === key)?.name || key)));
    card.append(pick);
  }
  if (!item.maps.includes(mapUI.current?.key)) storyReviewFocus(item, item.map);
  else if (walk.focused !== item.step.id) storyReviewFocus(item, mapUI.current.key);
}

// 그 맵으로 가서 이 단계 핀을 보여 줌 (맵이 바뀌는 동안 잠깐 기다림)
function storyReviewFocus(item, key) {
  const walk = mapEditor.walk;
  walk.focused = item.step.id;
  if (mapUI.current?.key !== key) chooseMap(key, false);
  let tries = 0;
  const wait = () => {
    if (mapEditor.walk !== walk) return;
    if (mapUI.current?.key === key && mapUI.leaflet && mapEditor.options?.story?.seed?.map === key) focusStoryStep(item.step.id, false);
    else if (++tries < 40) setTimeout(wait, 150);
  };
  wait();
}

// 결정: place = 장소 맞음('장소 불필요'에서 빼기), none = 장소 불필요 맞음(이 단계 핀 모두 지우기). 다시 누르면 취소
function storyReviewDecide(item, choice) {
  if (mapEditor.saving) return;
  const id = item.step.id;
  const unId = "unnoplace:" + id;
  const pins = storyPlacesFor(id).filter((p) => p.edit);
  const current = mapEditor.pending.get(unId) ? "place" : pins.some((p) => mapEditor.pending.get(editIdentity(p.edit))?.delete) ? "none" : null;
  // 먼저 이 단계의 이전 결정을 지움
  mapEditor.pending.delete(unId);
  pins.forEach((p) => { if (mapEditor.pending.get(editIdentity(p.edit))?.delete) mapEditor.pending.delete(editIdentity(p.edit)); });
  if (current !== choice) {
    if (choice === "place" && mapEditor.options?.unnoplace) {
      mapEditor.pending.set(unId, { edit: mapEditor.options.unnoplace, fields: { step: id }, pos: [0, 0, 0], base: [0, 0, 0],
        map: mapUI.current?.key, label: item.step.ko });
    }
    if (choice === "none") {
      pins.forEach((p) => mapEditor.pending.set(editIdentity(p.edit), { edit: p.edit, pos: [p.x, p.z, p.y], delete: true }));
    }
  }
  mapEditor.history = mapEditor.history.filter((h) => h.id !== unId && !pins.some((p) => h.id === editIdentity(p.edit)));
  refreshPositionEdits();
  refreshNewMarkers();
  drawStoryPins();
  if (current !== choice) advanceStoryWalk();
  else showStoryWalk();
}

function startStoryWalk() {
  cancelMarkerDraft();
  mapEditor.walk = { queue: storyWalkQueue(), index: 0 };
  showStoryWalk();
}

function stopStoryWalk() {
  mapEditor.walk = null;
  mapEditor.walkCard?.remove();
  mapEditor.walkCard = null;
  updatePositionEditor();
}

function storyWalkItem() {
  const walk = mapEditor.walk;
  return walk && walk.queue[walk.index];
}

function showStoryWalk() {
  const walk = mapEditor.walk;
  if (!walk) return;
  if (!mapEditor.walkCard) {
    mapEditor.walkCard = el("div", "story-walk");
    document.getElementById("tab-map").append(mapEditor.walkCard);
  }
  const card = mapEditor.walkCard;
  card.replaceChildren();
  const item = storyWalkItem();
  const act = (key, handler, extra = "", label = null) => {
    const node = el("button", "layer-toggle-btn " + extra, label ?? t(key));
    node.type = "button";
    node.addEventListener("click", handler);
    return node;
  };
  if (!item) {
    card.append(el("p", "", t(walk.review ? "editor.reviewDone" : "editor.walkDone")), act("editor.walkStop", stopStoryWalk));
    return;
  }
  if (walk.review) return showStoryReview(card, item, act);
  const mapName = walkMaps(item).map((key) => mapUI.maps.find((m) => m.key === key)?.name || key).join(" / ");
  card.append(el("div", "story-walk-head", `${walk.index + 1} / ${walk.queue.length} · ${item.ch.name} · ${mapName}`));
  card.append(el("strong", "", item.step.ko));
  if (item.twins.length) card.append(el("small", "story-walk-twins", t("editor.walkTwins", { n: item.twins.length + 1 })));
  if (item.step.en && item.step.en !== item.step.ko) card.append(el("small", "", item.step.en));
  const pics = el("div", "story-walk-pics");
  (item.section.images || []).forEach((img) => {
    const link = el("a");
    link.href = img.full || img.thumb;
    link.target = "_blank";
    link.rel = "noopener";
    link.title = img.caption || "";
    const pic = el("img");
    pic.src = img.thumb || img.full;
    pic.alt = img.caption || "";
    pic.loading = "lazy";
    link.append(pic);
    pics.append(link);
  });
  if (pics.childElementCount) card.append(pics);
  const maps = walkMaps(item);
  if (maps.length > 1) { // 맵이 여러 개인 단계: 장소가 있는 맵을 골라서 찍기
    const pick = el("div", "story-walk-actions");
    maps.forEach((key) => pick.append(act("", () => chooseMap(key, false),
      key === mapUI.current?.key ? "on" : "", mapUI.maps.find((m) => m.key === key)?.name || key)));
    card.append(pick);
  }
  // 이 단계가 쓰는 맵 고치기 (데이터에 빠진 맵 체크). 장소가 있는 맵·마지막 하나는 못 뺌
  if (mapEditor.options?.maps) {
    const box = el("details", "story-walk-maps");
    box.open = !!walk.mapsOpen;
    box.addEventListener("toggle", () => { walk.mapsOpen = box.open; });
    box.append(el("summary", "", t("editor.walkMaps")));
    mapUI.maps.forEach((m) => {
      const label = el("label");
      const check = el("input");
      check.type = "checkbox";
      check.checked = maps.includes(m.key);
      check.disabled = check.checked && (maps.length === 1 || storyWalkUsed(item, m.key));
      check.addEventListener("change", () => setWalkMaps(item, check.checked
        ? [...maps, m.key] : maps.filter((k) => k !== m.key), check.checked ? m.key : null));
      label.append(check, " " + m.name);
      box.append(label);
    });
    card.append(box);
  }
  const placed = storyPlacesFor(item.step.id).length + [...mapEditor.pending.values()]
    .filter((p) => p.edit?.seed?.add === "story" && p.fields?.step === item.step.id && !p.delete).length;
  card.append(el("p", "story-walk-hint", placed ? t("editor.walkPlaced", { n: placed }) : t("editor.walkHint")));
  const row = el("div", "story-walk-actions");
  row.append(
    act("editor.walkPrev", () => { walk.index = Math.max(0, walk.index - 1); showStoryWalk(); }),
    act("editor.walkNext", () => advanceStoryWalk(), "story-walk-next"),
    act("editor.walkNoPlace", storyWalkNoPlace, placed ? "story-walk-blocked" : ""), // 장소를 넣은 단계는 못 누름
    act("editor.walkStop", stopStoryWalk),
  );
  card.append(row);
  if (!maps.includes(mapUI.current?.key)) chooseMap(maps[0], false); // 그 단계의 맵으로
}

// 이 단계가 지금 쓰는 맵 (편집 중 고친 목록이 있으면 그것)
function walkMaps(item) {
  const pending = mapEditor.pending.get("maps:" + item.step.id)?.fields.maps;
  if (pending) return pending;
  // 저장 뒤에는 새로 받은 스토리 데이터의 맵 (step_maps 반영)
  const saved = story.data?.stepMaps?.[item.step.id];
  return Array.isArray(saved) && saved.length ? saved.filter((key) => mapUI.maps.some((m) => m.key === key)) : item.maps;
}

// 이 맵에 이 단계 장소가 있나 (저장된 것·저장 전 핀)
function storyWalkUsed(item, key) {
  return storyPlacesFor(item.step.id, key).length > 0 || [...mapEditor.pending.values()].some((p) =>
    p.edit?.seed?.add === "story" && p.fields?.step === item.step.id && p.map === key && !p.delete);
}

function setWalkMaps(item, maps, go) {
  const edit = mapEditor.options?.maps;
  if (mapEditor.saving || !edit || !maps.length) return showStoryWalk();
  const id = "maps:" + item.step.id;
  mapEditor.history.push({ id, before: mapEditor.pending.get(id) });
  const base = story.data?.stepMaps?.[item.step.id] || item.maps;
  const same = maps.length === base.length && maps.every((k) => base.includes(k));
  if (same) mapEditor.pending.delete(id); // 원래대로 돌아오면 바꿀 것 없음
  else mapEditor.pending.set(id, { edit, fields: { step: item.step.id, maps }, pos: [0, 0, 0], base: [0, 0, 0],
    map: mapUI.current?.key, label: item.step.ko, twins: item.twins.slice() });
  refreshPositionEdits();
  if (go) chooseMap(go, false); // 새로 체크한 맵으로 이동
  showStoryWalk();
}

function advanceStoryWalk() {
  const walk = mapEditor.walk;
  if (!walk) return;
  walk.index = Math.min(walk.queue.length, walk.index + 1);
  showStoryWalk();
}

function storyWalkPlace(latlng) {
  if (mapEditor.saving) return;
  const item = storyWalkItem();
  const edit = mapEditor.options?.story;
  if (!item || !edit || !walkMaps(item).includes(mapUI.current?.key) || edit.seed?.map !== mapUI.current?.key) return; // 맵이 바뀌는 중이면 무시 (옛 맵 열쇠로 넣지 않기)
  const x = Math.round(latlng.lng * 10) / 10;
  const z = Math.round(latlng.lat * 10) / 10;
  // 같은 단계를 거의 같은 자리에 또 누르면 무시 (겹친 핀이 쌓이지 않게)
  const same = [...mapEditor.pending.values()].some((p) => p.edit?.seed?.add === "story" && p.fields?.step === item.step.id
    && p.map === mapUI.current?.key && Math.hypot(p.pos[0] - x, p.pos[1] - z) < 3);
  if (same) return;
  // '장소 불필요'로 표시한 단계 (저장 전 포함)에는 못 넣음
  if ((story.data?.noPlace || []).includes(item.step.id) || [...mapEditor.pending.values()].some((p) =>
    p.edit?.seed?.add === "noplace" && p.fields?.step === item.step.id)) return;
  const pos = [x, z, floorHeight(x, z)];
  mapEditor.serial = (mapEditor.serial || 0) + 1;
  const id = "draft:" + mapEditor.serial;
  mapEditor.pending.set(id, { edit, fields: { step: item.step.id, candidate: false, note: "", y: pos[2] }, pos, base: pos.slice(),
    map: mapUI.current.key, label: item.step.ko, twins: item.twins.slice() });
  mapEditor.history.push({ id });
  refreshPositionEdits();
  showStoryWalk(); // 넣은 개수 갱신 (다음 단계는 [다음] 버튼으로)
}

// 지금 보고 있는 층의 높이: 그 층에 있는 가장 가까운 표시의 높이, 없으면 그 층 범위의 가운데
function floorHeight(x, z) {
  const heights = mapEditor.heights || [];
  const nearest = (list) => list.reduce((best, p) =>
    !best || Math.hypot(p[0] - x, p[1] - z) < Math.hypot(best[0] - x, best[1] - z) ? p : best, null);
  const here = nearest(heights.filter((p) => floorOf([x, z, p[2]]) === mapUI.floor));
  if (here) return here[2];
  const range = mapUI.floor != null ? mapUI.view?.layers?.[mapUI.floor]?.extents?.[0]?.height : null;
  if (Array.isArray(range) && range.every(Number.isFinite)) return Math.min(range[0] + 1.5, (range[0] + range[1]) / 2); // 그 층 바닥 조금 위
  return nearest(heights)?.[2] || 0;
}

function storyWalkNoPlace() {
  if (mapEditor.saving) return; // 저장 중에 넣으면 저장 결과 처리에서 같이 지워짐
  const item = storyWalkItem();
  const edit = mapEditor.options?.noplace;
  if (!item || !edit || !walkMaps(item).includes(mapUI.current?.key) || edit.seed?.map !== mapUI.current?.key) return;
  if (mapUI.maps.some((m) => storyWalkUsed(item, m.key))) return; // 장소를 넣은 단계 ('장소 불필요'와 모순)
  const already = [...mapEditor.pending.values()].some((p) => p.edit?.seed?.add === "noplace" && p.fields?.step === item.step.id);
  // (같은 글 단계들은 저장할 때 함께 들어감: twins)
  if (already) { advanceStoryWalk(); return; } // 이미 '장소 불필요'로 넣은 단계 (이전으로 돌아온 경우)
  mapEditor.serial = (mapEditor.serial || 0) + 1;
  const id = "draft:" + mapEditor.serial;
  mapEditor.pending.set(id, { edit, fields: { step: item.step.id }, pos: [0, 0, 0], base: [0, 0, 0], map: mapUI.current.key,
    label: item.step.ko, twins: item.twins.slice() });
  mapEditor.history.push({ id });
  refreshPositionEdits();
  advanceStoryWalk();
}

// 장소가 필요한 단계만 (장소 불필요 목록·이름 있는 탈출구로 대신하는 단계는 뺌)
function storyStepsNeedingPlace(steps) {
  const skip = new Set(story.data?.noPlace || []);
  const links = story.data?.extractLinks || {};
  return steps.filter((s) => !skip.has(s.id) && !links[s.id]);
}

// 이름 있는 탈출구로 대신하는 스토리 단계: 그 단계가 지금 할 일이면 탈출구 표시에 📖 표시·강조·설명을 붙임
function applyStoryExtractLinks() {
  // 이전에 연결했던 탈출구는 강조·순서까지 원래대로 (Codex 검수 62 #2)
  mapStory.markers.filter((item) => item.extract).forEach(({ marker }) => {
    marker.getElement()?.classList.remove("guide-highlight");
    marker.setZIndexOffset(baseZ(marker));
  });
  mapStory.markers = mapStory.markers.filter((item) => !item.extract);
  (mapUI.extractMarkers || []).forEach((item) => {
    item.marker.storyLinked = false;
    item.marker.getElement()?.classList.remove("story-extract");
    if (item.baseTip !== undefined) item.marker.setTooltipContent(item.baseTip);
  });
  if (!mapUI.current || mapEditor.active) return;
  const links = story.data?.extractLinks || {};
  storyDrawerChapters().forEach(({ s, entries }) => entries.forEach((entry) => {
    const link = links[entry.step.id];
    if (!link || link.map !== mapUI.current.key) return;
    const item = (mapUI.extractMarkers || []).find((x) => x.name === link.extract);
    if (!item) return;
    item.marker.storyLinked = true;
    item.marker.getElement()?.classList.add("story-extract");
    if (!item.marker.storyAddHook) {
      // 표시 켜기/끄기로 아이콘이 새로 만들어져도 📖 표시를 다시 붙임 (Codex 검수 62 #3)
      item.marker.storyAddHook = true;
      item.marker.on("add", () => {
        if (item.marker.storyLinked) item.marker.getElement()?.classList.add("story-extract");
      });
    }
    const tip = item.marker.getTooltip();
    if (tip) {
      if (item.baseTip === undefined) item.baseTip = tip.getContent();
      item.marker.setTooltipContent(item.baseTip + "<br>📖 " + escapeHtml(s.ch.name + " · " + entry.step.ko));
    }
    mapStory.markers.push({ marker: item.marker, id: entry.step.id, extract: true });
  }));
  highlightStoryStep();
}

function storyPinEntries() {
  if (mapEditor.active) {
    if (!mapUI.current) return []; // 맵을 고르기 전 (맵 없이 세면 모든 맵의 장소가 잡힘)
    // 편집 모드: 지금 할 단계가 아니어도 이 맵에 장소가 있는 단계는 모두 (옮기려면 보여야 함)
    return Object.keys(story.data?.places || {})
      .filter((id) => storyPlacesFor(id, mapUI.current?.key).length)
      .map(storyPlaceEntry).filter(Boolean);
  }
  const entries = storyPinSteps();
  // explicit 이 없고 맵도 아직 안 골라졌을 때 undefined === undefined 로 통과하지 않게
  if (mapStory.explicit && mapStory.explicit.map === mapUI.current?.key && !entries.some(({ entry }) => entry.step.id === mapStory.explicit.id)) {
    const found = storyPlaceEntry(mapStory.explicit.id);
    if (found) entries.push(found);
  }
  return entries;
}

function highlightStoryStep() {
  mapStory.markers.forEach(({ marker, id }) => {
    const selected = id === mapStory.focused;
    marker.getElement()?.classList.toggle("guide-highlight", selected);
    // 고르지 않은 표시는 원래 높이로 (스토리 핀은 계속 다른 표시 위, 연결된 탈출구는 탈출구 높이)
    marker.setZIndexOffset(selected ? 800 : baseZ(marker));
  });
  document.querySelectorAll("#drawer-list [data-story-step]").forEach((row) => {
    const selected = row.dataset.storyStep === mapStory.focused;
    row.classList.toggle("story-place-focused", selected);
    row.querySelector(".story-place-focus")?.setAttribute("aria-pressed", String(selected));
  });
}

function drawStoryPins() {
  mapUI.groups?.story?.clearLayers();
  mapUI.floorItems = (mapUI.floorItems || []).filter((item) => !item.story);
  mapStory.markers = [];
  if (mapUI.leaflet && mapUI.groups?.story) {
    storyPinEntries().forEach(({ s, entry, current }) => {
      storyPlacesFor(entry.step.id, mapUI.current.key).forEach((point) => {
        const pos = [point.x, point.z, point.y];
        const icon = markerIcon("story", "book-open", 20);
        if (point.candidate) icon.options.className += " guide-candidate";
        if (current === false) icon.options.className += " story-later"; // 현재 단계가 아닌 단계: 조금 흐리게
        const label = [s.ch.name, entry.step.ko, point.note,
          point.candidate ? t("map.guideCandidate") : ""].filter(Boolean).join(" · ");
        // 스토리 핀은 모든 표시 위 (2026-11-24 사용자: 비트코인 등에 깔려 안 보였음)
        const marker = L.marker(gamePos(pos), { icon, title: label, alt: label, zIndexOffset: STORY_PIN_Z });
        marker.baseZ = STORY_PIN_Z;
        marker.bindTooltip(escapeHtml(label), { direction: "top", offset: [0, -10] });
        const floor = floorOf(pos);
        marker.on("click", () => {
          if (!mapUI.drawerOpen) setDrawer(true);
          goToFloorOf(floor);
          mapStory.focused = entry.step.id;
          highlightStoryStep();
          const row = [...document.querySelectorAll("#drawer-list [data-story-step]")]
            .find((row) => row.dataset.storyStep === entry.step.id);
          unfoldDrawerFor(row);
          row?.scrollIntoView({ block: "center", behavior: reducedMotion() ? "instant" : "smooth" });
        });
        marker.addTo(mapUI.groups.story);
        bindPositionEditor(marker, { edit: point.edit, pos }, label);
        mapStory.markers.push({ marker, id: entry.step.id });
        mapUI.floorItems.push({ layer: marker, floor, story: true });
      });
    });
    applyFloor();
    highlightStoryStep();
  }
  applyStoryExtractLinks();
  renderLayerDrawer();
}

function focusStoryStep(id, toggle = true) {
  mapStory.focused = toggle && mapStory.focused === id ? null : id;
  const points = storyPlacesFor(id, mapUI.current?.key);
  const leaf = mapUI.leaflet;
  const linked = mapStory.markers.find((item) => item.id === id && item.extract);
  if (mapStory.focused && leaf && !points.length && linked) {
    const group = (mapUI.extractMarkers || []).find((x) => x.marker === linked.marker)?.group;
    if (group && !layerOn(group)) setLayer(group, true);
    collapseAll();
    leaf.setView(linked.marker.getLatLng(), Math.max(leaf.getZoom(), Math.min(4, leaf.getMaxZoom())), { animate: !reducedMotion() });
  }
  if (mapStory.focused && leaf && points.length) {
    if (!layerOn("story")) setLayer("story", true);
    goToFloorOf(floorOf([points[0].x, points[0].z, points[0].y]));
    collapseAll();
    fitArea(leaf, L.latLngBounds(points.map((p) => gamePos([p.x, p.z, p.y]))),
      { maxZoom: Math.min(4, leaf.getMaxZoom()), padding: 40, animate: true });
  }
  highlightStoryStep();
}

function openStoryPlaces(id) {
  const points = storyPlacesFor(id);
  const point = points.find((p) => mapUI.maps.some((m) => m.key === p.map));
  if (!point) return;
  goTab("map");
  mapStory.explicit = { id, map: point.map };
  mapStory.focused = id;
  chooseMap(point.map);
}

let mapGeneration = 0;
async function drawMap() {
  const generation = ++mapGeneration;
  if (mapEditor.active) {
    cancelMarkerDraft();
    mapEditor.options = null;
    mapEditor.newLayer?.remove();
    mapEditor.newLayer = mapEditor.newLeaf = null;
    updatePositionEditor(mapEditor.status?.textContent || "");
  }
  mapEditor.bindings.clear();
  clearMe(); // 내 위치 표시는 지도와 함께 사라짐 → 끝에서 다시 그림
  const m = mapUI.current;
  if (mapStory.explicit?.map !== m?.key) { mapStory.explicit = null; mapStory.focused = null; }
  if (mapUI.focusedPinMap !== m?.key) mapUI.focusedPin = null; // 고른 목표 번호는 그 맵에서만 (다른 맵의 같은 번호가 강조되지 않게)
  mapStory.markers = [];
  const box = document.getElementById("map-view");
  if (mapUI.leaflet) {
    mapUI.leaflet.remove();
    mapUI.leaflet = null;
  }
  // Leaflet 이 지도 칸에 단 'scroll' 처리기는 remove() 로 안 지워져서, 남아 있으면 옛 지도 전체가
  // 메모리에 계속 붙잡혀 있음 (지도를 그릴 때마다 약 180KB 누수, 2026-11-19 측정) → 여기서 직접 떼어 냄
  L.DomEvent.off(box);
  box.replaceChildren();
  mapUI.pins = {};
  mapUI.groups = {};
  mapUI.floorItems = [];
  mapUI.guideMarkers = {};
  mapUI.extractMarkers = [];
  mapUI.transits = []; // 이 맵의 트랜짓 표시 (서랍 '트랜짓으로 갈 수 있는 맵'이 씀)
  mapUI.dirty = false;
  renderLayerDrawer(); // 그림이 없거나 요청에 실패해도 이전 맵의 적 레이어를 지움
  if (!m) return;

  loadMapGuide(m); // 지도 그림과 독립적으로 표시, 같은 맵 재표시에는 다시 요청하지 않음
  const view = m.view;
  renderDrawer(); // 지도가 없어도 퀘스트 목록은 보여줌
  if (!view) {
    box.append(el("p", "empty", t("map.noImage")));
    return;
  }

  // 최대 확대 = 지도 데이터의 maxZoom (세관 5). 더 확대하면 SVG 가 수만 px 로 커져 건물 없는 맨바닥처럼 보임
  const maxZoom = Number(view.max_zoom) || 5;
  const displayRotation = view.svg ? (DISPLAY_ROTATION[m.key] || 0) : 0;
  const leaf = L.map(box, {
    crs: tdCRS(view, displayRotation),
    zoomSnap: 0.1,
    zoomAnimation: !reducedMotion(),
    fadeAnimation: !reducedMotion(),
    markerZoomAnimation: !reducedMotion(),
    inertia: !reducedMotion(),
    wheelPxPerZoomLevel: 120,
    attributionControl: false,
    zoomControl: false, // +/− 버튼 없음 (휠·두 손가락으로 확대). 손을 덜 대는 게 원칙
    // tarkov.dev 는 넓은 화면 기준이라, 우리 화면(세로로 긴 960 폭)에 전체가 들어오게 2단계 더 축소 허용
    minZoom: Math.max(0, (Number(view.min_zoom) || 1) - 2),
    maxZoom,
    // maxBounds(끌고 갈 수 있는 범위)는 회전된 지도에서 어긋나 지도를 밀어내서 쓰지 않음
  });
  mapUI.leaflet = leaf;
  const bounds = tdBounds(view.bounds);
  mapUI.bounds = bounds;
  mapUI.clusters = [];
  mapUI.view = view;
  mapUI.floor = null;       // null = 1층(기본). 숫자 = view.layers 의 몇 번째 층
  mapUI.floorItems = [];    // [{ layer, floor }] 다른 층이면 반투명
  mapUI.svgGroups = {};     // SVG 안의 층 그림 (id → g)
  mapUI.floorTile = null;
  // 표시 켜기/끄기 묶음 (왼쪽 서랍)
  mapUI.groups = {};
  LAYER_KEYS.forEach((k) => {
    mapUI.groups[k] = L.layerGroup();
    if (layerOn(k)) mapUI.groups[k].addTo(leaf);
  });
  leaf.setView([0, 0], 0, { animate: false });
  drawGuidePins();
  // 확대·축소하면 겹침이 달라지니 다시 계산
  leaf.on("zoomstart", collapseAll);
  leaf.on("zoomend", findClusters);
  leaf.on("click", collapseAll); // 지도 빈 곳을 누르면 펼친 핀을 다시 모음
  // 탭이 막 보인 순간에는 칸 크기가 덜 정해졌을 수 있어서, 크기를 다시 재고 맞춤
  // (requestAnimationFrame 은 창이 가려져 있으면 멈춰서 setTimeout 사용)
  setTimeout(() => {
    if (mapUI.leaflet !== leaf) return;
    fitFull(leaf, true);
    if (mapStory.focused) focusStoryStep(mapStory.focused, false);
  }, 0);

  if (view.svg) {
    // SVG 지도: 여러 층 중 기본 층만 보이게 (tarkov.dev 와 같은 방식)
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    L.svgOverlay(svg, tdBounds(view.svg_bounds || view.bounds), { className: "map-svg" }).addTo(leaf);
    try {
      const res = await fetch(view.svg);
      if (!res.ok) throw new Error();
      const text = await res.text();
      const parsed = new DOMParser().parseFromString(text, "image/svg+xml");
      if (parsed.documentElement.localName !== "svg" || parsed.querySelector("parsererror")) throw new Error();
      if (mapUI.leaflet !== leaf) return; // 그사이 다른 맵으로 바뀜
      svg.innerHTML = text;
      const inner = svg.querySelector("svg");
      if (inner) {
        svg.setAttribute("viewBox", inner.getAttribute("viewBox"));
        [...inner.children].filter((c) => c.nodeName === "g" && c.id).forEach((g) => {
          const base = g.id === view.svg_layer || g.dataset.keepWithGroup === view.svg_layer;
          if (base) g.classList.add("base-layer");
          else g.style.display = "none";
          mapUI.svgGroups[g.id] = g;
        });
        rotateMapSvg(svg, inner, displayRotation);
        // 그림이 늦게 와도 그사이 고른 층(예: 스토리 '지도에서 보기')을 그대로 보여 줌
        if (mapUI.floor != null) setFloor(mapUI.floor);
      }
    } catch (error) {
      if (generation !== mapGeneration) return;
      box.append(el("p", "map-error", t("map.imageFailed")));
    }
  } else if (view.tiles) {
    L.tileLayer(view.tiles, {
      tileSize: view.tile_size,
      bounds,
      minNativeZoom: Number(view.min_zoom) || 1,
      maxNativeZoom: maxZoom,
      maxZoom,
    }).addTo(leaf);
  }

  if (generation !== mapGeneration) return;
  drawPins();
  drawFeatures(leaf, m);
  drawFloorControl(box, view);
  drawMe(mapMe.moveTo); // 내 위치 (지도를 안 보는 동안 찍힌 위치면 층도 맞춤)
  if (!mapMe.pos || m.ids.includes(mapMe.pos.map_id)) mapMe.moveTo = false; // 그 맵을 그렸을 때만 예약을 끝냄
  drawShotHint();
  // 출처: 지도 오른쪽 아래에 작게 (CC BY-NC-SA 는 출처 표시가 조건)
  const credit = el("p", "map-credit");
  box.append(credit);
  credit.append(t("map.credit"));
  const author = el("a", "", view.author || "tarkov.dev");
  author.href = view.author_link || "https://tarkov.dev/";
  author.target = "_blank";
  credit.append(author);
  if (view.svg) { // SVG 지도는 라이선스(CC BY-NC-SA 4.0) 표시
    const license = el("a", "", "tarkov.dev SVG Maps (CC BY-NC-SA 4.0)");
    license.href = "https://github.com/the-hideout/tarkov-dev-svg-maps";
    license.target = "_blank";
    credit.append(" · ", license);
  } else {
    credit.append(" · tarkov.dev");
  }
}

// 목표 위치에 번호 핀 (같은 번호 = 서랍의 같은 목표)
function drawPins() {
  const leaf = mapUI.leaflet;
  if (!leaf) return;
  collapseAll();
  if (mapUI.groups && mapUI.groups.quests) mapUI.groups.quests.clearLayers(); // 옛 핀 비우기
  mapUI.pinMarkers = [];
  mapUI.floorItems = (mapUI.floorItems || []).filter((x) => !x.quest);
  const numbered = numberedObjectives();
  syncFocusedPin(numbered);
  numbered.forEach(({ n, quest, objective, spots }) => {
    // 레이드 중 임시로 숨긴 목표: 핀·구역을 그리지 않음. 번호는 그대로 (남은 핀 번호가 바뀌지 않게)
    if (isGoalHidden(quest, objective)) {
      delete mapUI.pins[n];
      return;
    }
    const color = traderColor(quest.trader_key);
    const layers = [];
    spots.forEach((spot) => {
      if (spot.outline && spot.outline.length > 2 && outlineMatches(spot)) {
        // 처치 구역은 '이 근처' 라서 점선으로 옅게, 나머지(가야 할 곳·설치 장소)는 진하게
        layers.push(L.polygon(spot.outline.map(gamePos), spot.area
          ? { color, weight: 1.5, dashArray: "6 6", fillColor: color, fillOpacity: 0.05, interactive: false }
          : { color, weight: 2, fillColor: color, fillOpacity: 0.18, interactive: false }));
      }
      spot.points.forEach((p) => {
        const marker = L.marker(gamePos(p), {
          icon: L.divIcon({
            className: "quest-pin",
            html: `<span style="--pin:${color}">${n}</span>`,
            iconSize: [24, 24],
            iconAnchor: [12, 12],
          }),
          zIndexOffset: QUEST_PIN_Z, // 퀘스트 핀은 탈출구·열쇠 같은 표시보다 항상 위
        });
        marker.bindTooltip(`<b>${escapeHtml(quest.name)}</b><br>${escapeHtml(objective.text)}`, { direction: "top", offset: [0, -10] });
        const item = { marker, home: L.latLng(gamePos(p)), n, cluster: null, floor: floorOf(p) };
        mapUI.pinMarkers.push(item);
        mapUI.floorItems.push({ layer: marker, floor: item.floor, quest: true });
        // 누르기: 겹친 핀이면 확대 없이 그 자리에서 바로 펼침(2026-11-25 사용자: 끝까지 확대해야 갈라지던 것),
        // 혼자 있는 핀·펼친 핀이면 서랍을 열고 그 목표로. 빈 지도를 누르면 다시 모임
        marker.on("click", (e) => {
          L.DomEvent.stopPropagation(e);
          if (item.floor !== mapUI.floor) setFloor(item.floor); // 다른 층 핀 → 그 층으로
          const c = item.cluster;
          if (c && c.items.length > 1 && !c.open) openCluster(c);
          else showInDrawer(n);
        });
        layers.push(marker);
      });
    });
    mapUI.pins[n] = L.featureGroup(layers).addTo(mapUI.groups.quests);
  });
  findClusters();
  highlightQuestPin();
  applyFloor();
  scheduleMapRefresh();
}

// 퀘스트가 완료·숨김으로 빠지면 번호가 다시 매겨짐 → 고른 목표를 번호가 아니라 목표(퀘스트 id + 순서)로 기억했다가
// 새 번호를 찾아 줌. 그 목표가 없어졌으면 고른 상태를 풂 (엉뚱한 목표에 강조가 붙지 않게)
function syncFocusedPin(numbered) {
  if (mapUI.focusedPin == null) return;
  const found = mapUI.focusedGoal && numbered.find((x) => goalHideKey(x.quest, x.objective) === mapUI.focusedGoal);
  mapUI.focusedPin = found ? found.n : null;
  if (!found) mapUI.focusedGoal = null;
}

// 고른 목표의 번호 핀에 강조 테두리 (서랍 줄만 강조되던 것, 2026-11-25 사용자). 스토리·공략 핀과 같은 모양(.guide-highlight)
function highlightQuestPin() {
  (mapUI.pinMarkers || []).forEach((item) => {
    const selected = item.n === mapUI.focusedPin;
    item.marker.getElement()?.classList.toggle("guide-highlight", selected);
    // 펼친 겹친 핀(1000)은 그대로 두고, 나머지만 고른 핀을 위로
    if (!item.cluster || !item.cluster.open) item.marker.setZIndexOffset(selected ? 800 : QUEST_PIN_Z);
  });
}

// ---------- 레이드 중 목표 임시 숨기기 (2026-11-25 사용자) ----------
// 주운 퀘스트 아이템 등 '이번 레이드에서 해 둔' 목표의 핀을 잠깐 치운다 (핀이 커서 한곳에 겹침).
// 완료 표시가 아님: 진행 기록에는 아무것도 쓰지 않고 화면에만 둔다. 진짜 완료는 게임 로그가 알려 줌
// 열쇠 = 퀘스트 id + 목표 순서 (목표에는 따로 id 가 없음. 레이드 중에는 퀘스트 목록을 다시 읽지 않아 순서가 안 바뀜)
// 풀리는 때 (2026-11-25 사용자): 로비로 돌아오면 모두 풀림 (죽어서 잃었을 수 있음). 트랜짓(다른 맵으로 바로 이어지는 레이드)은 유지.
// 레이드 중에 프로그램을 껐다 켜도 같은 레이드면 유지 → 이 PC 의 화면 저장소에 '레이드 시작 시각 + 열쇠'만 적어 둠
const GOAL_HIDE_STORAGE = "tarkovhelper.goalHides";
const mapHide = { keys: new Set(), raid: null, mapId: null, restored: false };
function saveGoalHides() {
  try {
    if (mapHide.keys.size) localStorage.setItem(GOAL_HIDE_STORAGE, JSON.stringify({ raid: mapHide.raid, keys: [...mapHide.keys] }));
    else localStorage.removeItem(GOAL_HIDE_STORAGE);
  } catch (e) { /* 저장소를 못 써도 이번 실행 동안은 동작 */ }
}
function redrawGoalHides() {
  if (!mapUI.leaflet || mapEditor.active) return;
  drawPins();
  renderDrawer();
}
function clearGoalHides() {
  const had = mapHide.keys.size > 0;
  mapHide.keys.clear();
  saveGoalHides();
  if (had) redrawGoalHides();
}
// 게임 상태가 바뀔 때마다: 새 레이드가 트랜짓인지(유지) 같은 맵 재입장인지(풂) 가르고, 껐다 켠 뒤 첫 레이드 상태에서 되살림
function updateGoalHidesForState(s) {
  if (s.status !== "raid" || !s.since) return; // 로비로 돌아온 것은 onGameState 의 로비 대기(LOBBY_WAIT_MS)가 끝날 때 clearGoalHides
  if (!mapHide.restored) {
    mapHide.restored = true;
    try {
      const saved = JSON.parse(localStorage.getItem(GOAL_HIDE_STORAGE) || "null");
      if (saved && saved.raid === s.since && Array.isArray(saved.keys)) {
        saved.keys.forEach((key) => { if (typeof key === "string") mapHide.keys.add(key); });
        if (mapHide.keys.size) redrawGoalHides();
      }
    } catch (e) { /* 깨진 저장 값은 버림 (아래에서 지워짐) */ }
  } else if (mapHide.raid !== s.since && mapHide.mapId === s.map_id) {
    // 직전 레이드와 같은 맵에 새로 들어옴 = 트랜짓이 아니라 죽거나 나온 뒤 재입장 (로비 대기가 끝나기 전에 들어온 경우) → 풂
    clearGoalHides();
  }
  mapHide.raid = s.since;
  mapHide.mapId = s.map_id;
  saveGoalHides();
}
function goalHideKey(quest, objective) { return `${quest.id}:${quest.objectives.indexOf(objective)}`; }
function isGoalHidden(quest, objective) { return mapHide.keys.has(goalHideKey(quest, objective)); }
// 숨기기 버튼은 레이드 중에, 지금 보는 지도가 그 레이드 맵일 때만
function canHideGoals() {
  const s = topbar.state;
  return s.status === "raid" && !!mapUI.current && mapUI.current.ids.includes(s.map_id);
}
function toggleGoalHidden(quest, objective, n) {
  const key = goalHideKey(quest, objective);
  if (mapHide.keys.has(key)) mapHide.keys.delete(key);
  else {
    mapHide.keys.add(key);
    if (mapUI.focusedPin === n) mapUI.focusedPin = null; // 숨긴 목표는 고른 상태도 풂
  }
  saveGoalHides();
  drawPins();
  renderDrawer();
}

// ---------- 먹을 것 (지도 위쪽 줄에 아이콘 + 바닥 생성 위치가 있으면 지도에 ◆) ----------
const ICON_URL = (id) => `https://assets.tarkov.dev/${id}-icon.webp`; // tarkov.dev 아이템 아이콘

// '윌스턴 담배 (Wilston cigarettes) / Malboro …' → '윌스턴 담배 등'
function shortItem(text) {
  const parts = text.split(" / ");
  let name = parts[0].replace(/\s*\([^)]*\)/g, "").trim();
  if (parts.length > 1 || /외 \d+종|and \d+ more/.test(text)) name += t("map.etc"); // i18n-ok: 서버가 만든 준비물 글자(server.andMore)와 비교
  return name;
}

// 먹을 것: 퀘스트·스토리는 인레이드만, 은신처 재료는 인레이드가 아니어도 전부 (사용자 결정: 레벨마다 거래로 구할 수 있는 게 달라서)
function lootItems() {
  if (typeof neededItems !== "function") return [];
  return neededItems().filter((i) => i.fir || i.from.some((f) => f.type === "hideout"));
}

function renderLootBar() {
  const bar = document.getElementById("loot-bar");
  bar.replaceChildren();
  const items = lootItems();
  bar.hidden = !items.length;
  if (!items.length) return;
  bar.append(el("span", "loot-label", t("map.loot")));
  const row = el("div", "loot-items");
  bar.append(row);
  items.forEach((item) => {
    const chip = el("span", "loot-icon"); // 누르는 기능 없음 (지도 표시를 없앰, 2026-11-25 사용자)
    chip.dataset.key = item.text;
    // 마우스를 올리면 전체 이름 + 개수 + 필요한 곳
    chip.title = `${item.text}${item.count > 1 ? ` ×${item.count}` : ""}\n` + t("map.lootNeededBy", { list: item.from.map((f) => f.name).join(", ") });
    const id = (item.ids || [])[0];
    if (id) {
      const img = el("img");
      img.src = ICON_URL(id);
      img.alt = shortItem(item.text);
      img.onerror = () => img.replaceWith(el("span", "loot-text", shortItem(item.text)));
      chip.append(img);
    } else {
      chip.append(el("span", "loot-text", shortItem(item.text))); // 아이콘을 모르면(스토리 아이템 등) 짧은 이름
    }
    if (item.count > 1) chip.append(el("b", "loot-count", `×${item.count}`));
    row.append(chip);
  });
  // 두 줄 이상일 때만 보이는 접기/펼치기 (접으면 첫 줄만, 상태는 기억)
  const toggle = el("button", "loot-toggle");
  toggle.type = "button";
  toggle.hidden = true;
  toggle.append(icon("chevron-down", 14), el("span", "loot-toggle-text"));
  toggle.addEventListener("click", () => {
    lootCollapsed = !lootCollapsed;
    try { localStorage.setItem(LOOT_COLLAPSE_STORAGE, lootCollapsed ? "1" : "0"); } catch (e) { /* 저장소를 못 쓰는 창 */ }
    updateLootToggle();
  });
  bar.append(toggle);
  updateLootToggle();
}

const LOOT_COLLAPSE_STORAGE = "tarkovhelper.lootCollapsed";
let lootCollapsed = false;
try { lootCollapsed = localStorage.getItem(LOOT_COLLAPSE_STORAGE) === "1"; } catch (e) { /* 무시 */ }

// 첫 줄 아래로 넘어간 아이콘 수를 세서 버튼 글자·보이기를 정함 (창 크기가 바뀌어도 다시 계산)
function updateLootToggle() {
  const bar = document.getElementById("loot-bar");
  const toggle = bar.querySelector(".loot-toggle");
  const chips = [...bar.querySelectorAll(".loot-items > .loot-icon")];
  if (!toggle || !chips.length || bar.hidden) return;
  const firstTop = chips[0].offsetTop;
  const hiddenCount = chips.filter((c) => c.offsetTop > firstTop).length;
  toggle.hidden = hiddenCount === 0;
  const collapsed = lootCollapsed && hiddenCount > 0;
  bar.classList.toggle("collapsed", collapsed);
  toggle.querySelector(".loot-toggle-text").textContent = collapsed ? t("map.lootExpand", { n: hiddenCount }) : t("map.lootCollapse");
  toggle.setAttribute("aria-expanded", String(!collapsed));
}

// ---------- 겹친 핀: 묶지 않고 그 자리에 겹쳐 둠 (묶음 표시는 지도를 가려서 안 씀) ----------
// 겹친 핀은 테두리 링으로 '여러 개'임을 알리고, 누르면 그 자리를 확대해서 떨어뜨림.
// 위치가 완전히 같아서 가장 크게 확대해도 겹치면 그때만 동그랗게 펼침 (지도 빈 곳을 누르면 다시 모임)
const CLUSTER_PX = 20; // 화면에서 이만큼 가까우면 겹친 것으로 봄
const QUEST_PIN_Z = 500; // 퀘스트 핀 높이 (표시물 위, 펼친 핀(1000) 아래)
const STORY_PIN_Z = 600; // 스토리 핀 높이 (퀘스트 핀보다 위, 겹친 핀을 펼쳤을 때(1000)만 그 아래)
// 겹쳤을 때 위에 오는 순서 (2026-11-24 사용자: 중요한 표시일수록 위, 규칙 docs/notes/map-screen.md)
// 할 일(스토리 600 > 퀘스트 500 > 공략 400) > 나가는 길·위험·열쇠 자리 > 참고(잠긴 문·비트코인·BTR·먹을 것)
const GUIDE_PIN_Z = 400;
const MARKER_Z = { extracts: 300, scavExtracts: 300, transits: 280, bossZones: 250, blackDivision: 250, keySpawns: 200, keys: 100, bitcoin: 80, btrStops: 60 };
// 강조(800)가 풀렸을 때 돌아갈 원래 높이
function baseZ(marker) { return marker.baseZ || 0; }

function findClusters() {
  const leaf = mapUI.leaflet;
  if (!leaf) return;
  collapseAll();
  const clusters = [];
  (mapUI.pinMarkers || []).forEach((item) => {
    const pt = leaf.latLngToContainerPoint(item.home);
    let c = clusters.find((x) => x.pt.distanceTo(pt) < CLUSTER_PX);
    if (!c) {
      c = { pt, items: [], open: false, legs: [], timer: null };
      clusters.push(c);
    }
    c.items.push(item);
    item.cluster = c;
  });
  clusters.forEach((c) => c.items.forEach((item) => {
    const icon = item.marker.getElement();
    if (icon) icon.classList.toggle("stacked", c.items.length > 1);
  }));
  mapUI.clusters = clusters;
}

function openCluster(c) {
  clearTimeout(c.timer);
  if (c.open || c.items.length < 2 || !mapUI.leaflet) return;
  (mapUI.clusters || []).forEach((other) => other !== c && closeCluster(other));
  const leaf = mapUI.leaflet;
  const count = c.items.length;
  const radius = 22 + count * 3;
  // 지도를 끌어서 옮겼을 수 있으니 펼치는 순간의 화면 위치로 다시 계산 (겹친 핀들의 가운데)
  const pts = c.items.map((item) => leaf.latLngToContainerPoint(item.home));
  c.pt = L.point(pts.reduce((s, p) => s + p.x, 0) / count, pts.reduce((s, p) => s + p.y, 0) / count);
  c.open = true;
  c.items.forEach((item, i) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / count;
    const spot = leaf.containerPointToLatLng(c.pt.add(L.point(Math.cos(angle) * radius, Math.sin(angle) * radius)));
    item.marker.setLatLng(spot);
    item.marker.setZIndexOffset(1000);
    // 펼친 핀과 원래 위치를 가는 선으로 이음
    c.legs.push(L.polyline([item.home, spot], { color: "#d8d4c8", weight: 1, opacity: 0.7, interactive: false }).addTo(leaf));
  });
}

function closeCluster(c) {
  clearTimeout(c.timer);
  if (!c.open) return;
  c.open = false;
  c.items.forEach((item) => {
    item.marker.setLatLng(item.home);
    item.marker.setZIndexOffset(item.n === mapUI.focusedPin ? 800 : QUEST_PIN_Z);
  });
  c.legs.forEach((leg) => leg.remove());
  c.legs = [];
}

function collapseAll() {
  (mapUI.clusters || []).forEach(closeCluster);
}

function escapeHtml(text) {
  const d = document.createElement("div");
  d.textContent = text || "";
  return d.innerHTML;
}

// 영역 네모가 핀 위치와 동떨어져 있으면(데이터 오류, 예: 삼림 '잠겨 있는 벙커') 영역은 안 그림
function outlineMatches(spot) {
  const cx = spot.outline.reduce((s, p) => s + p[0], 0) / spot.outline.length;
  const cz = spot.outline.reduce((s, p) => s + p[1], 0) / spot.outline.length;
  const [px, pz] = spot.points[0];
  return Math.hypot(cx - px, cz - pz) <= 100;
}

// 서랍과 지도에 같은 번호: 목표마다 번호 (이 맵에 위치가 있는 목표만)
function numberedObjectives() {
  const m = mapUI.current;
  const result = [];
  let n = 0;
  mapQuests(m.name).forEach((quest) => {
    quest.objectives.forEach((objective) => {
      const spots = (objective.where || []).filter((w) => w.map === m.name);
      if (spots.length) result.push({ n: ++n, quest, objective, spots });
    });
  });
  return result;
}

// ---------- 맵별 공략 (별도 진행 저장 없이 기존 스토리 기록 사용) ----------
const mapGuide = { key: null, data: null, prep: null, controller: null, revision: 0, focused: null,
  failed: false, retryAt: 0, retryTimer: null };
const GUIDE_ICONS = { key_spawn: "key", panel: "layers", toolset: "settings", explosive: "triangle-alert",
  door: "lock", lock: "lock", building: "package", start: "door-open" };

function currentGuide() {
  return mapGuide.key === mapUI.current?.key && (mapGuide.data?.steps?.length ||
    (mapEditor.active && Object.keys(mapGuide.data?.points || {}).length)) ? mapGuide.data : null;
}

function guideText(item, prefix = "") {
  const lang = document.documentElement.lang === "en" ? "en" : "ko";
  return item?.[prefix + lang] || item?.[prefix + "ko"] || item?.[prefix + "en"] || "";
}

async function loadMapGuide(m) {
  if (mapGuide.key === m.key && (!mapGuide.failed || mapGuide.controller || performance.now() < mapGuide.retryAt)) return;
  clearTimeout(mapGuide.retryTimer);
  mapGuide.retryTimer = null;
  mapGuide.controller?.abort();
  const controller = new AbortController();
  const revision = ++mapGuide.revision;
  Object.assign(mapGuide, { key: m.key, data: null, prep: null, focused: null, controller, failed: false });
  renderLayerDrawer();
  let failed = false;
  async function read(key) {
    try {
      const res = await fetch('/api/guide/' + encodeURIComponent(key), { signal: controller.signal });
      if (!res.ok) throw new Error();
      const data = await res.json();
      if (!Array.isArray(data?.steps)) throw new Error();
      return Array.isArray(data?.steps) && (data.steps.length ||
        (mapEditor.active && Object.keys(data.points || {}).length)) ? data : null;
    } catch (error) { failed = true; return null; } // 안내를 못 받아도 기존 지도/스토리는 유지
  }
  const [data, prep] = await Promise.all([read(m.key), m.key === "shoreline" ? read("terminal") : null]);
  if (revision !== mapGuide.revision || mapUI.current?.key !== m.key) return;
  Object.assign(mapGuide, { data, prep, controller: null, failed, retryAt: performance.now() + 2000 });
  if (failed && !controller.signal.aborted) {
    mapGuide.retryTimer = setTimeout(() => {
      mapGuide.retryTimer = null;
      if (revision === mapGuide.revision && mapUI.current?.key === m.key && currentTab() === "map") loadMapGuide(m);
    }, 2100); // 타이머는 하나만, 맵 변경/성공 시 해제. 정상 빈 공략은 다시 받지 않는다.
  }
  drawGuidePins();
  renderDrawer();
  maybeOpenGuideDrawer();
}

function guidePrepDetails(data, title, key) {
  const details = el("details", "guide-prep");
  details.dataset.key = key;
  details.append(el("summary", "", title));
  const items = el("ul", "guide-notes");
  (data.prep || []).forEach((item) => {
    const row = el("li", "", guideText(item));
    appendGuideWarnings(row, item.warnings);
    items.append(row);
  });
  details.append(items);
  return details;
}

function appendGuideWarnings(parent, warnings) {
  if (!warnings?.length) return;
  const list = el("ul", "guide-warnings");
  warnings.forEach((warning) => list.append(el("li", "", guideText(warning))));
  parent.append(list);
}

function renderDrawerGuide(list, m) {
  const data = currentGuide();
  if (!data) {
    // 입장 시점/장비는 로그로 알 수 없음: The Ticket 진행 중이면 준비 안내를 제공한다.
    const ch = typeof story !== "undefined" && story.data?.chapters.find((ch) => ch.id === "the-ticket");
    if (m.key !== "shoreline" || !mapGuide.prep?.steps?.length || !ch || !isChapterStarted(ch) || isChapterDone(ch.id)) return 0;
    const row = el("li", "drawer-guide");
    row.append(guidePrepDetails(mapGuide.prep, t("map.guideEntry"), "guide/shoreline/prep"));
    list.append(row);
    return 1;
  }
  list.append(foldKey(el("li", "drawer-divider drawer-section-title guide-title", t("map.guideTitle")), "guide"));
  const section = el("li", "drawer-guide");
  section.append(guidePrepDetails(data, t("map.guidePrep"), "guide/" + m.key + "/prep"));
  const steps = el("ol", "guide-steps");
  data.steps.forEach((step, index) => {
    const row = el("li", "guide-step");
    row.dataset.guideStep = step.id;
    const head = el("div", "guide-step-head");
    head.append(el("span", "guide-number", String(index + 1)));
    if (step.story_step) {
      const box = document.createElement("input");
      box.type = "checkbox";
      box.disabled = typeof story === "undefined" || !story.data || story.migrating;
      box.checked = !box.disabled && isStepDone({ id: step.story_step });
      row.classList.toggle("done", box.checked);
      box.title = t(box.checked ? "story.stepUndo" : "story.stepDone");
      box.setAttribute("aria-label", box.title + ": " + guideText(step));
      box.dataset.guideControl = step.id + "/check";
      box.addEventListener("change", () => saveStoryChanges({ [step.story_step + "!"]: box.checked ? "done" : null }));
      head.append(box);
    }
    const hasPoints = (step.points || []).some((id) => data.points?.[id]);
    const text = el("button", "guide-step-text", guideText(step));
    text.type = "button";
    if (hasPoints) text.title = t("map.guideFocus");
    text.dataset.guideControl = step.id + "/focus";
    text.setAttribute("aria-pressed", String(mapGuide.focused === step.id));
    text.addEventListener("click", () => focusGuideStep(step.id));
    head.append(text);
    row.append(head);
    appendGuideWarnings(row, step.warnings);
    row.classList.toggle("focused", mapGuide.focused === step.id);
    steps.append(row);
  });
  section.append(steps);
  appendGuideWarnings(section, data.warnings);
  const source = Object.values(data.sources || {}).find((item) => /^https?:\/\//i.test(item.link || ""));
  const credit = el("p", "guide-credit");
  const link = el(source ? "a" : "span", "", t("map.guideSource", { checked: data.checked || "" }));
  if (source) { link.href = source.link; link.target = "_blank"; link.rel = "noopener noreferrer"; }
  credit.append(link);
  section.append(credit);
  list.append(section);
  return 1;
}

function drawGuidePins() {
  for (const [id, binding] of mapEditor.bindings) if (binding.guide) mapEditor.bindings.delete(id);
  mapUI.groups?.guide?.clearLayers();
  mapUI.guideMarkers = {};
  mapUI.floorItems = (mapUI.floorItems || []).filter((item) => !item.guide);
  const data = currentGuide();
  if (data && mapUI.leaflet && mapUI.groups?.guide) {
    Object.entries(data.points || {}).forEach(([id, point]) => {
      if ((!mapEditor.active && point.kind === "extract") || (!GUIDE_ICONS[point.kind] && point.kind !== "extract") || !Array.isArray(point.pos)) return;
      const icon = markerIcon(point.kind === "lock" ? "lock" : "guide-" + point.kind, (GUIDE_ICONS[point.kind] || "door-open"), point.kind === "lock" ? 16 : 20);
      if (point.candidate) icon.options.className += " guide-candidate";
      const label = guideText(point, "label_") + (point.candidate ? " · " + t("map.guideCandidate") : "");
      const z = point.kind === "lock" ? MARKER_Z.keys : GUIDE_PIN_Z;
      const marker = L.marker(gamePos(point.pos), { icon, title: label, alt: label, zIndexOffset: z });
      marker.baseZ = z;
      marker.bindTooltip(escapeHtml(label), { direction: "top", offset: [0, -10] });
      const floor = floorOf(point.pos);
      marker.on("click", () => { if (mapEditor.active) return; goToFloorOf(floor); showGuidePoint(id); });
      bindPositionEditor(marker, point, label, true);
      marker.addTo(mapUI.groups.guide);
      mapUI.guideMarkers[id] = marker;
      mapUI.floorItems.push({ layer: marker, floor, guide: true });
      if (mapEditor.active && point.edit) mapEditor.bindings.get(editIdentity(point.edit))?.refresh();
    });
    applyFloor();
    highlightGuideStep();
  }
  drawStoryPins();
}

// 탈출구는 기존 표시를 재사용한다. 이름이 맞지 않아도 단계의 좌표 이동은 가능하다.
function guideExtractIds(name) {
  return Object.entries(currentGuide()?.points || {}).filter(([, point]) => point.kind === "extract"
    && [point.label_en, point.label_ko].some((label) => label?.split(/[·:]/)[0].trim() === name)).map(([id]) => id);
}

function highlightGuideStep() {
  const step = currentGuide()?.steps.find((step) => step.id === mapGuide.focused);
  const ids = new Set(step?.points || []);
  const mark = (marker, selected) => {
    marker.getElement()?.classList.toggle("guide-highlight", selected);
    marker.setZIndexOffset(selected ? 800 : baseZ(marker));
  };
  Object.entries(mapUI.guideMarkers || {}).forEach(([id, marker]) => mark(marker, ids.has(id)));
  (mapUI.extractMarkers || []).forEach(({ marker, name }) => mark(marker, guideExtractIds(name).some((id) => ids.has(id))));
  document.querySelectorAll("#drawer-list [data-guide-step]").forEach((row) => {
    const selected = row.dataset.guideStep === mapGuide.focused;
    row.classList.toggle("focused", selected);
    row.querySelector("button")?.setAttribute("aria-pressed", String(selected));
  });
}

function focusGuideStep(id) {
  const data = currentGuide();
  const step = data?.steps.find((step) => step.id === id);
  if (!step) return;
  mapGuide.focused = mapGuide.focused === id ? null : id;
  const points = (step.points || []).map((key) => data.points?.[key]).filter(Boolean);
  const leaf = mapUI.leaflet;
  if (mapGuide.focused && leaf && points.length) {
    if (!layerOn("guide")) setLayer("guide", true);
    (mapUI.extractMarkers || []).forEach(({ name, group }) => {
      if (guideExtractIds(name).some((key) => step.points.includes(key)) && !layerOn(group)) setLayer(group, true);
    });
    goToFloorOf(floorOf(points[0].pos));
    collapseAll();
    fitArea(leaf, L.latLngBounds(points.map((point) => gamePos(point.pos))), { maxZoom: Math.min(4, leaf.getMaxZoom()), padding: 40, animate: true });
  }
  highlightGuideStep();
}

function showGuidePoint(id) {
  const step = currentGuide()?.steps.find((step) => (step.points || []).includes(id));
  if (!step) return;
  if (!mapUI.drawerOpen) setDrawer(true);
  mapGuide.focused = step.id;
  highlightGuideStep();
  const row = [...document.querySelectorAll("#drawer-list [data-guide-step]")].find((row) => row.dataset.guideStep === step.id);
  unfoldDrawerFor(row);
  row?.scrollIntoView({ block: "center", behavior: reducedMotion() ? "instant" : "smooth" });
}

function updateGuideRaid(s, inRaid) {
  if (!inRaid) { mapUI.guideRaid = null; return; }
  const previous = mapUI.guideRaid;
  // loading → raid의 since 변경은 같은 판. 같은 상태의 새 since는 놓친 로비 뒤 새 판.
  if (!previous || previous.mapId !== s.map_id || (previous.status === s.status && previous.since !== s.since)
      || (previous.status === "raid" && s.status === "loading")) {
    mapUI.guideRaid = { mapId: s.map_id, status: s.status, since: s.since, opened: false, pending: true };
  } else Object.assign(previous, { status: s.status, since: s.since });
}

function maybeOpenGuideDrawer() {
  const raid = mapUI.guideRaid;
  if (!raid?.pending || raid.opened || mapUI.current?.key !== "terminal" || !currentGuide()
      || mapUI.autoMapId !== raid.mapId || !mapUI.current.ids.includes(raid.mapId)) return;
  raid.opened = true;
  raid.pending = false;
  if (!mapUI.drawerOpen) setDrawer(true);
}

// ---------- 오른쪽 서랍 ----------
function renderDrawer() {
  const m = mapUI.current;
  const list = document.getElementById("drawer-list");
  const guideFocus = list.contains(document.activeElement) ? document.activeElement.dataset.guideControl : null;
  // 다시 그려도 펼친 위치 설명·선택한 목표·스크롤은 그대로 (Codex 41 #4)
  const openTips = new Set([...list.querySelectorAll("details[data-key][open]")].map((d) => d.dataset.key));
  const scroller = list.parentElement;
  const scrollTop = scroller ? scroller.scrollTop : 0;
  list.replaceChildren();
  if (!m) return;
  const restore = () => {
    list.querySelectorAll("details[data-key]").forEach((d) => { if (openTips.has(d.dataset.key)) d.open = true; });
    if (mapUI.focusedPin != null) {
      list.querySelectorAll(".has-pin").forEach((g) => g.classList.toggle("focused", g.dataset.pin === String(mapUI.focusedPin)));
    }
    if (guideFocus) [...list.querySelectorAll("[data-guide-control]")].find((node) => node.dataset.guideControl === guideFocus)?.focus({ preventScroll: true });
    if (scroller) scroller.scrollTop = scrollTop;
  };
  const numbers = new Map(numberedObjectives().map((x) => [x.objective, x.n]));
  const hideable = canHideGoals();
  const here = mapQuests(m.name);
  const anywhere = anywhereQuests(activeQuests()).filter((q) => !here.includes(q));
  document.getElementById("drawer-count").textContent = here.length + anywhere.length;
  renderLootBar();

  const addQuest = (q) => {
    const li = el("li", `drawer-quest trader-${q.trader_key || "etc"}`);
    // 퀘스트 이름: 누르면 퀘스트 탭에서 자세히 (마우스 뒤로 버튼으로 지도로 돌아옴)
    const name = el("button", "drawer-quest-name");
    name.type = "button";
    name.title = t("map.questDetailHint");
    name.append(el("span", "", q.name), el("span", "drawer-more", "›"));
    name.addEventListener("click", () => goToQuests([q.id]));
    const heading = el("div", "drawer-quest-heading");
    heading.append(name);
    const wiki = questWikiLink(q);
    if (wiki) heading.append(wiki);
    li.append(heading);
    const goals = el("ul", "drawer-goals");
    q.objectives.forEach((o) => {
      const n = numbers.get(o);
      const goal = el("li", o.optional ? "optional" : "");
      const hidden = n && isGoalHidden(q, o);
      if (n) {
        // 위치가 있는 목표: 지도와 같은 번호, 누르면 지도에서 그 위치로 (숨긴 목표는 핀이 없으니 그대로)
        goal.append(el("span", "goal-num", String(n)));
        goal.classList.add("has-pin");
        goal.classList.toggle("goal-hidden", hidden);
        goal.dataset.pin = n;
        goal.addEventListener("click", () => { if (!isGoalHidden(q, o)) focusPin(n); });
      }
      goal.append(el("span", "goal-text", o.text + (o.optional ? t("map.optional") : "")));
      // 레이드 중 임시 숨기기: 줄 오른쪽 끝의 눈 아이콘 (체크가 아님 — 완료 표시로 오해하지 않게)
      if (n && (hideable || hidden)) {
        const eye = el("button", "goal-hide");
        eye.type = "button";
        eye.innerHTML = iconSvg(hidden ? "eye-off" : "eye", 15);
        eye.title = t(hidden ? "map.goalShow" : "map.goalHide");
        eye.setAttribute("aria-label", eye.title);
        eye.setAttribute("aria-pressed", String(hidden));
        eye.addEventListener("click", (e) => {
          e.stopPropagation(); // 줄 누르기(지도 이동)로 번지지 않게
          toggleGoalHidden(q, o, n);
        });
        goal.append(eye);
      }
      goals.append(goal);
    });
    li.append(goals);
    list.append(li);
  };

  // 스토리: 목록 맨 위에 구분해서 (2026-11-21 사용자: 지도 한 화면에서 스토리도)
  renderDrawerGuide(list, m);
  const storyCount = renderDrawerStories(list, m);
  // '할 일' 버튼 숫자 = 퀘스트 + 서랍에 보이는 스토리 단계
  document.getElementById("drawer-count").textContent = here.length + anywhere.length + storyCount;
  // 큰 칸은 내용이 있으면 항상 제목 줄 + 접기 (다른 칸이 있든 없든). 퀘스트가 하나도 없으면 제목 없이 안내 문구만 (2026-11-23 사용자)
  if (here.length) list.append(foldKey(el("li", "drawer-divider drawer-section-title", t("map.drawerQuests")), "quests"));
  if (!here.length && !anywhere.length) list.append(el("li", "empty", t("map.noQuests")));
  here.forEach(addQuest);
  if (anywhere.length) {
    list.append(foldKey(el("li", "drawer-divider", `${mapLabel(ALL_MAPS)} (${anywhere.length})`), "anywhere"));
    anywhere.forEach(addQuest);
  }
  renderDrawerTransits(list);
  foldDrawerSections(list);
  restore();
}

// ---------- 할 일 서랍 큰 칸 접기 (2026-11-23 사용자: 스토리·퀘스트가 길어서 보기 힘듦) ----------
// 큰 제목(스토리·공략·퀘스트·모든 맵·트랜짓)을 누르면 다음 제목 전까지 접힘. 접은 상태는 이 PC 에 기억
const DRAWER_FOLD_STORAGE = "tarkovhelper.drawerFolded";
const drawerFolded = (() => {
  try { return new Set(JSON.parse(localStorage.getItem(DRAWER_FOLD_STORAGE) || "[]")); } catch (e) { return new Set(); }
})();

function foldKey(li, key) {
  li.dataset.fold = key;
  return li;
}

function foldDrawerSections(list) {
  let key = null;
  [...list.children].forEach((li) => {
    if (li.dataset.fold) {
      key = li.dataset.fold;
      const open = !drawerFolded.has(key);
      const button = el("button", "drawer-fold");
      button.type = "button";
      button.setAttribute("aria-expanded", String(open));
      button.title = t(open ? "map.foldHide" : "map.foldShow");
      button.dataset.label = li.textContent;
      button.innerHTML = iconSvg(open ? "chevron-down" : "chevron-right", 13);
      button.append(" " + button.dataset.label);
      li.replaceChildren(button);
      li.classList.toggle("folded", !open);
      button.addEventListener("click", () => setDrawerFold(li.dataset.fold, drawerFolded.has(li.dataset.fold)));
    } else if (key) {
      li.dataset.foldIn = key;
      li.classList.toggle("drawer-folded", drawerFolded.has(key));
    }
  });
}

function setDrawerFold(key, open) {
  if (open) drawerFolded.delete(key); else drawerFolded.add(key);
  try { localStorage.setItem(DRAWER_FOLD_STORAGE, JSON.stringify([...drawerFolded])); } catch (e) { /* 저장소를 못 쓰는 창 */ }
  const list = document.getElementById("drawer-list");
  list.querySelectorAll(`:scope > li[data-fold-in="${key}"]`).forEach((li) => li.classList.toggle("drawer-folded", !open));
  const title = list.querySelector(`:scope > li[data-fold="${key}"]`);
  const button = title?.querySelector(".drawer-fold");
  if (button) {
    title.classList.toggle("folded", !open);
    button.setAttribute("aria-expanded", String(open));
    button.title = t(open ? "map.foldHide" : "map.foldShow");
    button.innerHTML = iconSvg(open ? "chevron-down" : "chevron-right", 13);
    button.append(" " + button.dataset.label);
  }
}

// 지도 핀을 눌러 서랍의 줄로 갈 때, 그 줄이 접힌 칸에 있으면 펼침
function unfoldDrawerFor(node) {
  const key = node?.closest("#drawer-list > li")?.dataset.foldIn;
  if (key && drawerFolded.has(key)) setDrawerFold(key, true);
}

// 트랜짓 도착 맵 → 맵 목록의 맵 (변형 맵은 이름 앞부분으로: '연구소 (Dark)' → 연구소)
function transitTarget(tr) {
  const maps = mapUI.maps || [];
  return maps.find((x) => x.ids.includes(tr.to))
    || (tr.to_name ? maps.find((x) => tr.to_name.startsWith(x.name + " ")) : null) || null;
}

// 서랍 맨 아래: 트랜짓으로 갈 수 있는 맵 + 그 맵에서 할 퀘스트 수. 누르면 지도에서 그 트랜짓으로
function renderDrawerTransits(list) {
  const groups = new Map();
  (mapUI.transits || []).forEach((tr) => {
    const id = tr.label;
    if (!groups.has(id)) groups.set(id, { label: tr.label, target: tr.target, items: [] });
    groups.get(id).items.push(tr);
  });
  if (!groups.size) return;
  list.append(foldKey(el("li", "drawer-divider drawer-transit-title", t("map.drawerTransits")), "transits"));
  const rows = [...groups.values()].map((g) => ({ ...g, count: g.target ? mapQuests(g.target.name).length : 0 }));
  rows.sort((a, b) => b.count - a.count); // 할 일이 많은 맵부터
  rows.forEach((g) => {
    const li = el("li", "drawer-transit");
    const button = el("button", "drawer-transit-row");
    button.type = "button";
    button.title = t("map.transitFocusHint");
    button.append(markerSymbol("transit", "arrow-left-right"), el("span", "drawer-transit-name", g.label),
      g.count ? el("span", "drawer-transit-count", t("map.transitQuests", { n: g.count }))
        : el("span", "drawer-transit-none", t("map.transitNothing")));
    button.addEventListener("click", () => focusTransit(g.items));
    li.append(button);
    list.append(li);
  });
}

function focusTransit(items) {
  const leaf = mapUI.leaflet;
  if (!leaf || !items.length) return;
  if (!layerOn("transits")) setLayer("transits", true);
  if (items[0].floor !== mapUI.floor) setFloor(items[0].floor);
  collapseAll();
  const bounds = L.latLngBounds(items.map((x) => x.marker.getLatLng()));
  afterMove(leaf, () => items[0].marker.openTooltip());
  fitArea(leaf, bounds, { maxZoom: 3, padding: 80, animate: true });
}

// 지도 이동이 끝나면 한 번 실행. 이동 전에 등록하고(움직임 줄이기 = 즉시 이동),
// 이미 그 자리라 이동이 없어도 잠시 뒤 실행 (Codex 41 #5)
function afterMove(leaf, fn) {
  let done = false;
  const run = () => {
    if (done) return;
    done = true;
    leaf.off("moveend", run);
    clearTimeout(timer);
    if (mapUI.leaflet === leaf) fn();
  };
  leaf.on("moveend", run);
  const timer = setTimeout(run, reducedMotion() ? 50 : 1200);
}

// 지도 서랍 맨 위 '스토리' 칸: 지금 맵에서 할 스토리 단계 (홈과 같은 규칙: 진행 중 구간 + 다음 구간)
// 체크는 여기서 바로(스토리 탭과 같은 기록), 위치 설명은 펼쳐서, 사진만 스토리 탭으로
function renderDrawerStories(list, m) {
  const chapters = storyDrawerChapters(m);
  if (mapStory.explicit?.map === m.key && !chapters.some((c) => c.entries.some((e) => e.step.id === mapStory.explicit.id))) {
    const found = storyPlaceEntry(mapStory.explicit.id);
    if (found) chapters.push({ s: found.s, entries: [found.entry] });
  }
  if (!chapters.length) return 0;
  list.append(foldKey(el("li", "drawer-divider drawer-section-title drawer-story-title", t("map.drawerStory")), "story"));
  chapters.forEach(({ s, entries }) => {
    const li = el("li", "drawer-quest drawer-story");
    const name = el("button", "drawer-quest-name");
    name.type = "button";
    name.title = t("map.storyOpenHint");
    name.append(el("span", "", s.ch.name), el("span", "drawer-more", "›"));
    name.addEventListener("click", () => openStoryChapter(s.ch.id));
    const heading = el("div", "drawer-quest-heading");
    heading.append(name);
    li.append(heading);
    // 아직 시작 안 한 챕터: 시작 조건(어디서 시작하는지)을 같이 보여 줌
    if (!isChapterStarted(s.ch) && s.ch.requirement) {
      li.append(el("p", "drawer-story-start", t("story.flow.notStarted", { requirement: s.ch.requirement })));
    }
    // 구간(장소)별로 묶음: 구간 이름 = 장소 설명
    const sections = new Map();
    entries.forEach((entry) => {
      if (!sections.has(entry.section)) sections.set(entry.section, []);
      sections.get(entry.section).push(entry.step);
    });
    sections.forEach((steps, sec) => {
      li.append(el("p", "drawer-story-place", sec.title));
      const goals = el("ul", "drawer-goals drawer-story-steps");
      steps.forEach((step) => {
        const row = el("li");
        row.dataset.storyStep = step.id;
        const label = el("label");
        const box = document.createElement("input");
        box.type = "checkbox";
        box.checked = isStepDone(step);
        box.title = t("story.stepDone");
        box.addEventListener("change", () => saveStoryChanges({ [`${step.id}!`]: box.checked ? "done" : null }));
        if (storyPlacesFor(step.id, m.key).length || story.data?.extractLinks?.[step.id]?.map === m.key) {
          label.append(box);
          const focus = el("button", "story-place-focus", step.ko);
          if (step.optional) focus.append(el("span", "opt-mark", t("story.optional")));
          focus.type = "button";
          focus.title = t("story.viewPlaces");
          focus.setAttribute("aria-pressed", String(mapStory.focused === step.id));
          focus.addEventListener("click", () => focusStoryStep(step.id));
          row.append(label, focus);
        } else {
          const text = el("span", "", step.ko);
          if (step.optional) text.append(el("span", "opt-mark", t("story.optional")));
          label.append(box, text);
          row.append(label);
        }
        box.setAttribute("aria-label", step.ko);
        row.classList.toggle("story-place-focused", mapStory.focused === step.id);
        goals.append(row);
      });
      li.append(goals);
      if (sec.tip) {
        const more = el("details", "drawer-story-tip");
        more.dataset.key = `${s.ch.id}/${sec.title}`;
        more.append(el("summary", "", t("map.storyWhere")), el("p", "", sec.tip));
        li.append(more);
      }
    });
    list.append(li);
  });
  return chapters.reduce((n, c) => n + c.entries.length, 0); // 서랍에 보인 스토리 단계 수 ('할 일' 숫자에 더함)
}

// 서랍의 목표를 누르면 지도에서 그 목표 위치로 이동
function focusPin(n) {
  const group = mapUI.pins[n];
  if (!group || !mapUI.leaflet) return;
  const first = (mapUI.pinMarkers || []).find((x) => x.n === n);
  if (first && first.floor !== mapUI.floor) setFloor(first.floor); // 그 목표가 있는 층으로
  if (!layerOn("quests")) setLayer("quests", true);
  collapseAll(); // 펼쳐진 위치가 아니라 원래 위치로 이동
  // 이동이 끝났는데도 다른 핀과 겹쳐 있으면 펼쳐서 보여줌
  afterMove(mapUI.leaflet, () => {
    const item = (mapUI.pinMarkers || []).find((x) => x.n === n);
    if (item && item.cluster) openCluster(item.cluster);
    if (item) item.marker.openTooltip();
  });
  fitArea(mapUI.leaflet, group.getBounds(), { maxZoom: 4, padding: 60, animate: true });
  highlightDrawer(n);
}

// 핀을 눌렀을 때: 서랍이 닫혀 있으면 열고, 그 목표로 스크롤해서 반짝임
function showInDrawer(n) {
  if (!mapUI.drawerOpen) setDrawer(true);
  highlightDrawer(n, true);
}

function highlightDrawer(n, flash = false) {
  mapUI.focusedPin = n;
  mapUI.focusedPinMap = mapUI.current?.key; // 다른 맵으로 가면 고른 번호를 잊음 (drawMap)
  const picked = numberedObjectives().find((x) => x.n === n);
  mapUI.focusedGoal = picked ? goalHideKey(picked.quest, picked.objective) : null; // 번호가 바뀌어도 따라가게 (syncFocusedPin)
  highlightQuestPin();
  document.querySelectorAll("#drawer-list .has-pin").forEach((g) => g.classList.toggle("focused", g.dataset.pin === String(n)));
  const target = document.querySelector(`#drawer-list .has-pin[data-pin="${n}"]`);
  if (!target) return;
  unfoldDrawerFor(target);
  // 서랍이 막 열리는 중이면 펼쳐진 뒤에 스크롤
  setTimeout(() => target.scrollIntoView({ block: "center", behavior: reducedMotion() ? "instant" : "smooth" }), flash ? 250 : 0);
  if (flash) {
    const quest = target.closest(".drawer-quest");
    quest.classList.remove("flash");
    void quest.offsetWidth; // 반짝임을 다시 시작하게
    quest.classList.add("flash");
  }
}

function setDrawer(open) {
  if (!open && mapUI.guideRaid?.pending && mapUI.current?.key === "terminal") {
    mapUI.guideRaid.opened = true; // 응답 대기 중 직접 닫아도 이번 판에는 다시 열지 않음
    mapUI.guideRaid.pending = false;
  }
  const changed = mapUI.drawerOpen !== open;
  mapUI.drawerOpen = open;
  document.querySelector(".map-layout").classList.toggle("drawer-closed", !open);
  const btn = document.getElementById("drawer-toggle");
  btn.setAttribute("aria-expanded", open);
  const arrow = btn.querySelector(".drawer-arrow");
  if (arrow) arrow.innerHTML = iconSvg(open ? "chevron-right" : "chevron-left", 15);
  // 폭은 즉시 확정하고 지도 크기를 먼저 맞춘 뒤 서랍 내용만 나타낸다.
  document.getElementById("map-drawer").inert = !open;
  if (changed && mapUI.leaflet) fitFull(mapUI.leaflet, false);
  const list = document.getElementById("drawer-list");
  if (open && changed) animateOpen(list, "fade", 150);
  else if (!open) cancelWindowMotion(list);
}

// ---------- 화면 전환 안내 ("세관 불러오는 중") ----------
let transitionRevision = 0;
let transitionTimer;
async function showTransition(title, text, action) {
  const revision = ++transitionRevision;
  clearTimeout(transitionTimer);
  const box = document.getElementById("transition");
  document.getElementById("transition-map").textContent = title;
  document.getElementById("transition-text").textContent = text;
  box.hidden = false;
  window.dispatchEvent(new CustomEvent("raid-transition", { detail: true }));
  animateOpen(box, "notice", 200);
  const started = Date.now();
  try {
    await action();
  } finally {
    if (revision === transitionRevision) {
      const wait = Math.max(0, TRANSITION_MS - (Date.now() - started));
      transitionTimer = setTimeout(() => {
        if (revision === transitionRevision) animateClose(box, () => {
          box.hidden = true;
          window.dispatchEvent(new CustomEvent("raid-transition", { detail: false }));
        }, "notice");
      }, wait);
    }
  }
}

// ---------- 게임 상태가 바뀌면 자동 전환 (topbar.js 의 game-state 알림) ----------
function onGameState(s) {
  if (mapEditor.active) return;
  updateMeForState(s);
  const inRaid = (s.status === "loading" || s.status === "raid") && s.map_id;
  const previousGuideRaid = mapUI.guideRaid;
  updateGuideRaid(s, inRaid);
  if (inRaid) {
    clearTimeout(mapUI.lobbyTimer);
    mapUI.lobbyTimer = null;
    if (mapUI.autoMapId === s.map_id && currentTab() === "map" && previousGuideRaid === mapUI.guideRaid) {
      maybeOpenGuideDrawer();
      return; // 이미 그 맵
    }
    const target = mapUI.maps.find((m) => m.ids.includes(s.map_id));
    if (!target) return; // 맵 목록을 아직 못 받음 → 받은 뒤 다시 확인 (loadMapList 끝)
    mapUI.autoMapId = s.map_id;
    showTransition(target.name, t("map.loading"), async () => {
      mapUI.current = target;
      if (target.key === "terminal" && mapUI.guideRaid && !mapUI.guideRaid.opened) mapUI.guideRaid.pending = true;
      maybeOpenGuideDrawer();
      document.getElementById("map-select").value = target.key;
      if (currentTab() !== "map") {
        mapUI.dirty = true;
        goTab("map"); // 탭이 보이면서 지도를 그림 (tab-shown)
      } else {
        drawMap();
      }
    });
    return;
  }
  // 레이드가 끝남: 트랜짓이면 곧 새 맵이 나오니 잠깐 기다렸다가 로비로
  // 매칭 취소는 트랜짓이 아니니 기다리지 않고 바로 로비로
  if (s.cancelled && mapUI.lobbyTimer) clearTimeout(mapUI.lobbyTimer), (mapUI.lobbyTimer = null);
  if (mapUI.autoMapId && !mapUI.lobbyTimer) {
    mapUI.lobbyTimer = setTimeout(() => {
      mapUI.lobbyTimer = null;
      mapUI.autoMapId = null;
      clearGoalHides(); // 로비로 돌아옴 (트랜짓이 아님) → 임시로 숨긴 목표를 모두 다시 보이게
      if (currentTab() !== "map") return; // 사용자가 다른 화면을 보고 있으면 그대로
      showTransition(t("map.lobby"), t("map.returning"), async () => goTab("home"));
    }, s.cancelled ? 0 : LOBBY_WAIT_MS);
  }
}
window.addEventListener("game-state", (e) => {
  updateGoalHidesForState(e.detail);
  onGameState(e.detail);
  // 레이드에 들어가거나 나오면 서랍의 숨기기 버튼이 생기거나 없어짐 (지도를 새로 그리는 경우는 drawMap 이 다시 그림)
  if (mapUI.leaflet && !mapEditor.active) renderDrawer();
});

// ---------- 내 위치 (게임 스크린샷 파일 이름의 좌표, 2026-11-24 사용자) ----------
// 사용자가 게임에서 스크린샷 키를 누르면 서버(shot_watch.py)가 파일 이름의 좌표를 읽어 준다.
// 표시는 30초: 처음 15초는 진하게, 나머지 15초 동안 천천히 사라짐. 새로 찍으면 옛 표시는 바로 없어짐
// 확대·보는 자리는 건드리지 않고 표시만 한다. 대신 큰 파동이 내 위치로 세 번 모여듦(갈수록 작게).
// 내 위치가 다른 층이면 층만 바꿈 (보는 자리는 그대로)
// 다른 탭을 보다가 찍으면 지도 탭의 그 맵으로 옮김 (2026-11-25 사용자: 찍은 게 확실히 보이게). 지도 탭에서 다른 맵을 보는 중이면 그대로
const ME_SHOW_MS = 30000;   // shot_watch.py SHOW_SECONDS 와 같게 (CSS me-fade 도 30s)
const ME_PIN_Z = 2000;      // 모든 표시 위 (펼친 퀘스트 핀 1000 보다도 위)
const ME_WAVE_MS = 3000;    // 막 찍은 위치만 파동 (다시 그릴 때는 없음). 파동 세 번이 약 3초
const SHOT_HINT_STORAGE = "tarkovhelper.shotHint";
const SHOT_HINT_TIMES = 3;  // 안내는 처음 몇 번의 레이드만. 한 번이라도 찍어 봤으면 다시 안 띄움
const SHOT_HINT_MS = 12000;
const mapMe = { pos: null, marker: null, timer: null, moveTo: false, hintSince: null, hintUntil: 0, hintTimer: null };

function clearMe() {
  clearTimeout(mapMe.timer);
  mapMe.timer = null;
  mapMe.marker?.remove();
  mapMe.marker = null;
}

// 바라보는 방향 → 화면에서의 각도 (0 = 위쪽). 지도가 돌아가 있어도 맞게, 두 점을 화면 좌표로 바꿔서 잼
function meAngle(leaf, pos) {
  if (!pos.fx && !pos.fz) return null;
  const a = leaf.project(L.latLng(gamePos([pos.x, pos.z])), 0);
  const b = leaf.project(L.latLng(gamePos([pos.x + pos.fx, pos.z + pos.fz])), 0);
  if (a.distanceTo(b) < 1e-9) return null;
  return Math.atan2(b.x - a.x, a.y - b.y) * 180 / Math.PI;
}

function drawMe(fresh) {
  clearMe();
  const pos = mapMe.pos, leaf = mapUI.leaflet, m = mapUI.current;
  if (!pos || !leaf || !m || mapEditor.active || !m.ids.includes(pos.map_id)) return;
  const age = Math.max(0, Date.now() - pos.at);
  if (age >= ME_SHOW_MS) { mapMe.pos = null; return; }
  const latlng = L.latLng(gamePos([pos.x, pos.z]));
  // 이 맵의 범위를 한참 벗어난 좌표는 그리지 않음 (다른 맵의 스크린샷 등)
  if (mapUI.bounds && !mapUI.bounds.pad(0.25).contains(latlng)) return;
  const angle = meAngle(leaf, pos);
  const wave = age < ME_WAVE_MS && !reducedMotion() ? '<i class="me-wave"></i><i class="me-wave"></i><i class="me-wave"></i>' : "";
  const shape = angle === null
    ? '<i class="me-dot"></i>'
    : `<svg class="me-arrow" viewBox="0 0 24 24" style="transform:rotate(${angle.toFixed(1)}deg)" aria-hidden="true"><path d="M12 2.5 20 21 12 16.8 4 21Z"/></svg>`;
  const icon = L.divIcon({
    className: "me-pin", iconSize: [44, 44], iconAnchor: [22, 22],
    html: `<span class="me-fade" style="animation-delay:-${Math.round(age)}ms"><i class="me-halo"></i>${wave}${shape}</span>`,
  });
  mapMe.marker = L.marker(latlng, { icon, interactive: false, keyboard: false, zIndexOffset: ME_PIN_Z }).addTo(leaf);
  mapMe.timer = setTimeout(() => { if (mapMe.pos === pos) mapMe.pos = null; clearMe(); }, ME_SHOW_MS - age);
  if (fresh) goToFloorOf(floorOf([pos.x, pos.z, pos.y])); // 새로 찍은 위치: 층만 맞춤
}

function shotHintState() {
  try {
    const saved = JSON.parse(localStorage.getItem(SHOT_HINT_STORAGE) || "{}");
    return { used: saved.used === true, shown: Number.isInteger(saved.shown) && saved.shown > 0 ? saved.shown : 0 };
  } catch (e) {
    return { used: false, shown: 0 };
  }
}

function saveShotHint(state) {
  try { localStorage.setItem(SHOT_HINT_STORAGE, JSON.stringify(state)); } catch (e) { /* 무시 */ }
}

// 지도 위쪽의 안내 한 줄 (지도를 다시 그려도 남은 시간만큼 다시 보임)
function drawShotHint() {
  clearTimeout(mapMe.hintTimer);
  document.querySelector("#map-view .map-hint")?.remove();
  const left = mapMe.hintUntil - Date.now();
  if (left <= 0 || !mapUI.leaflet) return;
  document.getElementById("map-view").append(el("p", "map-hint", t("map.shotHint")));
  mapMe.hintTimer = setTimeout(drawShotHint, left);
}

function onPlayerPosition(pos) {
  if (mapEditor.active) return;
  if (!pos) { // 레이드가 끝났거나 30초가 지남
    mapMe.pos = null;
    mapMe.moveTo = false;
    clearMe();
    return;
  }
  mapMe.pos = pos;
  const hint = shotHintState();
  if (!hint.used) saveShotHint({ ...hint, used: true });
  mapMe.hintUntil = 0;
  drawShotHint();
  // 다른 탭을 보는 중이면 지도 탭으로 (그 맵이 아니면 맵도 바꿈, 고른 맵 기억은 안 바꿈)
  const away = currentTab() !== "map";
  const mine = away && (mapUI.maps || []).find((x) => x.ids.includes(pos.map_id));
  if (mine && mapUI.current !== mine) {
    clearMe();
    mapMe.moveTo = true;
    mapUI.dirty = true; // 숨은 탭에서 그리지 않고, 지도 탭이 보일 때 그림
    mapUI.current = mine;
    document.getElementById("map-select").value = mine.key;
    drawBtrTrackerLink();
  }
  // 지금 그 맵의 지도가 그려져 있으면 바로 표시. 아니면 그 맵을 그릴 때(drawMap 끝) 남은 시간만큼 표시 (moveTo = 그때 층 맞춤)
  const here = mapUI.current && mapUI.current.ids.includes(pos.map_id);
  if (here && mapUI.leaflet && !mapUI.dirty) drawMe(true);
  else { clearMe(); mapMe.moveTo = true; } // 옛 표시와 그 만료 타이머를 지움 (옛 타이머가 새 위치까지 지우지 않게)
  if (away) goTab("map");
}
window.addEventListener("player-position", (e) => onPlayerPosition(e.detail));

// 레이드가 시작되면 안내 한 줄 (처음 몇 번만), 레이드가 아니면 내 위치를 지움
function updateMeForState(s) {
  if (s.status !== "raid") {
    mapMe.pos = null;
    mapMe.moveTo = false;
    mapMe.hintUntil = 0;
    clearMe();
    drawShotHint();
    return;
  }
  if (mapMe.hintSince === s.since) return;
  mapMe.hintSince = s.since;
  const hint = shotHintState();
  if (hint.used || hint.shown >= SHOT_HINT_TIMES) return;
  saveShotHint({ ...hint, shown: hint.shown + 1 });
  mapMe.hintUntil = Date.now() + SHOT_HINT_MS;
  drawShotHint();
}

// ---------- 이벤트 연결 ----------
document.getElementById("map-select").addEventListener("change", (e) => chooseMap(e.target.value));
document.getElementById("drawer-toggle").addEventListener("click", () => setDrawer(!mapUI.drawerOpen));

document.addEventListener("tab-shown", (e) => {
  if (mapEditor.active && e.detail !== "map") { showTab("map"); return; }
  if (e.detail !== "map") return;
  if (mapUI.dirty || !mapUI.leaflet) drawMap();
  else {
    if (mapUI.drawerDirty) renderDrawer(); // 다른 탭에서 스토리가 바뀜 → 서랍만 다시
    setTimeout(() => mapUI.leaflet && fitFull(mapUI.leaflet, false), 0);
  }
  mapUI.drawerDirty = false;
});

// 필요템 줄 수가 바뀌거나 사라져도 남은 지도 크기에 맞춘다.
const lootBarObserver = new ResizeObserver(() => {
  updateLootToggle();
  if (mapUI.leaflet && currentTab() === "map") {
    fitFull(mapUI.leaflet, false);
    if (mapStory.focused) focusStoryStep(mapStory.focused, false);
  }
});
lootBarObserver.observe(document.getElementById("loot-bar"));

// 창 크기가 바뀌면 (태블릿 돌리기 등) 다시 맞춤
let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (mapUI.leaflet && currentTab() === "map") fitFull(mapUI.leaflet, false);
  }, 200);
});

// 은신처에서 '모았음'·건설 완료하면 먹을 것도 다시
let mapRefreshTimer = null;
function scheduleMapRefresh() {
  clearTimeout(mapRefreshTimer);
  mapRefreshTimer = setTimeout(() => {
    renderLootBar();
  }, 0);
}
document.addEventListener("hideout-changed", scheduleMapRefresh);
document.addEventListener("story-changed", () => {
  scheduleMapRefresh();
  mapStory.explicit = null;
  mapStory.focused = null;
  drawStoryPins();
  // 서랍 맨 위 스토리 칸: 체크 성공(다음 구간)·실패(되돌림) 모두 바로 반영 (Codex 41 #1)
  if (currentTab() === "map" && mapUI.current) renderDrawer();
  else if (mapUI.current) mapUI.drawerDirty = true;
});

// 퀘스트가 바뀌면(자동 갱신·숨기기) 서랍과 핀을 다시
document.addEventListener("quests-changed", () => {
  fillMapSelect();
  if (currentTab() === "map" && mapUI.leaflet) {
    const center = mapUI.leaflet.getCenter();
    const zoom = mapUI.leaflet.getZoom();
    Object.values(mapUI.pins).forEach((g) => g.remove());
    mapUI.pins = {};
    drawPins();
    renderDrawer();
    mapUI.leaflet.setView(center, zoom, { animate: false });
  } else {
    mapUI.dirty = true;
  }
});

// 좁은 화면(휴대폰)은 처음에 서랍을 닫아 둠
if (window.innerWidth < 700) setDrawer(false);
initializePositionEditor().then(loadMapList);

// ---------- 표시 켜기/끄기 (왼쪽 서랍) ----------
// 퀘스트·먹을 것·탈출구·트랜짓·열쇠·귀중품. 고른 값은 이 브라우저에 기억 (다음에 켜도 그대로)
const LAYER_KEYS = ["quests", "extracts", "scavExtracts", "transits", "btrStops", "keys", "bitcoin", "keySpawns", "guide", "story", "blackDivision", "bossZones"];
const LAYER_INFO = {
  story: { label: t("map.storyLayer"), mark: "mk:story:book-open", on: true },
  blackDivision: { label: t("map.layerBlackDivision"), mark: "mk:enemy:crosshair", on: true },
  bossZones: { label: t("map.layerBossZones"), mark: "mk:boss:skull", on: true },
  guide: { label: t("map.guideLayer"), mark: "mk:guide-start:book-open", on: true },
  quests: { label: t("map.layerQuests"), mark: "shape:quest", on: true },
  extracts: { label: t("map.layerExtracts"), mark: "mk:extract-pmc:door-open", on: true },
  scavExtracts: { label: t("map.layerScavExtracts"), mark: "mk:extract-scav:door-open", on: false },
  transits: { label: t("map.layerTransits"), mark: "mk:transit:arrow-left-right", on: true },
  btrStops: { label: t("map.layerBtrStops"), mark: "mk:btr:bus", on: true },
  keys: { label: t("map.layerLocks"), mark: "mk:lock:lock", on: true },          // 열쇠로 여는 곳
  bitcoin: { label: t("map.layerBitcoin"), mark: "mk:bitcoin:bitcoin", on: true },
  keySpawns: { label: t("map.layerKeySpawns"), mark: "mk:keyspawn:key", on: true },      // 열쇠·키카드가 바닥에 생기는 곳
};
const LAYER_STORAGE = "tarkovhelper.layers";
const layerState = (() => {
  try { return JSON.parse(localStorage.getItem(LAYER_STORAGE) || "{}"); } catch (e) { return {}; }
})();


function layerOn(key) {
  if (LAYER_INFO[key].always) return true;  // 스위치 없이 항상 보이는 표시
  return key in layerState ? layerState[key] : LAYER_INFO[key].on;
}

function setLayer(key, on) {
  layerState[key] = on;
  try { localStorage.setItem(LAYER_STORAGE, JSON.stringify(layerState)); } catch (e) { /* 무시 */ }
  const group = mapUI.groups && mapUI.groups[key];
  if (group && mapUI.leaflet) {
    if (on) group.addTo(mapUI.leaflet);
    else group.remove();
  }
  highlightStoryStep();
  highlightGuideStep(); // 다시 켜면서 Leaflet이 만든 새 아이콘에도 선택 표시 유지
  renderLayerDrawer();
}

function renderLayerDrawer() {
  const list = document.getElementById("layer-list");
  list.replaceChildren();
  LAYER_KEYS.forEach((key) => {
    const info = LAYER_INFO[key];
    if (key === "guide" && !currentGuide()) return;
    // 편집 모드는 이 맵의 모든 장소, 평소에는 지금 할 단계의 장소 기준 (storyPinEntries 와 같은 기준)
    if (key === "story" && (!mapUI.current || !storyPinEntries().some(({ entry }) => storyPlacesFor(entry.step.id, mapUI.current.key).length))) return;
    if ((key === "blackDivision" || key === "bossZones") && !layerCount(key)) return;
    if (info.always) return;  // 항상 보이는 표시는 서랍에 스위치를 두지 않음
    const on = layerOn(key);
    const btn = el("button", `layer-toggle layer-${key}${on ? " on" : ""}`);
    btn.type = "button";
    btn.setAttribute("aria-pressed", on);
    // 기호: 지도에 찍히는 모양 그대로 (그림 아이콘 또는 같은 색·모양의 작은 도형)
    const mark = el("span", "layer-mark");
    if (info.mark.startsWith("mk:")) {
      const [, kind, icon] = info.mark.split(":");
      mark.innerHTML = `<span class="mk mk-${kind} mk-legend"><span>${mapIconSvg(icon, 11, 2.4)}</span></span>`;
    } else if (info.mark.startsWith("icon:")) mark.innerHTML = iconSvg(info.mark.slice(5), 14);
    else mark.append(el("i", `legend legend-${info.mark.slice(6)}`));
    // 이 맵에 몇 개 있는지 (0이면 지도에 안 보이는 이유를 알 수 있게)
    const count = layerCount(key);
    const name = el("span", "layer-name", info.label);
    if (count !== null) name.append(el("small", "layer-count", ` ${count}`));
    btn.append(mark, name, el("span", "layer-state", on ? t("map.layerOn") : t("map.layerOff")));
    btn.addEventListener("click", () => setLayer(key, !layerOn(key)));
    list.append(btn);
  });
}

// 표시 묶음마다 이 맵에 있는 개수 (지도를 아직 안 그렸으면 null)
function layerCount(key) {
  if (!mapUI.groups || !mapUI.leaflet) return null;
  if (key === "bossZones") return mapUI.groups.bossZones?.getLayers().filter((layer) => layer instanceof L.Marker).length || 0;
  if (key === "quests") return (mapUI.pinMarkers || []).length;
  return mapUI.groups[key] ? mapUI.groups[key].getLayers().length : 0;
}

function setLayerDrawer(open) {
  const changed = mapUI.layerDrawerOpen !== open;
  mapUI.layerDrawerOpen = open;
  document.querySelector(".map-layout").classList.toggle("layers-closed", !open);
  document.getElementById("layer-toggle-btn").setAttribute("aria-expanded", open);
  try { localStorage.setItem("tarkovhelper.layerDrawer", open ? "1" : "0"); } catch (e) { /* 무시 */ }
  document.getElementById("layer-drawer").inert = !open;
  if (changed && mapUI.leaflet) fitFull(mapUI.leaflet, false);
  const list = document.getElementById("layer-list");
  if (open && changed) animateOpen(list, "fade", 150);
  else if (!open) cancelWindowMotion(list);
}

// ---------- 탈출구·트랜짓·열쇠 문·귀중품 ----------
async function drawFeatures(leaf, m) {
  let data;
  try {
    const res = await fetch(`/api/map-features/${encodeURIComponent(m.key)}`);
    data = await res.json();
    if (!res.ok) return;
  } catch (error) {
    return; // 표시물을 못 받아도 지도와 퀘스트는 그대로
  }
  if (mapUI.leaflet !== leaf) return; // 그사이 다른 맵으로 바뀜
  if (mapEditor.active) {
    mapEditor.options = data.editorAdd || null;
    mapEditor.heights = data.editorHeights || [];
    leaf.on("click", (event) => {
      if (!mapEditor.walk && mapEditor.placing && !mapEditor.saving && mapEditor.options) openMarkerForm(event.latlng);
    });
    // 연속 입력: 아이콘·영역 위를 눌러도 장소로 넣음 (Leaflet 지도 클릭은 아이콘 위에서 안 생김). 끌기는 무시
    const box = leaf.getContainer();
    let down = null;
    box.addEventListener("pointerdown", (event) => { down = [event.clientX, event.clientY]; }, true);
    box.addEventListener("click", (event) => {
      // 스토리 핀(새로 넣은 것 포함)은 눌러서 팝업([삭제])을 열 수 있게 그대로 둠
      if (!mapEditor.walk || mapUI.leaflet !== leaf || event.target.closest(".leaflet-control, .leaflet-popup, .mk-story")) return;
      if (down && Math.hypot(event.clientX - down[0], event.clientY - down[1]) > 5) return;
      event.stopPropagation(); // 아이콘 팝업 대신 장소 넣기
      if (!mapEditor.saving) storyWalkPlace(leaf.mouseEventToLatLng(event));
    }, true);
    refreshNewMarkers();
    updatePositionEditor(mapEditor.status?.textContent || "");
  }
  // card: true 면 링크가 든 '마우스 올림 창' (창 위로 옮겨도 안 사라짐), 아니면 보통 설명
  const add = (groupKey, p, icon, tooltip, card = false, options = {}) => {
    const z = MARKER_Z[groupKey] || 0;
    const marker = L.marker(gamePos(p), { icon, keyboard: false, zIndexOffset: z, ...options });
    marker.baseZ = z;
    if (tooltip && card) {
      marker.on("mouseover", () => openHoverCard(marker, tooltip));
      marker.on("mouseout", closeHoverCardSoon);
    } else if (tooltip) marker.bindTooltip(tooltip, { direction: "top", offset: [0, -8] });
    const floor = floorOf(p);
    marker.on("click", () => goToFloorOf(floor)); // 누르면 그 표시가 있는 층으로
    marker.addTo(mapUI.groups[groupKey]);
    mapUI.floorItems.push({ layer: marker, floor });
    return marker;
  };

  // 탈출구·트랜짓·BTR 정류장: 이름은 마우스를 올릴 때만 (항상 띄우면 지도를 가림, 2026-11-20 사용자)
  data.extracts.forEach((e) => {
    const group = e.faction === "scav" ? "scavExtracts" : "extracts";
    const marker = add(group, e.pos, markerIcon(`extract-${e.faction}`, "door-open", 18), escapeHtml(e.name));
    bindPositionEditor(marker, e, e.name);
    mapUI.extractMarkers.push({ marker, name: e.name, group });
    marker.on("click", () => {
      if (mapEditor.active) return;
      const ids = guideExtractIds(e.name);
      if (ids.length) showGuidePoint(ids[0]);
    });
  });
  applyStoryExtractLinks(); // 탈출구 표시가 생긴 뒤 스토리 단계 연결 다시 붙이기
  // 트랜짓: 마우스를 올리면 도착 맵 이름 (2026-11-21 사용자)
  mapUI.transits = data.transits.map((tr) => {
    const target = transitTarget(tr);
    const label = tr.to_name || (target ? target.name : tr.name); // 변형 맵은 정확한 이름 (연구소 (Dark) 등)
    const marker = add("transits", tr.pos, markerIcon("transit", "arrow-left-right", 18),
      escapeHtml(t("map.transitTo", { map: label })));
    return { target, label, marker, floor: floorOf(tr.pos) };
  });
  renderDrawer(); // 서랍 맨 아래 '트랜짓으로 갈 수 있는 맵'
  (data.btrStops || []).forEach((stop) => {
    const marker = add("btrStops", stop.pos, markerIcon("btr", "bus", 18), escapeHtml(stop.name));
    bindPositionEditor(marker, stop, stop.name);
  });
  drawEnemyFeatures(data, add);
  // 열쇠 문: 마우스를 올리면 필요한 열쇠
  data.locks.forEach((lock) => {
    const where = lock.type === "trunk" ? t("map.lockTrunk") : "";
    const power = lock.power ? t("map.lockPower") : "";
    add("keys", lock.pos, markerIcon("lock", "lock", 16),
      `<b>${keyWithWiki(lock.key, lock.wiki)}</b>${where}${power}`, true);
  });
  // 바닥 아이템 자리: 비트코인 / 열쇠 자리 (마우스를 올리면 그 자리 열쇠)
  data.loot.forEach((spot) => {
    if (spot.bitcoin) add("bitcoin", spot.pos, markerIcon("bitcoin", "bitcoin", 16), `<b>${t("map.bitcoin")}</b>`);
    if (spot.keys.length) {
      const list = spot.keys.slice(0, 5).map((name, i) => keyWithWiki(name, (spot.keyWikis || [])[i])).join("<br>") +
        (spot.keys.length > 5 ? t("map.andMore", { n: spot.keys.length - 5 }) : "");
      add("keySpawns", spot.pos, markerIcon("keyspawn", "key", 16), `<b>${t("map.keySpawn")}</b><br>${list}`, true);
    }
  });
  applyFloor();
  highlightGuideStep();
  renderLayerDrawer();
}

// 적 표시도 다른 핀과 같은 CRS·층 처리를 사용한다.
function drawEnemyFeatures(data, add) {
  const validPos = (pos) => Array.isArray(pos) && pos.length === 3 && pos.every(Number.isFinite);
  (Array.isArray(data.blackDivision) ? data.blackDivision : []).forEach((spawn) => {
    if (!spawn || !validPos(spawn.pos)) return;
    const known = Array.isArray(spawn.count) && spawn.count.length === 2 &&
      spawn.count.every((n) => Number.isInteger(n) && n >= 1) && spawn.count[0] <= spawn.count[1];
    const count = known ? (spawn.count[0] === spawn.count[1] ? String(spawn.count[0]) : spawn.count.join("–")) : "?";
    const label = known ? t(spawn.inside ? "map.blackDivisionInside" : "map.blackDivisionCount", { n: count }) : t("map.blackDivisionUnknown");
    const labelText = label + (spawn.estimated ? " · " + t("map.enemyEstimated") : "");
    const icon = markerIcon("enemy", "crosshair", 22);
    icon.options.html += `<small class="enemy-count">${escapeHtml(count)}</small>`;
    const marker = add("blackDivision", spawn.pos, icon, escapeHtml(labelText),
      false, { keyboard: true, title: labelText, alt: labelText });
    bindPositionEditor(marker, spawn, labelText + " · " + spawn.name);
  });
  (Array.isArray(data.bossZones) ? data.bossZones : []).forEach((zone) => {
    if (!zone || !validPos(zone.pos) || !Array.isArray(zone.bosses)) return;
    const names = zone.bosses.filter((name) => typeof name === "string" && name);
    if (!names.length) return;
    // 게임 좌표에서 10m 옆으로 띄운다. 지도 회전 후에도 BD와 같은 방식으로 이동한다.
    const iconPos = mapEditor.active && zone.edit ? zone.pos : [zone.pos[0] + 10, zone.pos[1], zone.pos[2]];
    const marker = add("bossZones", iconPos, markerIcon("boss", "skull", 22),
      "<b>" + escapeHtml(t("map.layerBossZones")) + "</b><br>" + names.map(escapeHtml).join("<br>"),
      false, { keyboard: true, title: names.join(", "), alt: names.join(", ") });
    bindPositionEditor(marker, zone, names.join(", ") + " · " + zone.name);
  });
}

// ---------- 층 ----------
// tarkov.dev 지도 설정의 층(layers): 높이 범위 + 건물 범위 안에 있으면 그 층. 아니면 1층(null)
function floorOf(p) {
  const view = mapUI.view;
  if (!view || !view.layers || p.length < 3) return null;
  const [x, z, y] = p;
  for (let i = 0; i < view.layers.length; i++) {
    for (const ext of view.layers[i].extents || []) {
      const h = ext.height || [-1e9, 1e9];
      if (y < h[0] || y > h[1]) continue;
      if (!ext.bounds || !ext.bounds.length) return i;
      const inside = ext.bounds.some((b) => {
        const [x1, z1] = b[0];
        const [x2, z2] = b[1];
        return x >= Math.min(x1, x2) && x <= Math.max(x1, x2) && z >= Math.min(z1, z2) && z <= Math.max(z1, z2);
      });
      if (inside) return i;
    }
  }
  return null;
}

// 지금 층이 아닌 표시는 반투명 (보이기는 함)
function applyFloor() {
  (mapUI.floorItems || []).forEach(({ layer, floor }) => {
    const here = floor === mapUI.floor;
    if (layer.setOpacity) layer.setOpacity(here ? 1 : 0.3);
    const tip = layer.getTooltip && layer.getTooltip();
    if (tip && tip.getElement && tip.getElement()) tip.getElement().style.opacity = here ? "" : "0.35";
  });
}

function floorLabel(name) {
  const m = /^(\d+)(st|nd|rd|th) Floor$/i.exec(name || "");
  if (m) return t("map.floorN", { n: m[1] });
  const names = (window.I18N && window.I18N.floors) || {}; // 층 이름 사전 (영어 층 이름 → 화면 글자)
  return names[name] || name;
}

// 높은 층이 위로 오게 (지하는 1층 아래)
function floorRank(name) {
  const m = /^(\d+)(st|nd|rd|th) Floor$/i.exec(name || "");
  if (m) return Number(m[1]);
  if (name === "Second Level") return 2;
  if (/Underground|Basement|Tunnels|Garage|Bunkers|Technical/i.test(name || "")) return 0;
  return 1.5;
}

function setFloor(index) {
  const view = mapUI.view;
  if (!view || !mapUI.leaflet) return;
  mapUI.floor = index;
  const layer = index === null ? null : view.layers[index];
  // SVG: 고른 층 그림만 보이고, 1층 그림은 흐리게
  Object.values(mapUI.svgGroups || {}).forEach((g) => {
    if (g.classList.contains("base-layer")) {
      g.style.display = "";
      g.style.opacity = layer ? "0.25" : "";
    } else {
      g.style.display = layer && layer.svg_layer === g.id ? "" : "none";
    }
  });
  // 그림이 타일로만 있는 층
  if (mapUI.floorTile) { mapUI.floorTile.remove(); mapUI.floorTile = null; }
  if (layer && !layer.svg_layer && layer.tiles) {
    const maxZoom = Number(view.max_zoom) || 5;
    mapUI.floorTile = L.tileLayer(layer.tiles, {
      tileSize: view.tile_size, bounds: mapUI.bounds, maxNativeZoom: maxZoom, maxZoom,
    }).addTo(mapUI.leaflet);
  }
  updateFloorNav();
  applyFloor();
}

// ---------- 층 네비게이터 (오른쪽 아래) ----------
// 얇은 판을 비스듬한 시점(아이소메트릭)으로 위아래로 쌓음. 고른 층만 밝게, 나머지는 반투명.
// 판을 누르거나, 네비게이터 위에서 마우스 휠을 굴려 층을 바꿈 (지도는 확대되지 않음)
const FLOOR_GAP = 18; // 판 사이 간격(px) (CSS .floor-plane 의 -18px 과 같게)

function drawFloorControl(box, view) {
  mapUI.floorRows = null;
  if (!view.layers || !view.layers.length) return;
  const rows = view.layers.map((l, i) => ({ index: i, name: l.name, rank: floorRank(l.name) }));
  rows.push({ index: null, name: "1st Floor", rank: 1 });
  rows.sort((a, b) => a.rank - b.rank); // 아래층부터
  mapUI.floorRows = rows;

  const nav = el("div", "floor-nav");
  nav.title = t("map.floorNav");
  const stack = el("div", "floor-stack");
  rows.forEach((row, i) => {
    const plane = el("button", "floor-plane");
    plane.type = "button";
    plane.style.setProperty("--i", i);
    plane.dataset.floor = String(row.index);
    plane.setAttribute("aria-label", floorLabel(row.name));
    plane.addEventListener("click", (e) => {
      e.stopPropagation();
      setFloor(row.index);
    });
    stack.append(plane);
  });
  nav.append(stack, el("div", "floor-label"));
  // 휠: 위로 굴리면 위층, 아래로 굴리면 아래층 (한 번에 한 층)
  let lastWheel = 0;
  nav.addEventListener("wheel", (e) => {
    e.preventDefault();
    e.stopPropagation();
    const now = Date.now();
    if (now - lastWheel < 180) return;
    lastWheel = now;
    stepFloor(e.deltaY < 0 ? 1 : -1);
  }, { passive: false });
  L.DomEvent.disableClickPropagation(nav);
  L.DomEvent.disableScrollPropagation(nav);
  box.append(nav);
  updateFloorNav();
}

// 표시를 눌렀을 때: 다른 층이면 그 층으로 바꿈
function goToFloorOf(floor) {
  if (floor !== mapUI.floor) setFloor(floor);
}

function stepFloor(direction) {
  const rows = mapUI.floorRows;
  if (!rows) return;
  const at = rows.findIndex((r) => r.index === mapUI.floor);
  const next = rows[Math.max(0, Math.min(rows.length - 1, at + direction))];
  if (next && next.index !== mapUI.floor) setFloor(next.index);
}

function updateFloorNav() {
  const rows = mapUI.floorRows;
  const nav = document.querySelector(".floor-nav");
  if (!rows || !nav) return;
  const at = rows.findIndex((r) => r.index === mapUI.floor);
  nav.querySelectorAll(".floor-plane").forEach((plane, i) => {
    plane.classList.toggle("active", i === at);
    plane.classList.toggle("above", i > at);
  });
  // 고른 층이 늘 같은 높이에 오도록 묶음을 미끄러뜨림
  nav.querySelector(".floor-stack").style.transform = `translateY(${at * FLOOR_GAP}px)`;
  // 층 이름: 살짝 사라졌다 나타남
  const label = nav.querySelector(".floor-label");
  label.classList.add("fade");
  setTimeout(() => {
    label.textContent = floorLabel(rows[at].name);
    label.classList.remove("fade");
  }, 150);
}

// 왼쪽 서랍 연결
document.getElementById("layer-toggle-btn").addEventListener("click", () => setLayerDrawer(!mapUI.layerDrawerOpen));
renderLayerDrawer();
(() => {
  let open = false;
  try { open = localStorage.getItem("tarkovhelper.layerDrawer") === "1"; } catch (e) { /* 무시 */ }
  setLayerDrawer(open);
})();
