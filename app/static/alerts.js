// 알림함 (상단바 🔔, 2026-11-23 사용자 안 A)
// - 서버 /api/state 의 alerts(코드 목록) → 숫자 배지 + 펼침 창 목록. 뜻·문장은 사전 alerts.codes.<코드>
// - dev(소스 실행일 때만 서버가 보냄) → 번역 필요 줄. 배지 숫자에는 넣지 않음
// - 신고 버튼 3개는 링크 열기·복사만 (자동으로 보내는 것 없음)

// 빨강 = 기능이 멈춤 / 노랑 = 저장본·옛 정보로 계속 동작
// 신고 주소 (공개 링크, 비밀 아님. docs/notes/discord.md)
const REPORT_DISCORD = "https://discord.gg/8MYr8QABYU";
const REPORT_GITHUB = "https://github.com/Pagliatto/RaidBriefer/issues/new";
const ALERT_LEVEL = { "LOG-01": "bad", "LOG-02": "bad", "LOG-03": "bad", "SAVE-01": "bad", "DATA-01": "warn", "UPD-01": "warn" };

const alertsBox = (() => {
  const get = (id) => document.getElementById(id);
  const slot = get("alerts-slot");
  const button = get("alerts-open");
  const popup = get("alerts-popup");
  const list = get("alerts-list");
  const badge = get("alerts-badge");
  let drawnKey = null;
  let current = { codes: [], dev: null };

  function codesOf(s) {
    return (Array.isArray(s.alerts) ? s.alerts : []).filter((code) => ALERT_LEVEL[code]);
  }

  function alertRow(code) {
    const level = ALERT_LEVEL[code];
    const row = el("div", `alert-row ${level}`);
    const mark = el("span", "alert-mark");
    mark.innerHTML = iconSvg(level === "bad" ? "triangle-alert" : "circle-alert", 16);
    const title = el("b", "", t(`alerts.codes.${code}.title`));
    title.append(" ", el("span", "alert-code", code));
    row.append(mark, title, el("p", "", t(`alerts.codes.${code}.body`)));
    return row;
  }

  function devRow(count) {
    const row = el("div", "alert-row dev");
    const mark = el("span", "alert-mark");
    mark.innerHTML = iconSvg("languages", 16);
    const title = el("b", "", t("alerts.translationTitle", { count }));
    title.append(" ", el("span", "alert-dev-tag", t("alerts.devTag")));
    row.append(mark, title, el("p", "", t("alerts.translationBody")));
    return row;
  }

  function render() {
    const rows = current.codes.map(alertRow);
    const devCount = current.dev?.translation_needed;
    if (Number.isInteger(devCount) && devCount > 0) rows.push(devRow(devCount));
    if (!current.codes.length) {
      const empty = el("div", "alerts-empty");
      const mark = el("span", "alert-mark");
      mark.innerHTML = iconSvg("circle-check", 22);
      empty.append(mark, el("p", "", t("alerts.empty")));
      rows.unshift(empty);
    }
    list.replaceChildren(...rows);
    get("alerts-help").hidden = !current.codes.length;
  }

  // drawTopbar 가 1초마다 부름 → 바뀌었을 때만 다시 그림
  function draw(s) {
    const codes = codesOf(s);
    const dev = s.dev && typeof s.dev === "object" ? s.dev : null;
    const key = JSON.stringify([codes, dev?.translation_needed ?? null]);
    if (key === drawnKey) return;
    drawnKey = key;
    current = { codes, dev };
    const level = codes.some((code) => ALERT_LEVEL[code] === "bad") ? "bad" : codes.length ? "warn" : "none";
    button.dataset.level = level;
    badge.hidden = !codes.length;
    badge.textContent = codes.length ? String(codes.length) : "";
    const label = codes.length ? t("alerts.countLabel", { count: codes.length }) : t("alerts.title");
    button.setAttribute("aria-label", label);
    button.title = label;
    render();
  }

  function positionPopup() {
    const rect = button.getBoundingClientRect();
    const width = document.documentElement.clientWidth;
    const height = document.documentElement.clientHeight;
    const top = Math.min(rect.bottom + 8, height - 48);
    popup.style.left = `${Math.max(8, Math.min(rect.right - popup.offsetWidth, width - popup.offsetWidth - 8))}px`;
    popup.style.top = `${top}px`;
    popup.style.maxHeight = `${Math.max(32, height - top - 8)}px`;
  }

  function closePopup(returnFocus = false) {
    if (popup.hidden || isWindowClosing(popup)) return;
    button.setAttribute("aria-expanded", "false");
    const restoreFocus = returnFocus || popup.contains(document.activeElement);
    animateClose(popup, () => { popup.hidden = true; }, "popover");
    if (restoreFocus) button.focus();
  }

  function openPopup() {
    cancelWindowMotion(popup);
    popup.hidden = false;
    button.setAttribute("aria-expanded", "true");
    positionPopup();
    animateOpen(popup, "popover");
    popup.focus();
  }

  button.addEventListener("click", () => {
    if (!popup.hidden && !isWindowClosing(popup)) return closePopup(true);
    openPopup();
  });
  document.addEventListener("click", (event) => {
    if (!slot.contains(event.target)) closePopup();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !popup.hidden) {
      event.preventDefault();
      closePopup(true);
    }
  });
  document.addEventListener("focusin", (event) => {
    if (!popup.hidden && !slot.contains(event.target)) closePopup();
  });
  window.addEventListener("resize", () => { if (!popup.hidden) positionPopup(); });

  // ---------- 신고 버튼 3개: 링크 열기·복사만 (자동으로 보내는 것 없음) ----------
  // 버전·코드만 넣는다. 로그 내용·계정·닉네임·폴더 경로는 넣지 않음 (게임 로그에 로그인 정보가 있음)
  async function reportInfo() {
    try {
      const res = await fetch("/api/report-info");
      if (res.ok) return await res.json();
    } catch (error) {
      // 서버가 잠깐 안 되면 버전만 비워 둠
    }
    return {};
  }

  // 위키 링크와 같은 방식(새 창 → 기본 브라우저)으로 연다
  function openExternal(url) {
    const link = el("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener";
    link.hidden = true;
    document.body.append(link);
    link.click();
    link.remove();
  }

  get("alerts-discord").addEventListener("click", () => openExternal(REPORT_DISCORD));

  get("alerts-github").addEventListener("click", async () => {
    const info = await reportInfo();
    const codes = current.codes;
    const params = new URLSearchParams({ template: "bug_report.yml", title: `[Bug] ${codes.join(" ")}`.trim() }); // i18n-ok 이슈 제목 머리말 (양식과 같음)
    if (info.version) params.set("version", info.version);
    if (info.windows) params.set("windows", info.windows);
    if (info.game_version) params.set("game_version", info.game_version);
    if (codes.length) params.set("alert_codes", codes.join(", "));
    params.set("language", document.documentElement.lang === "ko" ? "한국어" : "English"); // i18n-ok 양식 선택지 이름
    openExternal(`${REPORT_GITHUB}?${params}`);
  });

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (error) {
      // 클립보드 권한이 없는 창: 옛 방식으로 한 번 더
      const area = el("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.append(area);
      area.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      area.remove();
      return ok;
    }
  }

  const copyButton = get("alerts-copy");
  let copyTimer = null;
  copyButton.addEventListener("click", async () => {
    const info = await reportInfo();
    const text = t("alerts.reportTemplate", {
      version: info.version || t("alerts.unknown"),
      game: info.game_version || t("alerts.unknown"),
      windows: info.windows || t("alerts.unknown"),
      codes: current.codes.length ? current.codes.join(", ") : t("alerts.noCodes"),
    });
    const ok = await copyText(text);
    copyButton.textContent = t(ok ? "alerts.copied" : "alerts.copyFailed");
    clearTimeout(copyTimer);
    copyTimer = setTimeout(() => { copyButton.textContent = t("alerts.copy"); }, 2000);
  });

  return { draw, codes: () => current.codes.slice() };
})();

function drawAlerts(state) { alertsBox.draw(state); }
