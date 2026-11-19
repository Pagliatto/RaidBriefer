"""화면 글자 사전 (언어별).

- 사전 파일: app/i18n/<언어>.json  (예: ko.json). 이름표("home.collectTitle") → 문장
- 화면(JS)에서는 t("home.collectTitle"), HTML 틀에서는 {{ t('home.collectTitle') }}
- 문장 안의 {name} 은 값으로 바뀜: t("home.mapTodo", name="세관") → "세관에서 할 일"
- 기준 언어는 한국어. 다른 언어 사전에 없는 이름표는 한국어로 보여 줌
"""

import json
import threading

from app import paths

I18N_DIR = paths.BUNDLE_DIR / "app" / "i18n"
DEFAULT_LANG = "ko"
LANGS = {"ko": "한국어", "en": "English"}  # i18n-ok: 언어 이름은 그 언어로 씀. 사전 파일이 있는 언어

_lock = threading.Lock()
_cache: dict[str, dict] = {}


def bundle(lang: str) -> dict:
    """그 언어의 사전 전체 (없는 이름표는 한국어로 채움)"""
    lang = lang if lang in LANGS else DEFAULT_LANG
    with _lock:
        if lang not in _cache:
            base = json.loads((I18N_DIR / f"{DEFAULT_LANG}.json").read_text(encoding="utf-8"))
            if lang != DEFAULT_LANG:
                base = _merge(base, json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8")))
            _cache[lang] = base
        return _cache[lang]


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def current_lang() -> str:
    from app import settings  # settings 가 이 파일을 쓸 수도 있어서 늦게 불러옴

    lang = settings.load().get("lang") or system_lang()  # 고른 적이 없으면 윈도우 언어
    return lang if lang in LANGS else DEFAULT_LANG


def system_lang() -> str:
    """윈도우 화면 언어: 한국어면 ko, 아니면 en"""
    try:
        import ctypes

        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "ko" if lang_id & 0x3FF == 0x12 else "en"  # 0x12 = 한국어
    except Exception:
        return DEFAULT_LANG


def t(key: str, lang: str | None = None, **values) -> str:
    """이름표 → 문장. 사전에 없으면 이름표를 그대로 돌려줌 (검사 도구가 찾아냄)"""
    node = bundle(lang or current_lang())
    for part in key.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    if not isinstance(node, str):
        return key
    for name, value in values.items():
        node = node.replace("{" + name + "}", str(value))
    return node
