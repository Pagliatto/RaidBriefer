# 번역 규칙 (data/ko_overrides.json)

게임 공식 한글 번역이 없는 **퀘스트 목표 문장만** 우리가 번역한다.

- 퀘스트 **이름은 번역하지 않는다** (게임 화면과 같아야 찾기 쉬움)
- 화면에는 **우리 번역을 위에, 영어 원문을 아래에 작게** 함께 보여준다 → 번역이 없거나 오래돼도 틀린 정보가 안 나옴
- 각 항목에 번역할 때의 영어 원문(`en`)을 같이 저장한다. 지금 원문과 다르면 그 번역은 쓰지 않는다 (원문이 바뀐 것)
- 공식 한글 번역이 생기면 공식 번역이 이긴다

## 저장 형식

```json
"목표 id": { "en": "번역할 때의 영어 원문", "ko": "우리 번역" }
```

## 말투 (기존 공식 번역을 따라감)

| 영어 | 한글 |
|---|---|
| on Customs | 세관(Customs)에서 |
| Eliminate any target | 아무 적 사살하기 |
| Eliminate Scavs / PMC operatives | 스캐브 사살하기 / PMC 사살하기 |
| Locate and neutralize ○○ | ○○ 찾아서 사살하기 |
| Locate ○○ | ○○ 찾기 |
| Locate and obtain ○○ | ○○ 찾아서 획득하기 |
| Hand over ○○ | ○○ 건네주기 |
| found in raid | 레이드에서 발견한 / 인레이드 |
| Stash ○○ at/in ○○ | ○○에 [아이템] 숨기기 |
| Mark ○○ with an MS2000 Marker | ○○에 MS2000 마커 설치하기 |
| Survive and extract | 살아서 탈출하기 |
| (In one raid) | (한 레이드 내에) |
| while wearing ○○ | ○○을 착용한 상태로 |

- 아이템 이름은 `[대괄호]`로 감싸고, 공식 한글 이름이 있으면 그것을, 없으면 영어 그대로 쓴다.
- 맵 이름: 세관(Customs), 해안선(Shoreline), 삼림(Woods), 인터체인지(Interchange), 공장(Factory), 리저브(Reserve), 등대(Lighthouse), 타르코프 시내(Streets of Tarkov), 그라운드 제로(Ground Zero), 연구소(The Lab), 터미널(Terminal)
- 보스: 르샬라(Reshala), 카반(Kaban), 글루하(Glukhar), 세니타(Sanitar), 로그(Rogue), 스캐브(Scav)
- 상인: 프라퍼, 테라피스트, 스키어, 피스키퍼, 메카닉, 래그맨, 예거
