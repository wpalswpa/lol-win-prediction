<!-- ─────────────────────────────────────────────
  모델 API 서버(models/app.py · FastAPI) — 외부·내부 서비스가 HTTP 로 모델을 부르는 방법.
  왜 필요한가: 웹서비스 말고 다른 서비스도 같은 모델을 쓰게 하려면 파이썬 import 가 아니라 HTTP 계약이 필요하다.
  주로 보는 사람: 연계 개발자(외부 서비스) · 운영 담당 · Claude(검수)
  대화형 문서: http://<팀 서버>:9544/docs  (Swagger, 브라우저에서 바로 호출해 볼 수 있다)
  ───────────────────────────────────────────── -->

# 모델 API 연계 가이드 — 설치 · 엔드포인트 · 외부 연계

**모델 API** 는 LoL 10분 시점 경기 상태(블루−레드 차이 피처 13개)를 JSON 으로 받아
블루팀 승리 확률·승리요인·이상탐지·코칭을 돌려주는 독립 HTTP 서버다.
웹서비스(`web/`)와 별개 프로세스·별개 가상환경으로 돌고, 어느 언어에서든 HTTP 로 부를 수 있다.

| 항목 | 값 |
|---|---|
| 코드 | `models/app.py` (FastAPI) · 계약 `models/schemas.py` · 팀 엔드포인트 `models/routes_team.py` |
| 포트 | **9544** (프런트 9504 · 웹 백엔드 9524 와 별개) — `models/.env` 의 `PORT` |
| Swagger | `http://<호스트>:9544/docs` · OpenAPI 명세 `http://<호스트>:9544/openapi.json` |
| 공개 도메인 경유 | `https://p4.sumzip.com/model/docs` (프런트가 `/model/*` 를 9544 로 중계) |
| 운영 스크립트 | `./check_api.sh {setup|start|stop|restart|status|logs|health|test}` |
| 확률의 정본 | `lolwin.predict` — 웹 화면과 같은 함수를 부르므로 두 확률은 같다 |

---

## 1. 설치와 기동 (팀 서버 · 다른 기계 공통)

```bash
git clone https://github.com/wpalswpa/lol-win-prediction.git && cd lol-win-prediction
./check_api.sh setup     # models/.venv 생성 + models/requirements.txt 설치 + models/.env 복사 (처음 한 번)
./check_api.sh start     # 9544 기동 → health 200 이면 "시작됨"
./check_api.sh test      # 단위 테스트 10건 + HTTP 스모크 10건
./check_api.sh status    # 실행 중 · pid · health · 모델 버전
```

필요한 것: Python 3.11 이상 (모델을 만든 scikit-learn 1.9.0 이 3.11 에서 검증됨).
모델 파일(`models/model/artifacts/model.joblib` · 이상탐지 `models/model/anomaly_detect/model.joblib`)은 저장소에 들어 있어 따로 받을 것이 없다.
운영 웹서비스의 `venv311` 에는 FastAPI 를 넣지 않는다 — 두 서비스의 의존성을 섞지 않기 위해서다.

설정은 `models/.env` 로 바꾼다 (코드는 안 고친다).

| 키 | 기본값 | 뜻 |
|---|---|---|
| `PORT` · `HOST` | `9544` · `0.0.0.0` | 같은 망의 다른 서비스가 부를 수 있게 모든 인터페이스에 연다. 로컬만 열려면 `HOST=127.0.0.1` |
| `CLOSE_MARGIN` | `0.10` | 확률이 0.5±이 값 안이면 «판단보류» |
| `INCLUDE_ANOMALY` | `true` | 응답에 이상탐지 결과를 넣을지 |
| `CORS_ORIGINS` | `*` | 브라우저에서 직접 부르는 외부 서비스의 오리진. 쉼표로 여러 개. 비우면 CORS 헤더 없음 |
| `API_KEYS` | (비움) | 쉼표 구분 허용 키. 값이 있으면 POST 요청에 `X-API-Key` 헤더가 없을 때 401 |

서버 재부팅 뒤 자동으로 살아나지 않는다 — `./check_api.sh start`. 로그는 `logs/api.log`, pid 는 `run/api.pid`.

## 2. 엔드포인트

모든 요청·응답은 JSON 이다. 응답마다 `X-Request-ID` 헤더가 붙어 로그(`logs/api.log`)의 `req_id` 와 맞춰 볼 수 있다.

| 메서드 · 경로 | 무엇을 하나 | 입력 | 응답(200) |
|---|---|---|---|
| `GET /health` | 판단할 준비가 됐는지 | — | `status`(ok/loading) · `model_name` · `model_version` · `load_seconds` |
| `GET /schema` | 입력 피처 13개의 뜻·형·학습 범위 | — | `artifacts/schema.json` 원문 — 입력 폼을 만들 때 |
| `GET /examples` | 예시 입력 3건 | — | `[{id: close/blue/red, label, payload}]` |
| `POST /predict` | 한 경기를 판단 | 피처 13개 객체 | 아래 «응답 필드» |
| `POST /predict/batch` | 여러 경기를 한 번에 (1~32건) | `{"items": [피처 13개, ...]}` | `{"results": [...], "count": n}` — 입력 순서 유지 |
| `POST /coach` | 이 상태에서 무엇을 했다면 승률이 얼마나 올랐나 | 피처 13개 (+ 선택 `verdict`) | `win_prob` · `actions`(상승폭 큰 순 3개) · `how_to_read` · `verdict_advice` |
| `GET /metrics` | 운영 지표 다섯 | — | `requests_total` · `error_rate` · `latency_p95_ms` · `abstain_rate` · `label_distribution` |
| `GET /docs` · `GET /openapi.json` | Swagger UI · OpenAPI 3 명세 | — | HTML · JSON |

### 입력 — 피처 13개 (전부 블루 − 레드 차이, 양수 = 블루 우세)

| 피처 | 뜻 | 형 · 받는 범위 |
|---|---|---|
| `FirstBlood` | 첫 킬을 블루가 가졌나 | 0 또는 1 |
| `KillsDiff` | 킬 차이 | 정수 −50~50 |
| `GoldDiff` | 골드 차이 | 정수 −30000~30000 |
| `ExpDiff` | 경험치 차이 | 정수 −30000~30000 |
| `WardsPlacedDiff` | 와드 설치 차이 | 정수 −500~500 |
| `WardsDestroyedDiff` | 와드 제거 차이 | 정수 −100~100 |
| `AssistsDiff` | 어시스트 차이 | 정수 −100~100 |
| `DragonsDiff` | 드래곤 차이 | 정수 −1~1 |
| `HeraldsDiff` | 전령 차이 | 정수 −1~1 |
| `TowersDestroyedDiff` | 타워 파괴 차이 | 정수 −11~11 |
| `AvgLevelDiff` | 평균 레벨 차이 | 실수 −10~10 |
| `TotalMinionsKilledDiff` | 미니언(CS) 차이 | 정수 −500~500 |
| `TotalJungleMinionsKilledDiff` | 정글 몬스터 차이 | 정수 −300~300 |

13개가 전부 있어야 하고, 없는 키를 넣으면 거부한다(`extra=forbid`).
«받는 범위» 밖이면 422 로 거부하고, 범위 안이지만 **학습 범위**(`GET /schema` 의 `train_min`~`train_max`) 밖이면 200 으로 답하되 `warnings` 에 적는다 — 이례적인 경기를 아예 못 보게 하지 않기 위해서다.

### 응답 필드 — `POST /predict`

```json
{
  "label": "블루 승리 예측",
  "win_prob_blue": 0.9461,
  "pred": 1,
  "top_factors": [
    {"feature": "GoldDiff", "name": "골드(돈) 차이", "value": 4500.0, "contribution": 1.9541, "direction": "블루에 유리"}
  ],
  "anomaly": {"is_anomaly": false, "score": 0.0412, "percentile": 71.3, "label": "정상 범위", "unusual_features": []},
  "warnings": [],
  "model_version": "lolwin-1.0"
}
```

| 필드 | 보장 |
|---|---|
| `label` | `블루 승리 예측` · `레드 승리 예측` · `판단보류`(확률이 0.5±`CLOSE_MARGIN` 안인 접전). 보류여도 `win_prob_blue`·`pred` 는 그대로 온다 |
| `win_prob_blue` | 0~1, 소수 4자리 |
| `pred` | `win_prob_blue >= 0.5` 이면 1, 아니면 0 |
| `top_factors` | **5개**, 기여도 절대값 내림차순. `contribution` 은 다른 지표를 통제한 뒤 남은 몫이라 킬이 음수로 나올 수 있다 — «킬을 하면 진다»가 아니다 ([serving.md](serving.md) 3장) |
| `anomaly` | 학습 데이터에서 보기 드문 경기 상태인지. `percentile` 98 이상이면 `is_anomaly=true`. `INCLUDE_ANOMALY=false` 면 `null` |
| `warnings` | 문자열 목록. 비어 있으면 정상 |

### 오류 규약

| HTTP | 언제 | 본문 |
|---|---|---|
| 422 | 피처 누락 · 모르는 키 · 형이 다름 · 받는 범위 밖 · 배치 33건 이상 | `{"detail": [{"loc": ["body", "GoldDiff"], "msg": "Field required", "type": "missing"}, ...]}` |
| 401 | `API_KEYS` 를 켰는데 `X-API-Key` 가 없거나 틀림 | `{"detail": "..."}` |
| 503 | 모델 적재 중 (기동 직후 1~2초) | `{"detail": "모델을 준비 중입니다..."}` — 잠시 후 재시도 |
| 500 | 서버 내부 오류 | `{"detail": "서버에서 판단에 실패했습니다"}` — `X-Request-ID` 로 로그 추적 |

## 3. 외부 서비스에서 부르기

같은 망 안에서는 `http://<팀 서버 IP>:9544`, 바깥에서는 `https://p4.sumzip.com/model` 이 기본 주소다.
아래 예시는 `BASE` 만 바꾸면 그대로 돈다.

### curl

```bash
BASE=http://127.0.0.1:9544
curl -s $BASE/health
curl -s -X POST $BASE/predict -H 'Content-Type: application/json' \
  -d '{"FirstBlood":1,"KillsDiff":5,"GoldDiff":4500,"ExpDiff":3000,"WardsPlacedDiff":5,"WardsDestroyedDiff":2,"AssistsDiff":6,"DragonsDiff":1,"HeraldsDiff":1,"TowersDestroyedDiff":1,"AvgLevelDiff":1.2,"TotalMinionsKilledDiff":30,"TotalJungleMinionsKilledDiff":10}'
curl -s -X POST $BASE/predict/batch -H 'Content-Type: application/json' -d @models/examples/request_batch.json
```

### Python (표준 라이브러리만)

```python
import json, urllib.request

BASE = "http://127.0.0.1:9544"

def predict(state: dict, api_key: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(f"{BASE}/predict", data=json.dumps(state).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:      # 422/401 은 HTTPError 로 온다
        return json.loads(r.read())

r = predict({"FirstBlood": 1, "KillsDiff": 5, "GoldDiff": 4500, "ExpDiff": 3000,
             "WardsPlacedDiff": 5, "WardsDestroyedDiff": 2, "AssistsDiff": 6,
             "DragonsDiff": 1, "HeraldsDiff": 1, "TowersDestroyedDiff": 1,
             "AvgLevelDiff": 1.2, "TotalMinionsKilledDiff": 30, "TotalJungleMinionsKilledDiff": 10})
print(r["label"], r["win_prob_blue"], r["top_factors"][0]["name"])
```

### JavaScript (브라우저 · Node)

```js
const BASE = "https://p4.sumzip.com/model";   // 브라우저에서 직접 부를 때는 서버의 CORS_ORIGINS 에 내 오리진이 있어야 한다
const state = await (await fetch(`${BASE}/examples`)).json();
const r = await fetch(`${BASE}/predict`, {
  method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify(state[1].payload),
});
if (r.status === 422) console.error(await r.json());   // 입력 문제 — detail 에 어느 필드인지 있다
else { const d = await r.json(); console.log(d.label, d.win_prob_blue); }
```

### 연계 규칙 (외부 개발자가 지킬 것)

1. `GET /health` 가 `ok` 일 때만 판단 요청을 보낸다. `loading` 이면 잠시 후 재시도.
2. 화면에 `win_prob_blue` 를 띄울 때 `label` 이 `판단보류` 면 «접전»이라고 함께 알린다. 확률만 보여주면 63% 가 확신처럼 읽힌다.
3. `warnings` 가 비어 있지 않으면 사용자에게 그대로 보여준다 — 믿을지 말지는 쓰는 쪽이 정한다.
4. `top_factors[].contribution` 을 «이 지표를 올리면 이긴다»로 옮기지 않는다. 처방이 필요하면 `POST /coach` 를 쓴다.
5. 배당·베팅 산출에 쓰지 않는다 ([../model_card.md](../model_card.md) 금지 항목).
6. 여러 건은 `/predict/batch` 32건씩. 시험셋 1,976판 기준 건당 약 10 ms 다.

## 4. 웹서비스와의 관계 · 운영

```
브라우저 ─ https://p4.sumzip.com ─▶ web/frontend.py (9504) ─┬─ /api/*   ─▶ web/app.py (9524) ─ lolwin.predict (함수 호출, 현재)
                                                            └─ /model/* ─▶ models/app.py (9544) ─ lolwin.predict + anomaly.detect
외부·내부 다른 서비스 ────────────────────────────────────────────────────▶ models/app.py (9544)
```

- 프런트의 `/model/*` 중계는 `X-Forwarded-Prefix: /model` 을 붙여 보내므로 Swagger 가 `/model/openapi.json` 을 올바로 찾는다.
- 웹 백엔드가 함수 호출 대신 이 API 를 부르도록 바꾸는 절차는 「API 서비스 전환 계획서」 3~5단계다. 그 전까지 두 경로의 확률은 같은 함수라 같다 — `models/test_app.py::test_golden_parity` 가 골든 50건으로 검사한다.
- 모델을 재학습하면 `models/model/artifacts/` 와 루트 `artifacts/` 를 **같이** 바꾸고, `MODEL_VERSION` 을 올린다. 사본이 어긋나면 웹과 API 가 다른 답을 낸다.
- 지연 실측(2026-09-22, 팀 서버 맥): 단건 p50 10.9 ms · p95 11.7 ms, 동시 10 에서 p95 130 ms, 오류 0.

## 5. 검증 — 무엇을 고치든 이것이 통과해야 한다

```bash
./check_api.sh test          # 단위 10건(계약·골든 50건 파리티·코치·프리픽스) + 서버가 떠 있으면 HTTP 스모크 10건
./check_project.sh test      # 웹서비스 쪽은 그대로 통과해야 한다 (API 는 웹을 건드리지 않는다)
```
