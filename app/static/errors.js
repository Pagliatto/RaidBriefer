// 화면(자바스크립트) 오류 기록 (오류 코드 방법 B, 2026-11-23 사용자, report_log.py)
// 다른 화면 파일보다 먼저 읽혀서 모든 화면 오류를 잡는다.
// 서버에는 '우리 화면 파일 이름·줄·오류 종류'만 보낸다. 오류 문장·주소·화면 내용은 보내지 않음
// (문장에 퀘스트 이름이나 경로가 섞일 수 있음). 서버가 ERR-js-<파일>-<줄> 코드로 report.txt 에 적는다.
(() => {
  const sent = new Set();
  const OUR_FRAME = /\/static\/(\w+)\.js(?:\?[^:\s)]*)?:(\d+):\d+/; // vendor/leaflet 등 남의 파일은 안 맞음
  const OUR_FILE = /\/static\/(\w+)\.js(?:\?.*)?$/;
  // 이 화면이 실제로 읽은 우리 파일 이름만 보낸다 (오류 문장에 섞인 이름이 나가지 않게)
  const ours = (name) => [...document.scripts].some((s) => OUR_FILE.exec(s.src)?.[1] === name);
  // 연결이 끊겼을 때 브라우저가 내는 문장 (WebView2·Chrome / Safari / Firefox)
  const NETWORK_FAIL = /^(Failed to fetch|Load failed|NetworkError when attempting to fetch resource\.?)$/;

  // 오류에 가장 가까운 우리 파일 줄 (스택 맨 위부터)
  function place(error, file, line) {
    const stack = error && typeof error.stack === "string" ? error.stack : "";
    for (const row of stack.split("\n")) {
      if (!/^\s+at\s/.test(row)) continue; // 첫 줄(오류 문장)은 건너뛰고 실제 호출 위치 줄만 본다
      const found = OUR_FRAME.exec(row);
      if (found && ours(found[1])) return [found[1], Number(found[2])];
    }
    const found = typeof file === "string" ? OUR_FILE.exec(file) : null;
    if (found && ours(found[1]) && Number.isInteger(line)) return [found[1], line];
    return ["unknown", 0];
  }

  function report(error, file, line) {
    try {
      const type = error && typeof error.name === "string" && /^[A-Za-z_]\w{0,59}$/.test(error.name) ? error.name : "Error";
      if (type === "AbortError") return; // 지도 전환 등에서 일부러 취소한 요청
      // 프로그램이 꺼지거나 다시 켜지는 중의 연결 실패는 오류가 아님 (문장은 여기서만 보고 보내지 않음)
      if (type === "TypeError" && NETWORK_FAIL.test(String(error && error.message))) return;
      const [name, row] = place(error, file, line);
      const key = `${name}:${row}:${type}`;
      if (sent.has(key) || sent.size >= 20) return; // 같은 오류는 화면을 새로 열 때까지 한 번만
      sent.add(key);
      fetch("/api/report/js-error", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file: name, line: row, type }),
      }).catch(() => {});
    } catch (_) {
      // 기록하다 생긴 문제로 화면을 더 망가뜨리지 않음
    }
  }

  window.addEventListener("error", (event) => {
    if (event.target && event.target !== window) return; // 그림·파일 못 받음은 화면 코드 오류가 아님
    report(event.error, event.filename, event.lineno);
  });
  window.addEventListener("unhandledrejection", (event) => report(event.reason, null, null));
})();
