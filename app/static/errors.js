// 화면(자바스크립트) 오류 기록 (오류 코드 방법 B, 2026-11-23 사용자, report_log.py)
// 다른 화면 파일보다 먼저 읽혀서 모든 화면 오류를 잡는다.
// 서버에는 '우리 화면 파일 이름·줄·오류 종류'만 보낸다. 오류 문장·주소·화면 내용은 보내지 않음
// (문장에 퀘스트 이름이나 경로가 섞일 수 있음). 서버가 ERR-js-<파일>-<줄> 코드로 report.txt 에 적는다.
// 우리 파일이 아닌 곳에서 난 오류는 난 곳의 종류만 구분한다 (2026-11-26, 이슈 25 ERR-js-unknown-0 은 원인을 알 수 없었음):
//   leaflet = 지도 부품 안 (한 줄짜리 파일이라 줄 대신 '몇 번째 글자'), browser = 오류 내용 없이 브라우저가 알린 것, unknown = 그 밖
// 같이 보내는 것: 그때 열려 있던 탭(정해진 이름), 미리 정해 둔 문장과 같을 때만 그 짧은 이름(hint)
(() => {
  const sent = new Set();
  const OUR_FRAME = /\/static\/(\w+)\.js(?:\?[^:\s)]*)?:(\d+):\d+/; // vendor/leaflet 등 남의 파일은 안 맞음
  const OUR_FILE = /\/static\/(\w+)\.js(?:\?.*)?$/;
  const LEAFLET_FRAME = /\/static\/vendor\/leaflet\/[\w.-]+\.js(?:\?[^:\s)]*)?:\d+:(\d+)/;
  const LEAFLET_FILE = /\/static\/vendor\/leaflet\/[\w.-]+\.js(?:\?.*)?$/;
  const MAX_PLACE = 999999; // 서버가 받는 가장 큰 숫자
  const TABS = ["home", "quest", "story", "hideout", "map"];
  // 문장은 여기서만 보고, 맞으면 짧은 이름만 보낸다 (문장 자체는 보내지 않음)
  const HINTS = [
    [/^ResizeObserver loop/, "resize-loop"],
    [/^Invalid LatLng object/, "invalid-latlng"],
    [/^Set map center and zoom first/, "map-not-ready"],
    [/^Bounds are not valid/, "invalid-bounds"],
    [/^Map container/, "map-container"],
    [/^Script error\.?$/, "script-error"],
  ];
  // 이 화면이 실제로 읽은 우리 파일 이름만 보낸다 (오류 문장에 섞인 이름이 나가지 않게)
  const ours = (name) => [...document.scripts].some((s) => OUR_FILE.exec(s.src)?.[1] === name);
  // 연결이 끊겼을 때 브라우저가 내는 문장 (WebView2·Chrome / Safari / Firefox)
  const NETWORK_FAIL = /^(Failed to fetch|Load failed|NetworkError when attempting to fetch resource\.?)$/;
  const place999 = (n) => (Number.isInteger(n) && n >= 0 && n <= MAX_PLACE ? n : 0);

  // 오류에 가장 가까운 우리 파일 줄 (스택 맨 위부터). 우리 파일이 없으면 난 곳의 종류
  function place(error, file, line, column, noObject) {
    const stack = error && typeof error.stack === "string" ? error.stack : "";
    let leaflet = null;
    for (const row of stack.split("\n")) {
      if (!/^\s+at\s/.test(row)) continue; // 첫 줄(오류 문장)은 건너뛰고 실제 호출 위치 줄만 본다
      const found = OUR_FRAME.exec(row);
      if (found && ours(found[1])) return [found[1], Number(found[2])];
      if (leaflet === null) {
        const inLeaflet = LEAFLET_FRAME.exec(row);
        if (inLeaflet) leaflet = place999(Number(inLeaflet[1]));
      }
    }
    const found = typeof file === "string" ? OUR_FILE.exec(file) : null;
    if (found && ours(found[1]) && Number.isInteger(line)) return [found[1], line];
    if (leaflet !== null) return ["leaflet", leaflet];
    if (typeof file === "string" && LEAFLET_FILE.test(file)) return ["leaflet", place999(column)];
    if (noObject) return ["browser", 0];
    return ["unknown", 0];
  }

  function openTab() {
    try {
      const tab = document.querySelector(".tab.active")?.dataset.tab;
      return TABS.includes(tab) ? tab : null;
    } catch (_) {
      return null;
    }
  }

  function hintOf(text) {
    const clean = String(text ?? "").replace(/^Uncaught\s+(?:\w+:\s+)?/, "");
    const found = HINTS.find(([rule]) => rule.test(clean));
    return found ? found[1] : null;
  }

  // message: 오류 내용 없이 브라우저가 알린 경우의 문장 (error 가 없을 때만 씀)
  function report(error, file, line, column, message, noObject) {
    try {
      const type = error && typeof error.name === "string" && /^[A-Za-z_]\w{0,59}$/.test(error.name) ? error.name : "Error";
      if (type === "AbortError") return; // 지도 전환 등에서 일부러 취소한 요청
      // 프로그램이 꺼지거나 다시 켜지는 중의 연결 실패는 오류가 아님 (문장은 여기서만 보고 보내지 않음)
      if (type === "TypeError" && NETWORK_FAIL.test(String(error && error.message))) return;
      const hint = hintOf(error && typeof error.message === "string" ? error.message : message);
      // 'ResizeObserver loop' 는 알림을 다음 화면으로 미뤘다는 브라우저 경고일 뿐 화면은 정상 (이슈 43) → 알림함에 띄우지 않음
      if (hint === "resize-loop") return;
      const [name, row] = place(error, file, line, column, noObject);
      const key = `${name}:${row}:${type}`;
      if (sent.has(key) || sent.size >= 20) return; // 같은 오류는 화면을 새로 열 때까지 한 번만
      sent.add(key);
      const body = { file: name, line: row, type };
      const tab = openTab();
      if (tab) body.tab = tab;
      if (hint) body.hint = hint;
      fetch("/api/report/js-error", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).catch(() => {});
    } catch (_) {
      // 기록하다 생긴 문제로 화면을 더 망가뜨리지 않음
    }
  }

  window.addEventListener("error", (event) => {
    if (event.target && event.target !== window) return; // 그림·파일 못 받음은 화면 코드 오류가 아님
    report(event.error, event.filename, event.lineno, event.colno, event.message, event.error == null);
  });
  window.addEventListener("unhandledrejection", (event) => report(event.reason, null, null, null, null, false));
})();
