# FakeShot Phase 0 파일럿

지인 20명 안팎이 사진 한 장을 보고 실제 사진인지 AI 이미지인지 고르는 웹 테스트다. 1차와 2차를 하루 간격으로 30문항씩 풀고, 두 점수의 상관으로 재검사 신뢰도를 잰다.

## 폴더 구성

| 폴더 | 내용 |
|---|---|
| `web/` | 참가자가 여는 정적 웹. 빌드 과정 없이 그대로 배포한다. |
| `supabase/` | DB 스키마와 운영용 SQL |
| `pipeline/` | 사진 정규화, 지름길 점검, 세트 구성, 참가자 링크 생성, 연결 점검 |
| `analysis/` | 신뢰도 분석 |
| `private/` | 정답, 원본 경로, 참가자 목록. 공유하지 않는다 (git 제외). |

## 테스트 설계

- 세트 A와 B가 각 30문항(실물 15, AI 15)이다. 참가자 절반은 A를 먼저, 나머지 절반은 B를 먼저 푼다.
- 연습 3문항(채점 안 함), 문항당 5초, 4점 척도(확실히 실물, 아마 실물, 아마 AI, 확실히 AI), 확대 불가.
- 1차를 마치고 12시간이 지나야 2차가 열린다. 세션이 끝날 때마다 그 세션 점수를 보여 주고, 2차 후에는 합계도 보여 준다. 풀고 있는 세션의 점수는 새로고침해도 보이지 않는다(문항마다 새로고침해 정답을 알아내는 것을 막기 위해).
- 자동 수집 항목: 응답, 응답 시간, 시간 초과, 표시된 이미지 크기, 화면 크기와 픽셀 밀도, 터치 기기 여부, 풀이 중 탭 이탈과 포커스 이탈.
- 풀다가 새로고침하거나 창을 닫으면 같은 링크로 이어서 푼다. 중단 직전에 보던 문항은 다시 보여 주지 않고 `abandoned`로 기록한다.

## 1. 사진 준비

본 문항은 실물 30장, AI 30장이고 연습 문항이 3장이다. 점검 후 교체할 여분까지 실물 35장, AI 35장, 연습용 실물 2장과 AI 2장 정도를 준비한다.

```
raw/
  real/food/      real/street/     ...   카테고리 폴더 이름은 자유
  ai/food/        ai/street/       ...   실물과 같은 카테고리 이름을 쓴다
  practice/real/  practice/ai/
```

촬영할 때는 다음을 지킨다.

- 폰 3~4대로 나눠 찍는다. 한 기종으로만 찍으면 그 폰 특유의 색감이 실물의 특징이 된다.
- 기본 카메라 앱으로 필터와 보정 없이 찍는다. HEIC 파일도 그대로 넣으면 된다.
- 정사각형으로 잘라 쓰므로 피사체를 화면 가운데에 둔다. 세로 사진은 위아래가 잘린다.
- 인물은 초상권 동의를 받은 경우에만 넣는다.

AI 이미지는 이렇게 만든다.

- 실물과 같은 카테고리로, 폰으로 대충 찍은 듯한 분위기에 맞춘다. 다만 실물 사진의 장면을 그대로 복제하지는 않는다. 모든 참가자가 모든 문항을 보기 때문에 비슷한 두 장이 나오면 비교해서 추론할 수 있다.
- 실물 사진을 넣어 변형하는 방식(image-to-image)은 쓰지 않는다.
- 2~3개 모델로 나눠 만든다.
- 세로 3:4로 생성한다. 실물처럼 가장자리가 잘린 구도가 되고, Gemini 앱이 오른쪽 아래에 넣는 워터마크도 정사각형으로 자를 때 함께 잘려 나간다. 정사각형으로 생성하면 워터마크가 남으므로 `normalize`가 경고한다.
- 워터마크를 편집 도구로 지우지 않는다. AI 이미지에만 편집 흔적이 남아 새 단서가 된다.
- 짧은 변이 768px 이상이면 된다(Gemini 기본 출력 896×1200은 충분하다). 작으면 확대 흔적이 단서가 된다.
- 다른 모델이 워터마크를 다른 위치에 넣는다면 그 이미지는 쓰지 않는다. 파이프라인은 메타데이터만 지우고 픽셀에 박힌 표시는 지우지 못한다.

## 2. 파이프라인

```
uv run python -m pipeline.normalize                                   # raw/ → private/normalized/
uv run python -m pipeline.suggest_exclusions --write private/exclude.txt  # 여분 중 뺄 이미지 제안
uv run python -m pipeline.check_shortcuts                             # 지름길 점검 → private/check/
uv run python -m pipeline.build_sets                                  # web/img, web/items.json, private/seed_items.sql
```

- `normalize`는 회전 보정, sRGB 변환, 정사각형 크롭, 768px 리사이즈, 메타데이터 제거를 한다. 크롭은 가운데보다 살짝 위(가로 사진은 왼쪽)를 기준으로 해서 오른쪽 아래를 조금 더 잘라낸다. 실물과 AI에 똑같이 적용된다.
- `suggest_exclusions`는 카테고리마다 실물과 AI를 6장씩만 남기고(`--keep-per-category`), 나머지 여분 중 어떤 것을 빼야 두 그룹의 밝기, 대비, 색 차이가 가장 줄어드는지 찾아 `exclude.txt`에 원본 경로로 적는다. 기존 `exclude.txt`는 덮어쓰지 않는다.
- `exclude.txt`에는 정규화 key(`main-ai-food-0012`) 대신 원본 경로(`raw/ai/food/x.png`)를 적는 편이 안전하다. key는 사진을 추가하면 번호가 밀린다.
- `check_shortcuts`는 밝기, 대비, 채도, 선명도, 노이즈, 파일 용량을 실물과 AI 사이에서 비교한다. "차이 있음"이 나오면 제안된 이미지의 key를 `private/exclude.txt`에 한 줄씩 적고 다시 돌린다. `private/check/contact_real.jpg`와 `contact_ai.jpg`를 나란히 놓고 눈으로도 비교한다.
- `build_sets`는 세트와 절반을 배정하고 이미지를 무작위 이름으로 저장한 뒤, 정답이 새어 나갈 단서가 없는지 자동으로 점검한다.

`web/img`와 `web/items.json`은 빌드 결과라 git에 올리지 않는다(공개 저장소에 실제 사진이 올라가지 않게). 사진이 없을 때 흐름을 점검하려면 가짜 이미지로 먼저 만든다.

```
uv run python -m pipeline.make_placeholders
uv run python -m pipeline.normalize --raw private/placeholder_raw
uv run python -m pipeline.build_sets
```

실제 사진으로 `build_sets`를 다시 돌리면 가짜 이미지가 교체된다.

`build_sets`를 돌릴 때마다 문항 id가 새로 만들어진다. 그래서 다시 돌린 뒤에는 반드시 `private/seed_items.sql`을 Supabase에서 다시 실행하고 `web` 폴더도 다시 배포해야 한다. 응답 기록이 남아 있으면 `supabase/reset_test_data.sql`을 먼저 실행한다. 실제 사진으로 한 번 만든 뒤에는 실수로 덮어쓰지 않도록 `--force`를 붙여야만 다시 만들어진다. 파일럿이 시작된 뒤에는 문항을 바꾸지 않는다.

## 3. 로컬에서 미리 해 보기

```
python3 -m http.server 8000 -d web
```

브라우저에서 `http://localhost:8000/?mock=1&reset=1`을 연다. 서버 없이 전체 흐름을 볼 수 있다. 모의 모드에서는 2차가 바로 열리고 점수는 계산하지 않는다. 주소 끝에 `&gap=60`을 붙이면 1차 후 60초짜리 대기 화면을 볼 수 있다.

## 4. Supabase 설정

1. supabase.com에서 새 프로젝트를 만든다. 리전은 Seoul을 고른다.
2. SQL Editor에서 `supabase/schema.sql` 전체를 실행한다. 다시 실행해도 안전하다.
3. SQL Editor에서 `private/seed_items.sql`을 실행한다.
4. Project Settings의 API 메뉴에서 Project URL과 anon(또는 publishable) key를 복사해 `web/config.js`에 넣는다. 이 키는 공개되어도 되는 키다. 정답이 든 테이블은 이 키로 읽을 수 없게 막혀 있다.

무료 플랜은 일주일 동안 요청이 없으면 프로젝트가 일시 정지된다. 링크를 보내기 직전에 대시보드에서 프로젝트가 켜져 있는지 확인한다.

## 5. 배포

Netlify에 로그인한 뒤 [Netlify Drop](https://app.netlify.com/drop)에 `web` 폴더를 끌어다 놓으면 주소가 나온다. 로그인하지 않고 올리면 1시간 안에 계정에 연결하지 않을 경우 사이트가 삭제되니 반드시 로그인부터 한다. 다른 정적 호스팅(Vercel, Cloudflare Pages 등)도 된다. 이미지나 `config.js`를 바꿀 때마다 `web` 폴더를 다시 올린다.

## 6. 참가자 링크

```
uv run python -m pipeline.participants --n 20 --base-url https://배포된-주소/
```

1. SQL Editor에서 `private/seed_participants.sql`을 실행한다.
2. `private/participants.csv`의 name 칸에 누구에게 보낼 링크인지 적는다.
3. 본인이 쓸 링크도 하나 정하고 exclude 칸에 1을 적는다. 문항을 아는 사람의 응답은 분석에서 빠진다.
4. 링크가 더 필요하면 `--add`를 붙여 추가한다. 이미 보낸 링크는 바뀌지 않는다.

## 7. 시작 전 점검

```
uv run python -m pipeline.smoke_test --token 본인토큰
```

서버 함수 호출, 정답 테이블 차단, 토큰 등록을 확인한다. 이어서 본인 링크로 폰에서 1차를 끝까지 풀어 본다. 핀치 확대와 길게 눌러 저장하기가 막히는지도 확인한다. 2차까지 바로 확인하려면 SQL Editor에서 `update settings set gap_hours = 0;`으로 간격을 잠시 없앴다가 `12`로 되돌린다.

점검이 끝나면 `supabase/reset_test_data.sql`을 실행해 테스트 기록을 지우고 링크를 보낸다.

## 8. 진행 중

- `supabase/progress.sql`로 누가 1차와 2차를 마쳤는지 확인하고, 2차가 열린 사람에게 알림을 보낸다.
- 제한 시간 같은 화면 설정은 `web/config.js`에 있다. 파일럿 도중에는 바꾸지 않는다. 참가자마다 조건이 달라진다.

## 9. 분석

1. SQL Editor에서 `supabase/export.sql`을 실행하고 결과를 CSV로 내려받아 `private/export.csv`로 저장한다.
2. `uv run python -m analysis.analyze`를 실행한다. 결과는 화면과 `private/report.md`에 나온다.

보고서의 지표는 다음과 같다.

- 재검사 r: 1차 정답률과 2차 정답률의 상관. 핵심 지표다.
- 재검사 AUC: 확신도까지 반영한 판별력의 상관.
- 세션별 반분 r과 60문항 전체 반분 r: Spearman-Brown으로 보정한 값을 함께 보여 준다.
- 순서 효과(2차 − 1차)와 세트 난이도 차이, 기기별 정답률, 문항별 정답률과 갈림 정도.

유효 응답은 5초 안에 고르고 탭 이탈이나 포커스 이탈이 없는 응답이다. 완료한 세션 중 유효 응답이 20개 이상인 세션만 신뢰도 계산에 들어간다. 20명 규모에서는 신뢰구간이 넓으므로 r이 0 근처인지, 0.8 이상인지 같은 극단값만 해석한다.

## 개발용 테스트

브라우저 테스트는 `web/items.json`이 있어야 하므로 위의 가짜 이미지 빌드를 먼저 해 둔다.

```
uv run pytest                   # 파이프라인과 분석
cd tests && npm test            # DB 스키마(PGlite로 실제 Postgres 실행)와 앱 로직
cd tests && npm run test:e2e    # 실제 브라우저로 모의 모드 전체 흐름
```

이 WSL에는 Chromium이 쓰는 시스템 라이브러리가 없어서 `tests/.libs`에 따로 받아 둔 것을 쓴다.
