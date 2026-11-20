# NOTICE — 출처와 라이선스

RaidBriefer는 **Escape from Tarkov PvE 모드용 비공식 팬 도구**입니다.
**Battlestate Games(BSG)와 관련이 없으며, BSG가 만들거나 승인한 프로그램이 아닙니다.**
"Escape from Tarkov", 게임 속 이름·아이템 그림 등은 Battlestate Games Limited 의 자산입니다.

RaidBriefer is an **unofficial fan-made tool** for the PvE mode of Escape from Tarkov.
It is **not affiliated with or endorsed by Battlestate Games**. Game names, terms and item images
belong to Battlestate Games Limited.

## 이 프로젝트의 라이선스

| 대상 | 라이선스 |
|---|---|
| 소스 코드 (`app/`, `tools/`, 배치 파일) | **MIT** — `LICENSE` 참고 |
| 스토리 데이터 (`data/story/`) | **CC BY-NC-SA 3.0** — 공식 위키 내용을 번역·요약한 2차 저작물 (아래 참고) |
| tarkov.dev 에 없는 퀘스트 (`data/corrections.json`) | **CC BY-NC-SA 3.0** — 공식 위키의 퀘스트 목표를 옮기고 번역한 것 (항목마다 위키 주소) |
| 로고·아이콘 (`docs/logo/`, `app/static/icons/`) | MIT (이 프로젝트에서 만든 것) |

**비상업 조건**: 스토리 데이터와 지도 그림이 "비상업(NC)" 조건이라, RaidBriefer 를 유료로 팔거나 광고를 붙여 배포하면 안 됩니다.

## 사용하는 외부 자료

### 데이터 (실행할 때 인터넷에서 받음, 저장소에는 넣지 않음)
- **tarkov.dev** (The Hideout) — 퀘스트·맵·아이템·은신처 데이터 `https://json.tarkov.dev`, 아이템 아이콘 `https://assets.tarkov.dev`
  - https://tarkov.dev · https://github.com/the-hideout
- **SPT** (`SP-Tushonka/server-csharp`) — tarkov.dev 에 아직 없는 퀘스트의 이름(번역 파일)·맵·이벤트 목록. 깃허브에서 데이터 파일만 받고, 필요한 칸만 골라 `cache/` 에 저장
  - https://github.com/SP-Tushonka/server-csharp
- **tarkov.dev 지도 설정** — `the-hideout/tarkov-dev` 의 `src/data/maps.json` (MIT)
- **지도 그림 (SVG Maps)** — `the-hideout/tarkov-dev-svg-maps`, **CC BY-NC-SA 4.0**. 그린 사람(예: Shebuka)은 프로그램 지도 오른쪽 아래에 표시
  - https://github.com/the-hideout/tarkov-dev-svg-maps

### 스토리 데이터의 출처
- **Escape from Tarkov Wiki (Fandom)** — https://escapefromtarkov.fandom.com/wiki/Story_chapters
  - 위키 라이선스 **CC BY-NC-SA** (위키에 직접 확인, 2026-11-18). 이 프로젝트의 `data/story/` 는 위키 내용을 한국어로 번역·요약한 것이라 같은 조건 **CC BY-NC-SA 3.0** 으로 배포합니다.
  - 프로그램 스토리 탭 아래에 출처를 표시합니다.

### 프로그램에 포함되거나 불러오는 라이브러리·글꼴·아이콘
| 이름 | 쓰는 곳 | 라이선스 |
|---|---|---|
| Flask | 프로그램 서버 | BSD-3-Clause |
| Requests | 데이터 받기 | Apache-2.0 |
| pywebview | 프로그램 창 | BSD-3-Clause |
| PyInstaller | exe 로 묶기 (실행 파일 안의 시작 장치) | GPL-2.0 + bootloader 예외 (묶은 프로그램은 자유롭게 배포 가능) |
| Python | exe 안에 포함된 실행 환경 | PSF License |
| Leaflet 1.9.4 | 지도 표시 (cdnjs) | BSD-2-Clause |
| Pretendard 1.3.9 | 글꼴 (jsDelivr) | SIL Open Font License 1.1 |
| Lucide | 아이콘 (`app/static/icons.js` 에 그림 데이터 포함) | ISC |

<details>
<summary>Lucide ISC License</summary>

```
ISC License

Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of Feather (MIT).
All other copyright (c) for Lucide are held by Lucide Contributors 2022.

Permission to use, copy, modify, and/or distribute this software for any purpose with or without
fee is hereby granted, provided that the above copyright notice and this permission notice appear
in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS
SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE
AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE
OF THIS SOFTWARE.
```
</details>

## 게임과의 관계 (안전)
- RaidBriefer 는 게임이 남기는 **로그 파일을 읽기만** 합니다.
- 게임 **메모리 읽기, 게임 파일 수정, 모드, 화면 위에 그리기(오버레이)는 하지 않습니다.**
- 게임이 켜져 있는지는 작업 관리자처럼 **실행 중인 프로그램 이름만** 확인합니다.
