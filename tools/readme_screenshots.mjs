// README 화면 사진 다시 찍기: 프로그램을 켜 둔 상태에서
//   node tools/readme_screenshots.mjs http://127.0.0.1:8765/ docs/screenshots %TEMP%/rb-shot
// (보이지 않는 엣지 창으로 탭을 차례로 누르고 960x1080 사진을 저장)
// Headless Edge + DevTools protocol: open the app, click tabs, save screenshots.
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
const [,, url, outDir, profile] = process.argv;
const edge = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const port = 9333;
const proc = spawn(edge, ["--headless=new", "--disable-gpu", "--hide-scrollbars", `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, "--window-size=960,1080", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let target;
for (let i = 0; i < 50 && !target; i++) {
  await sleep(200);
  try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((t) => t.type === "page"); } catch {}
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0; const wait = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (wait.has(d.id)) { wait.get(d.id)(d); wait.delete(d.id); } };
const send = (method, params = {}) => new Promise((r) => { const i = ++id; wait.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const js = (expression) => send("Runtime.evaluate", { expression, awaitPromise: true });
await send("Emulation.setDeviceMetricsOverride", { width: 960, height: 1080, deviceScaleFactor: 1, mobile: false });
await send("Page.navigate", { url });
await sleep(6000);
const shots = [
  ["story", "goTab('story')", 2500],
  ["quests", "goTab('quest')", 1500],
  ["home", "goTab('home')", 1500],
  ["hideout", "goTab('hideout')", 1500],
  ["map", "goTab('map'); setTimeout(()=>{ const s=document.getElementById('map-select'); s.value='terminal'; s.dispatchEvent(new Event('change')); setTimeout(()=>{ if (mapUI.leaflet) fitFull(mapUI.leaflet, true); }, 5000); }, 1500)", 9000],
];
for (const [name, code, ms] of shots) {
  await js(code); await sleep(ms);
  // '바뀐 점' 창 등 열린 대화 상자는 사진에서 빼기
  await js("document.querySelectorAll('dialog[open]').forEach((d) => d.close())"); await sleep(300);
  const r = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(`${outDir}/${name}.png`, Buffer.from(r.result.data, "base64"));
  console.log("saved", name);
}
ws.close();
// msedge.exe 는 실제 브라우저를 띄우고 먼저 끝나므로 proc.kill() 로는 안 꺼짐 → 브라우저에 직접 닫기 요청
try {
  const { webSocketDebuggerUrl } = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  const bws = new WebSocket(webSocketDebuggerUrl);
  await new Promise((r) => (bws.onopen = r));
  bws.send(JSON.stringify({ id: 1, method: "Browser.close" }));
  await sleep(1500);
} catch {}
proc.kill();
process.exit(0);
