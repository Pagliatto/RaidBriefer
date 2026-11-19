// 화면 글자 사전 (언어별). 서버가 페이지에 window.I18N 으로 사전을 넣어 줌 (app/i18n/<언어>.json)
// 사용: t("home.collectTitle"), 값 넣기: t("home.mapTodo", { name: "세관" }) → "세관에서 할 일"

function t(key, values) {
  let text = key.split(".").reduce((node, part) => (node && typeof node === "object" ? node[part] : undefined), window.I18N);
  if (typeof text !== "string") {
    console.warn("[사전] 없는 이름표:", key); // i18n-ok: 개발용 메시지
    return key;
  }
  if (values) text = text.replace(/\{(\w+)\}/g, (all, name) => (name in values ? values[name] : all));
  return text;
}

// 문장 중간에 굵은 글씨 같은 요소를 넣을 때: tNodes("setup.foundQuests", { n: el("b", "", "62개") })
// → ["진행 중인 퀘스트 ", <b>62개</b>, "를 찾았어요."] (언어마다 순서가 달라도 사전대로 배치됨)
function tNodes(key, values) {
  const text = t(key);
  return text.split(/(\{\w+\})/).filter((part) => part !== "").map((part) => {
    const m = /^\{(\w+)\}$/.exec(part);
    return m && values && m[1] in values ? values[m[1]] : part;
  });
}
