"""PC 프로그램의 웹 서버.

실행: .venv\\Scripts\\python.exe -m app.server
그다음 브라우저에서 http://127.0.0.1:8765 를 연다.
"""

import atexit
import json
import time
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file

from app import brightness, game_state, hideout, i18n, updater, map_views, paths, progress, quest_log, quests, settings, story, tarkov_api

from app.version import VERSION

HOST = "127.0.0.1"  # 지금은 이 PC에서만 열림 (휴대폰 접속은 다음 단계)
PORT = 8765

app = Flask(__name__)

# 로그 전체를 읽는 데 1초쯤 걸려서, 퀘스트 알림 로그가 그대로면 계산한 결과를 재사용
# (로그가 바뀌면 바로 다시 계산. 혹시 몰라 1분이 지나도 다시)
_cache = {"at": 0.0, "data": None, "rev": None}
CACHE_SECONDS = 60


@app.route("/")
def home():
    lang = i18n.current_lang()
    return render_template("index.html", lang=lang, i18n=i18n.bundle(lang), t=lambda key, **v: i18n.t(key, lang, **v))


@app.get("/favicon.<any(ico,svg):ext>")
def favicon(ext):
    # 프로그램이 쓰는 아이콘은 app/static/icons 에 (docs/logo 는 원본 보관용, exe 로 묶을 때 빠질 수 있음)
    icon_dir = Path(app.root_path) / "static" / "icons"
    if ext == "svg":
        return send_file(icon_dir / "favicon.svg", mimetype="image/svg+xml")
    return send_file(icon_dir / "RaidBriefer.ico", mimetype="image/x-icon")


@app.route("/api/quests")
def api_quests():
    rev = quest_log.log_rev()
    if _cache["data"] is None or _cache["rev"] != rev or time.time() - _cache["at"] > CACHE_SECONDS:
        try:
            _cache["data"] = quests.build()
            _cache["at"] = time.time()
            _cache["rev"] = rev
        except Exception as error:  # 화면에 오류 안내를 보여주기 위해
            return jsonify({"error": i18n.t("server.questsFailed", error=error)}), 500
    return jsonify(_cache["data"])


@app.route("/api/quest-catalog")
def api_quest_catalog():
    """처음 설정 2단계: 직접 고를 수 있는 전체 퀘스트 목록"""
    try:
        return jsonify(quests.catalog())
    except Exception as error:
        return jsonify({"error": i18n.t("server.catalogFailed", error=error)}), 500


@app.route("/api/log-gap/ok", methods=["POST"])
def api_log_gap_ok():
    """'로그가 지워진 것 같아요' 안내 확인: 지금 단서가 된 퀘스트들을 '확인함'으로 저장"""
    body = request.get_json(silent=True) or {}
    ids = body.get("ids")
    if not isinstance(ids, list) or len(ids) > 1000 or not all(isinstance(i, str) and 0 < len(i) <= 100 for i in ids):
        return jsonify({"error": i18n.t("server.badRequest")}), 400
    for quest_id in ids:
        progress.set_check("gap_ok", quest_id, True)
    _cache["data"] = None
    return jsonify({"ok": True})


@app.route("/api/maps")
def api_maps():
    """지도 화면: 고를 수 있는 맵 + 맵마다 지도 그림·좌표 맞추는 값"""
    try:
        result = []
        for m in map_views.map_list():
            result.append({**m, "view": map_views.view_for(m["key"])})
        return jsonify(result)
    except Exception as error:
        return jsonify({"error": i18n.t("server.mapsFailed", error=error)}), 500


@app.route("/api/map-features/<map_key>")
def api_map_features(map_key):
    """지도에 같이 표시할 것: 탈출구·트랜짓·열쇠 문·귀중품"""
    if len(map_key) > 60:
        return jsonify({"error": i18n.t("server.badRequest")}), 400
    try:
        return jsonify(map_views.features(map_key))
    except Exception as error:
        return jsonify({"error": i18n.t("server.featuresFailed", error=error)}), 500


@app.route("/api/loot/<map_key>")
def api_loot(map_key):
    """먹을 아이템이 이 맵 바닥에 생기는 위치. ?ids=아이템id,아이템id"""
    ids = {i for i in request.args.get("ids", "").split(",") if 0 < len(i) <= 40}
    if len(ids) > 300 or len(map_key) > 60:
        return jsonify({"error": i18n.t("server.badRequest")}), 400
    try:
        return jsonify(map_views.loose_spots(map_key, ids))
    except Exception as error:
        return jsonify({"error": i18n.t("server.lootFailed", error=error)}), 500


@app.route("/map-svg/<name>")
def map_svg(name):
    """지도 그림 (처음 한 번 tarkov.dev 에서 받아 cache/map_svg 에 저장)"""
    try:
        path = map_views.svg_file(name)
    except Exception:
        abort(502)
    if path is None:
        abort(404)
    return send_file(path, mimetype="image/svg+xml", max_age=24 * 3600)


@app.route("/api/hideout")
def api_hideout():
    """은신처: 시설마다 지금 레벨 + 다음 레벨 재료·조건, 다음 레벨 재료 합계"""
    try:
        return jsonify(hideout.build())
    except Exception as error:
        return jsonify({"error": i18n.t("server.hideoutFailed", error=error)}), 500


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """전체 초기화: 모든 체크 기록을 지우고, 설정도 처음 상태로 (게임 설치 위치만 남김) → 처음 설정부터 다시"""
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True:  # 실수로 지우지 않게 확인 값이 있어야 함
        return jsonify({"error": i18n.t("server.badRequest")}), 400
    progress.reset_all()
    game_dir = settings.load()["game_dir"]
    settings.save({**settings.DEFAULTS, "game_dir": game_dir})
    _cache["data"] = None
    return jsonify({"ok": True})


@app.get("/api/update")
def api_update():
    """새 버전 안내용: 지금 버전·최신 버전·진행 상태"""
    return jsonify(updater.state())


@app.post("/api/update/apply")
def api_update_apply():
    """[지금 업데이트]: 받아서 확인하고 도우미를 켠 뒤 프로그램을 닫음 (exe 일 때만)"""
    if not (request.get_json(silent=True) or {}).get("confirm"):
        abort(400)
    try:
        return jsonify(updater.apply())
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/state")
def api_state():
    """지금 게임 상태 (상단바·화면 자동 전환용). 화면이 2초마다 물어봄"""
    state = game_state.get()
    state["data_updating"] = tarkov_api.is_updating()
    state["quest_rev"] = quest_log.log_rev()  # 바뀌면 화면이 퀘스트를 새로 불러옴
    return jsonify(state)


@app.get("/api/brightness")
def api_brightness_get():
    return jsonify(brightness.status())


@app.post("/api/brightness")
def api_brightness_set():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": i18n.t("server.badRequest")}), 400
    return jsonify(brightness.configure(body))


@app.post("/api/brightness/preview")
def api_brightness_preview():
    return jsonify(brightness.preview())


@app.post("/api/brightness/identify")
def api_brightness_identify():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("monitor"), str):
        return jsonify({"error": i18n.t("server.badRequest")}), 400
    return jsonify(brightness.identify(body["monitor"]))


@app.post("/api/brightness/restore")
def api_brightness_restore():
    return jsonify(brightness.restore())


def _release_notes() -> dict | None:
    """현재 버전의 읽을 수 있는 안내만 사용한다. 없거나 깨지면 안내를 생략한다."""
    try:
        data = json.loads((paths.DATA_DIR / "release_notes.json").read_text(encoding="utf-8"))
        notes = data.get(VERSION) if isinstance(data, dict) else None
        if not isinstance(notes, dict):
            return None
        result = {lang: [line for line in lines if isinstance(line, str) and line.strip()]
                  for lang, lines in notes.items() if lang in ("ko", "en") and isinstance(lines, list)}
        if not any(result.values()):
            return None
        if notes.get("action") == "brightness":
            result["action"] = "brightness"
        return result
    except (OSError, ValueError):
        return None


@app.route("/api/whats-new")
def api_whats_new():
    current = settings.load()
    notes = _release_notes()
    show = bool(current["setup_done"] and current["last_seen_version"] != VERSION and notes)
    return jsonify({"version": VERSION, "show": show, "notes": notes})


@app.route("/api/whats-new/seen", methods=["POST"])
def api_whats_new_seen():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or body.get("version") != VERSION:
        return jsonify({"error": i18n.t("server.badRequest")}), 400
    settings.save({"last_seen_version": VERSION})
    return jsonify({"version": VERSION, "seen": True})


@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    current = settings.load()
    return jsonify({**current, "lang": i18n.current_lang(), "langs": list(i18n.LANGS.items()),  # 목록으로 (순서 유지)
                    "check": settings.check_game_dir(current["game_dir"])})


@app.route("/api/settings", methods=["POST"])
def api_settings_set():
    """보내는 내용: {"game_dir": "D:\\Battlestate Games\\Escape from Tarkov"}
    또는 {"find": true} → 모든 드라이브에서 자동으로 찾기"""
    body = request.get_json(silent=True) or {}
    if "hideout_all" in body:  # 은신처 재료 합계에 조건 남은 시설도 합칠지 (켜기/끄기)
        if not isinstance(body["hideout_all"], bool):
            return jsonify({"error": i18n.t("server.badRequest")}), 400
        saved = settings.save({"hideout_all": body["hideout_all"]})
        return jsonify({**saved, "check": settings.check_game_dir(saved["game_dir"])})
    if "lang" in body:  # 화면·게임 데이터 언어 → 화면은 새로고침해서 반영
        if body["lang"] not in i18n.LANGS:
            return jsonify({"error": i18n.t("server.badRequest")}), 400
        saved = settings.save({"lang": body["lang"]})
        _cache["data"] = None  # 퀘스트 이름·목표를 새 언어로 다시 계산
        return jsonify({**saved, "check": settings.check_game_dir(saved["game_dir"])})
    if "edition" in body:  # 게임 에디션 (은신처 시작 레벨)
        if body["edition"] not in settings.EDITIONS:
            return jsonify({"error": i18n.t("server.badRequest")}), 400
        saved = settings.save({"edition": body["edition"]})
        return jsonify({**saved, "check": settings.check_game_dir(saved["game_dir"])})
    if "setup_done" in body:  # 처음 설정 화면을 끝냄(true) / 다시 하기(false)
        if not isinstance(body["setup_done"], bool):
            return jsonify({"error": i18n.t("server.badRequest")}), 400
        changes = {"setup_done": body["setup_done"]}
        if body["setup_done"]:
            changes["last_seen_version"] = VERSION
        saved = settings.save(changes)
        return jsonify({**saved, "check": settings.check_game_dir(saved["game_dir"])})
    if body.get("find"):
        found = settings.find_game_dir()
        if not found:
            return jsonify({"error": i18n.t("server.notFoundGame")}), 404
        game_dir = found
    else:
        game_dir = body.get("game_dir")
        if not isinstance(game_dir, str) or not game_dir.strip() or len(game_dir) > 500:
            return jsonify({"error": i18n.t("server.needGameDir")}), 400
        game_dir = game_dir.strip().strip('"')
    check = settings.check_game_dir(game_dir)
    if not check["ok"]:  # 틀린 위치는 저장하지 않음
        return jsonify({"error": check["message"]}), 400
    saved = settings.save({"game_dir": game_dir})
    _cache["data"] = None  # 퀘스트를 새 위치의 로그로 다시 계산
    return jsonify({**saved, "check": check})


@app.route("/api/story")
def api_story():
    try:
        return jsonify(story.build())
    except Exception as error:
        return jsonify({"error": i18n.t("server.storyFailed", error=error)}), 500


# 체크 저장: kind = handed(제출) 또는 story(스토리 단계)
@app.route("/api/check/<kind>", methods=["GET"])
def api_check_get(kind):
    if kind not in progress.KINDS:
        return jsonify({"error": i18n.t("server.badRequest")}), 404
    return jsonify(progress.get(kind))


@app.route("/api/check/<kind>", methods=["POST"])
def api_check_set(kind):
    """단일 {key, value} 저장. 스토리는 {changes: {키: 값 또는 null}} 일괄 저장도 지원."""
    body = request.get_json(silent=True)
    if kind not in progress.KINDS or not isinstance(body, dict):
        return jsonify({"error": i18n.t("server.badRequest")}), 400
    if "changes" in body:
        changes = body["changes"]
        if kind != "story" or not isinstance(changes, dict) or not 1 <= len(changes) <= 500:
            return jsonify({"error": i18n.t("server.badRequest")}), 400
    else:
        key = body.get("key")
        if not isinstance(key, str):
            return jsonify({"error": i18n.t("server.badRequest")}), 400
        changes = {key: body.get("value")}
    # 하나라도 잘못된 값이면 쓰기 전에 전체 요청을 거절한다.
    for key, value in changes.items():
        ok_value = (value is None or isinstance(value, bool)
                    or (isinstance(value, str) and len(value) <= 100)
                    or (isinstance(value, int) and 0 <= value <= 10))  # 은신처 레벨
        if not isinstance(key, str) or not key or len(key) > 300 or not ok_value:
            return jsonify({"error": i18n.t("server.badRequest")}), 400
    result = progress.set_checks(kind, changes)
    if kind == "added":  # 직접 추가한 퀘스트는 퀘스트 목록 자체가 바뀌므로 다시 계산
        _cache["data"] = None
    return jsonify(result)


def serve(port: int = PORT) -> None:
    """서버 켜기 (브라우저 방식·프로그램 창 방식이 같이 씀). 끝날 때까지 돌아오지 않음"""
    import threading

    atexit.register(brightness.shutdown)
    brightness.init()
    game_state.start()  # 게임 로그를 뒤에서 계속 지켜보기 (상단바 상태)
    updater.check_in_background()  # 새 버전이 있는지 깃허브에 한 번 물어봄
    # 프레스티지 찾기는 처음 한 번 몇 초 걸려서 미리 해 둠 (화면이 기다리지 않게)
    threading.Thread(target=quest_log.last_prestige, daemon=True).start()
    app.run(host=HOST, port=port, debug=False)


if __name__ == "__main__":
    import sys
    import threading
    import webbrowser

    port = PORT
    if "--port" in sys.argv:  # 예: --port 8770 (테스트용으로 다른 번호에서 켤 때)
        port = int(sys.argv[sys.argv.index("--port") + 1])

    url = f"http://{HOST}:{port}"
    print(f"브라우저에서 {url} 를 열어 주세요. (끝내려면 Ctrl+C)")  # i18n-ok: 개발용 콘솔
    if "--open" in sys.argv:
        # 서버가 켜질 시간을 1.5초 준 뒤 브라우저 열기
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    serve(port)
