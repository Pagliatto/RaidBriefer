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
  lobbyTimer: null,  // 로비로 돌아갈 때 잠깐 기다리는 타이머 (트랜짓 대비)
  dirty: true,       // 퀘스트가 바뀌어서 다시 그려야 함
};
const MAP_KEY_STORAGE = "tarkovhelper.map";
const TRANSITION_MS = 1600;   // 전환 안내를 최소한 보여주는 시간
const LOBBY_WAIT_MS = 30000;  // 레이드가 끝나고 이만큼 새 맵이 안 나오면 로비로 (트랜짓은 실제 로그에서 16~18초, 넉넉하게)

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

function tdCRS(view) {
  const tf = view.transform || [1, 0, -1, 0];
  const rotation = Number(view.rotation) || 0;
  return L.extend({}, L.CRS.Simple, {
    transformation: new L.Transformation(tf[0], tf[1], tf[2] * -1, tf[3]),
    projection: L.extend({}, L.Projection.LonLat, {
      project: (latLng) => L.Projection.LonLat.project(tdRotate(latLng, rotation)),
      unproject: (point) => tdRotate(L.Projection.LonLat.unproject(point), rotation * -1),
    }),
  });
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
  if (animate) leaf.flyTo(center, zoom, { duration: 0.6 });
  else leaf.setView(center, zoom, { animate: false });
}

// 가장 멀리 본 화면 = 지도가 꽉 찬 화면. 그보다 더 축소는 안 되게 (작은 지도 + 검은 여백 방지)
// reset: true 면 전체 보기로 돌아감. false 면 지금 전체 보기였을 때만 다시 맞춤 (서랍 열고 닫기·창 크기)
function fitFull(leaf, reset) {
  if (!mapUI.bounds) return;
  const wasFull = leaf.getZoom() <= leaf.getMinZoom() + 0.05;
  leaf.invalidateSize();
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
async function loadMapList() {
  try {
    const res = await fetch("/api/maps");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "");
    mapUI.maps = data;
  } catch (error) {
    document.getElementById("map-view").replaceChildren(el("p", "empty", t("map.loadFailed", { error: error.message })));
    return;
  }
  let saved = null;
  try { saved = localStorage.getItem(MAP_KEY_STORAGE); } catch (e) { /* 저장소를 못 쓰는 창 */ }
  mapUI.current = mapUI.maps.find((m) => m.key === saved) || mapUI.maps[0];
  fillMapSelect();
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
  mapUI.current = next;
  if (remember) {
    try { localStorage.setItem(MAP_KEY_STORAGE, key); } catch (e) { /* 무시 */ }
  }
  document.getElementById("map-select").value = key;
  drawMap();
}

// 이 맵의 진행 중 퀘스트 (숨긴 것 제외) → 그다음 '모든 맵' 퀘스트
function mapQuests(name) {
  const active = activeQuests();
  return active.filter((q) => q.maps.includes(name));
}

// ---------- 지도 그리기 ----------
async function drawMap() {
  const m = mapUI.current;
  const box = document.getElementById("map-view");
  if (mapUI.leaflet) {
    mapUI.leaflet.remove();
    mapUI.leaflet = null;
  }
  box.replaceChildren();
  mapUI.pins = {};
  mapUI.dirty = false;
  document.getElementById("map-auto").hidden = !(mapUI.autoMapId && m && m.ids.includes(mapUI.autoMapId));
  if (!m) return;

  const view = m.view;
  renderDrawer(); // 지도가 없어도 퀘스트 목록은 보여줌
  if (!view) {
    box.append(el("p", "empty", t("map.noImage")));
    return;
  }

  // 최대 확대 = 지도 데이터의 maxZoom (세관 5). 더 확대하면 SVG 가 수만 px 로 커져 건물 없는 맨바닥처럼 보임
  const maxZoom = Number(view.max_zoom) || 5;
  const leaf = L.map(box, {
    crs: tdCRS(view),
    zoomSnap: 0.1,
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
  // 확대·축소하면 겹침이 달라지니 다시 계산
  leaf.on("zoomstart", collapseAll);
  leaf.on("zoomend", findClusters);
  leaf.on("click", collapseAll); // 지도 빈 곳을 누르면 펼친 핀을 다시 모음
  // 탭이 막 보인 순간에는 칸 크기가 덜 정해졌을 수 있어서, 크기를 다시 재고 맞춤
  // (requestAnimationFrame 은 창이 가려져 있으면 멈춰서 setTimeout 사용)
  setTimeout(() => {
    if (mapUI.leaflet !== leaf) return;
    fitFull(leaf, true);
  }, 0);

  if (view.svg) {
    // SVG 지도: 여러 층 중 기본 층만 보이게 (tarkov.dev 와 같은 방식)
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    L.svgOverlay(svg, tdBounds(view.svg_bounds || view.bounds), { className: "map-svg" }).addTo(leaf);
    try {
      const text = await (await fetch(view.svg)).text();
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
      }
    } catch (error) {
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

  drawPins();
  drawFeatures(leaf, m);
  drawFloorControl(box, view);
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
  numberedObjectives().forEach(({ n, quest, objective, spots }) => {
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
        // 누르기: 겹친 핀이면 그 자리를 확대해서 떨어뜨림, 혼자 있는 핀이면 서랍을 열고 그 목표로
        marker.on("click", (e) => {
          L.DomEvent.stopPropagation(e);
          if (item.floor !== mapUI.floor) setFloor(item.floor); // 다른 층 핀 → 그 층으로
          const c = item.cluster;
          if (c && c.items.length > 1 && !c.open) zoomIntoCluster(c);
          else showInDrawer(n);
        });
        layers.push(marker);
      });
    });
    mapUI.pins[n] = L.featureGroup(layers).addTo(mapUI.groups.quests);
  });
  findClusters();
  applyFloor();
  drawLoot();
}

// ---------- 먹을 것 (지도 위쪽 줄에 아이콘 + 바닥 생성 위치가 있으면 지도에 ◆) ----------
const LOOT_LIMIT = 8; // 이보다 많으면 '+N' 으로 접어 둠
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
  const shown = mapUI.lootOpen ? items : items.slice(0, LOOT_LIMIT);
  shown.forEach((item) => {
    const chip = el("button", "loot-icon");
    chip.type = "button";
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
    chip.addEventListener("click", () => focusLoot(item.text));
    bar.append(chip);
  });
  if (items.length > LOOT_LIMIT) {
    const more = el("button", "loot-more", mapUI.lootOpen ? t("map.lootFold") : `+${items.length - LOOT_LIMIT}`);
    more.type = "button";
    more.addEventListener("click", () => {
      mapUI.lootOpen = !mapUI.lootOpen;
      renderLootBar();
      setTimeout(() => mapUI.leaflet && fitFull(mapUI.leaflet, false), 0); // 줄 높이가 바뀌면 지도 크기 다시
    });
    bar.append(more);
  }
  markLootChips();
}

// 바닥 생성 위치가 데이터에 있는 아이템만 지도에 ◆ (귀중품 위주. 상자에서 나오는 건 위치 데이터가 없음)
async function drawLoot() {
  if (mapUI.lootLayer) mapUI.lootLayer.remove();
  if (mapUI.floorItems) mapUI.floorItems = mapUI.floorItems.filter((x) => !x.lootPin);
  mapUI.lootLayer = null;
  mapUI.lootSpots = {};
  const leaf = mapUI.leaflet;
  const m = mapUI.current;
  const items = lootItems();
  const ids = [...new Set(items.flatMap((i) => i.ids || []))];
  if (!leaf || !m || !ids.length) return markLootChips();
  let spots = {};
  try {
    const res = await fetch(`/api/loot/${encodeURIComponent(m.key)}?ids=${ids.join(",")}`);
    if (res.ok) spots = await res.json();
  } catch (error) { /* 위치를 못 받아도 목록은 그대로 */ }
  if (mapUI.leaflet !== leaf) return; // 그사이 다른 맵으로 바뀜
  const layers = [];
  items.forEach((item) => {
    const points = (item.ids || []).flatMap((id) => spots[id] || []);
    if (!points.length) return;
    mapUI.lootSpots[item.text] = points.map((p) => L.latLng(gamePos(p)));
    points.forEach((p) => {
      const pin = L.marker(gamePos(p), {
        icon: L.divIcon({ className: "loot-pin", html: "<span></span>", iconSize: [12, 12], iconAnchor: [6, 6] }),
        keyboard: false,
      }).bindTooltip(shortItem(item.text), { direction: "top", offset: [0, -6] });
      const floor = floorOf(p);
      pin.on("click", () => goToFloorOf(floor)); // 누르면 그 층으로
      mapUI.floorItems.push({ layer: pin, floor, lootPin: true });
      layers.push(pin);
    });
  });
  mapUI.lootLayer = L.layerGroup(layers).addTo(mapUI.groups.loot);
  applyFloor();
  renderLayerDrawer();
  markLootChips();
}

// 지도에 위치가 있는 아이템 칩에는 ◆ 표시
function markLootChips() {
  document.querySelectorAll("#loot-bar .loot-icon").forEach((chip) => {
    chip.classList.toggle("on-map", !!(mapUI.lootSpots && mapUI.lootSpots[chip.dataset.key]));
  });
}

function focusLoot(key) {
  const spots = mapUI.lootSpots && mapUI.lootSpots[key];
  if (spots && spots.length && mapUI.leaflet) {
    collapseAll();
    fitArea(mapUI.leaflet, L.latLngBounds(spots), { maxZoom: 4, padding: 60, animate: true });
  }
}

// ---------- 겹친 핀: 묶지 않고 그 자리에 겹쳐 둠 (묶음 표시는 지도를 가려서 안 씀) ----------
// 겹친 핀은 테두리 링으로 '여러 개'임을 알리고, 누르면 그 자리를 확대해서 떨어뜨림.
// 위치가 완전히 같아서 가장 크게 확대해도 겹치면 그때만 동그랗게 펼침 (지도 빈 곳을 누르면 다시 모임)
const CLUSTER_PX = 20; // 화면에서 이만큼 가까우면 겹친 것으로 봄
const QUEST_PIN_Z = 500; // 퀘스트 핀 높이 (표시물 위, 펼친 핀(1000) 아래)

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

function zoomIntoCluster(c) {
  const leaf = mapUI.leaflet;
  if (!leaf) return;
  const max = leaf.getMaxZoom();
  if (leaf.getZoom() >= max - 0.05) {
    openCluster(c); // 더 확대할 수 없음 → 펼쳐서 보여줌
    return;
  }
  const center = L.latLngBounds(c.items.map((item) => item.home)).getCenter();
  leaf.flyTo(center, Math.min(max, leaf.getZoom() + 2), { duration: 0.5 });
}

function closeCluster(c) {
  clearTimeout(c.timer);
  if (!c.open) return;
  c.open = false;
  c.items.forEach((item) => {
    item.marker.setLatLng(item.home);
    item.marker.setZIndexOffset(QUEST_PIN_Z);
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

// ---------- 오른쪽 서랍 ----------
function renderDrawer() {
  const m = mapUI.current;
  const list = document.getElementById("drawer-list");
  list.replaceChildren();
  if (!m) return;
  const numbers = new Map(numberedObjectives().map((x) => [x.objective, x.n]));
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
    li.append(name);
    const goals = el("ul", "drawer-goals");
    q.objectives.forEach((o) => {
      const n = numbers.get(o);
      const goal = el("li", o.optional ? "optional" : "");
      if (n) {
        // 위치가 있는 목표: 지도와 같은 번호, 누르면 지도에서 그 위치로
        goal.append(el("span", "goal-num", String(n)));
        goal.classList.add("has-pin");
        goal.dataset.pin = n;
        goal.addEventListener("click", () => focusPin(n));
      }
      goal.append(el("span", "", o.text + (o.optional ? t("map.optional") : "")));
      goals.append(goal);
    });
    li.append(goals);
    list.append(li);
  };

  if (!here.length && !anywhere.length) list.append(el("li", "empty", t("map.noQuests")));
  here.forEach(addQuest);
  if (anywhere.length) {
    list.append(el("li", "drawer-divider", `${mapLabel(ALL_MAPS)} (${anywhere.length})`));
    anywhere.forEach(addQuest);
  }
}

// 서랍의 목표를 누르면 지도에서 그 목표 위치로 이동
function focusPin(n) {
  const group = mapUI.pins[n];
  if (!group || !mapUI.leaflet) return;
  const first = (mapUI.pinMarkers || []).find((x) => x.n === n);
  if (first && first.floor !== mapUI.floor) setFloor(first.floor); // 그 목표가 있는 층으로
  if (!layerOn("quests")) setLayer("quests", true);
  collapseAll(); // 펼쳐진 위치가 아니라 원래 위치로 이동
  fitArea(mapUI.leaflet, group.getBounds(), { maxZoom: 4, padding: 60, animate: true });
  // 이동이 끝났는데도 다른 핀과 겹쳐 있으면 펼쳐서 보여줌
  mapUI.leaflet.once("moveend", () => {
    const item = (mapUI.pinMarkers || []).find((x) => x.n === n);
    if (item && item.cluster) openCluster(item.cluster);
    if (item) item.marker.openTooltip();
  });
  highlightDrawer(n);
}

// 핀을 눌렀을 때: 서랍이 닫혀 있으면 열고, 그 목표로 스크롤해서 반짝임
function showInDrawer(n) {
  if (!mapUI.drawerOpen) setDrawer(true);
  highlightDrawer(n, true);
}

function highlightDrawer(n, flash = false) {
  document.querySelectorAll("#drawer-list .has-pin").forEach((g) => g.classList.toggle("focused", g.dataset.pin === String(n)));
  const target = document.querySelector(`#drawer-list .has-pin[data-pin="${n}"]`);
  if (!target) return;
  // 서랍이 막 열리는 중이면 펼쳐진 뒤에 스크롤
  setTimeout(() => target.scrollIntoView({ block: "center", behavior: "smooth" }), flash ? 250 : 0);
  if (flash) {
    const quest = target.closest(".drawer-quest");
    quest.classList.remove("flash");
    void quest.offsetWidth; // 반짝임을 다시 시작하게
    quest.classList.add("flash");
  }
}

function setDrawer(open) {
  mapUI.drawerOpen = open;
  document.querySelector(".map-layout").classList.toggle("drawer-closed", !open);
  const btn = document.getElementById("drawer-toggle");
  btn.setAttribute("aria-expanded", open);
  const arrow = btn.querySelector(".drawer-arrow");
  if (arrow) arrow.innerHTML = iconSvg(open ? "chevron-right" : "chevron-left", 15);
  // 서랍이 열리고 닫힌 뒤 지도 크기를 다시 맞춤
  setTimeout(() => mapUI.leaflet && fitFull(mapUI.leaflet, false), 230);
}

// ---------- 화면 전환 안내 ("세관 불러오는 중") ----------
async function showTransition(title, text, action) {
  const box = document.getElementById("transition");
  document.getElementById("transition-map").textContent = title;
  document.getElementById("transition-text").textContent = text;
  box.hidden = false;
  box.classList.remove("leaving");
  const started = Date.now();
  try {
    await action();
  } finally {
    const wait = Math.max(0, TRANSITION_MS - (Date.now() - started));
    setTimeout(() => {
      box.classList.add("leaving");
      setTimeout(() => { box.hidden = true; }, 300);
    }, wait);
  }
}

// ---------- 게임 상태가 바뀌면 자동 전환 (topbar.js 의 game-state 알림) ----------
function onGameState(s) {
  const inRaid = (s.status === "loading" || s.status === "raid") && s.map_id;
  if (inRaid) {
    clearTimeout(mapUI.lobbyTimer);
    mapUI.lobbyTimer = null;
    if (mapUI.autoMapId === s.map_id && currentTab() === "map") return; // 이미 그 맵
    const target = mapUI.maps.find((m) => m.ids.includes(s.map_id));
    if (!target) return; // 맵 목록을 아직 못 받음 → 받은 뒤 다시 확인 (loadMapList 끝)
    mapUI.autoMapId = s.map_id;
    showTransition(target.name, t("map.loading"), async () => {
      mapUI.current = target;
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
      document.getElementById("map-auto").hidden = true;
      if (currentTab() !== "map") return; // 사용자가 다른 화면을 보고 있으면 그대로
      showTransition(t("map.lobby"), t("map.returning"), async () => goTab("home"));
    }, s.cancelled ? 0 : LOBBY_WAIT_MS);
  }
}
window.addEventListener("game-state", (e) => onGameState(e.detail));

// ---------- 이벤트 연결 ----------
document.getElementById("map-select").addEventListener("change", (e) => chooseMap(e.target.value));
document.getElementById("drawer-toggle").addEventListener("click", () => setDrawer(!mapUI.drawerOpen));

document.addEventListener("tab-shown", (e) => {
  if (e.detail !== "map") return;
  if (mapUI.dirty || !mapUI.leaflet) drawMap();
  else setTimeout(() => mapUI.leaflet && fitFull(mapUI.leaflet, false), 0);
});

// 창 크기가 바뀌면 (태블릿 돌리기 등) 다시 맞춤
let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (mapUI.leaflet && currentTab() === "map") fitFull(mapUI.leaflet, false);
  }, 200);
});

// 은신처에서 '모았음'·건설 완료하면 먹을 것도 다시
document.addEventListener("hideout-changed", () => {
  renderLootBar();
  if (mapUI.leaflet) drawLoot();
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
loadMapList();

// ---------- 표시 켜기/끄기 (왼쪽 서랍) ----------
// 퀘스트·먹을 것·탈출구·트랜짓·열쇠·귀중품. 고른 값은 이 브라우저에 기억 (다음에 켜도 그대로)
const LAYER_KEYS = ["quests", "loot", "extracts", "scavExtracts", "transits", "keys", "bitcoin", "keySpawns", "valuables"];
const LAYER_INFO = {
  quests: { label: t("map.layerQuests"), mark: "shape:quest", on: true },
  loot: { label: t("map.layerLoot"), mark: "shape:loot", on: true },  // 바닥 위치를 아는 아이템만 (없으면 0)
  extracts: { label: t("map.layerExtracts"), mark: "shape:extract", on: true },
  scavExtracts: { label: t("map.layerScavExtracts"), mark: "shape:scav", on: false },
  transits: { label: t("map.layerTransits"), mark: "shape:transit", on: true },
  keys: { label: t("map.layerLocks"), mark: "icon:lock", on: true },          // 열쇠로 여는 곳
  bitcoin: { label: t("map.layerBitcoin"), mark: "icon:bitcoin", on: true },
  keySpawns: { label: t("map.layerKeySpawns"), mark: "icon:key", on: true },      // 열쇠·키카드가 바닥에 생기는 곳
  valuables: { label: t("map.layerValuables"), mark: "shape:valuable", on: false }, // 맵마다 수백 곳이라 기본은 끔
};
const LAYER_STORAGE = "tarkovhelper.layers";
const layerState = (() => {
  try { return JSON.parse(localStorage.getItem(LAYER_STORAGE) || "{}"); } catch (e) { return {}; }
})();

function layerOn(key) {
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
  renderLayerDrawer();
}

function renderLayerDrawer() {
  const list = document.getElementById("layer-list");
  list.replaceChildren();
  LAYER_KEYS.forEach((key) => {
    const info = LAYER_INFO[key];
    const on = layerOn(key);
    const btn = el("button", `layer-toggle layer-${key}${on ? " on" : ""}`);
    btn.type = "button";
    btn.setAttribute("aria-pressed", on);
    // 기호: 지도에 찍히는 모양 그대로 (그림 아이콘 또는 같은 색·모양의 작은 도형)
    const mark = el("span", "layer-mark");
    if (info.mark.startsWith("icon:")) mark.innerHTML = iconSvg(info.mark.slice(5), 14);
    else mark.append(el("i", `legend legend-${info.mark.slice(6)}`));
    // 이 맵에 몇 개 있는지 (0이면 지도에 안 보이는 이유를 알 수 있게)
    const count = layerCount(key);
    const name = el("span", "layer-name", info.label);
    if (count !== null) name.append(el("small", "layer-count", ` ${count}`));
    btn.append(mark, name, el("span", "layer-state", on ? "ON" : "OFF"));
    btn.addEventListener("click", () => setLayer(key, !layerOn(key)));
    list.append(btn);
  });
}

// 표시 묶음마다 이 맵에 있는 개수 (지도를 아직 안 그렸으면 null)
function layerCount(key) {
  if (!mapUI.groups || !mapUI.leaflet) return null;
  if (key === "quests") return (mapUI.pinMarkers || []).length;
  if (key === "loot") return mapUI.lootLayer ? mapUI.lootLayer.getLayers().length : 0;
  return mapUI.groups[key] ? mapUI.groups[key].getLayers().length : 0;
}

function setLayerDrawer(open) {
  mapUI.layerDrawerOpen = open;
  document.querySelector(".map-layout").classList.toggle("layers-closed", !open);
  document.getElementById("layer-toggle-btn").setAttribute("aria-expanded", open);
  try { localStorage.setItem("tarkovhelper.layerDrawer", open ? "1" : "0"); } catch (e) { /* 무시 */ }
  setTimeout(() => mapUI.leaflet && fitFull(mapUI.leaflet, false), 230);
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
  const add = (groupKey, p, icon, tooltip, permanent = false) => {
    const marker = L.marker(gamePos(p), { icon, keyboard: false });
    if (tooltip) marker.bindTooltip(tooltip, permanent
      ? { permanent: true, direction: "right", offset: [6, 0], className: "feature-label" }
      : { direction: "top", offset: [0, -8] });
    const floor = floorOf(p);
    marker.on("click", () => goToFloorOf(floor)); // 누르면 그 표시가 있는 층으로
    marker.addTo(mapUI.groups[groupKey]);
    mapUI.floorItems.push({ layer: marker, floor });
  };
  const dot = (cls, size) => L.divIcon({ className: `feature ${cls}`, html: "<span></span>", iconSize: [size, size], iconAnchor: [size / 2, size / 2] });

  // 탈출구·트랜짓: 이름(영어)을 항상 옆에 표시 → 한눈에 어디로 나갈지
  data.extracts.forEach((e) => {
    const group = e.faction === "scav" ? "scavExtracts" : "extracts";
    add(group, e.pos, dot(`extract extract-${e.faction}`, 16), escapeHtml(e.name), true);
  });
  data.transits.forEach((tr) => add("transits", tr.pos, dot("transit", 16), escapeHtml(tr.name), true));
  // 열쇠 문: 마우스를 올리면 필요한 열쇠
  data.locks.forEach((lock) => {
    const where = lock.type === "trunk" ? t("map.lockTrunk") : "";
    const power = lock.power ? t("map.lockPower") : "";
    add("keys", lock.pos, L.divIcon({ className: "feature lock-pin", html: `<span>${iconSvg("lock", 13, 2.4)}</span>`, iconSize: [16, 16], iconAnchor: [8, 8] }),
      `<b>${escapeHtml(lock.key)}</b>${where}${power}`);
  });
  // 바닥 아이템 자리: 비트코인 / 열쇠 자리 / 기타 귀중품 (마우스를 올리면 그 자리 아이템)
  data.loot.forEach((spot) => {
    if (spot.bitcoin) add("bitcoin", spot.pos, L.divIcon({ className: "feature btc-pin", html: `<span>${iconSvg("bitcoin", 11, 2.6)}</span>`, iconSize: [16, 16], iconAnchor: [8, 8] }), `<b>${t("map.bitcoin")}</b>`);
    if (spot.keys.length) {
      const list = spot.keys.slice(0, 5).map(escapeHtml).join("<br>") + (spot.keys.length > 5 ? t("map.andMore", { n: spot.keys.length - 5 }) : "");
      add("keySpawns", spot.pos, L.divIcon({ className: "feature keyspawn-pin", html: `<span>${iconSvg("key", 13, 2.4)}</span>`, iconSize: [16, 16], iconAnchor: [8, 8] }), `<b>${t("map.keySpawn")}</b><br>${list}`);
    }
    if (spot.items.length) {
      const more = spot.more ? t("map.andMore", { n: spot.more }) : "";
      add("valuables", spot.pos, dot("valuable", 7), spot.items.map(escapeHtml).join("<br>") + more);
    }
  });
  applyFloor();
  renderLayerDrawer();
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
