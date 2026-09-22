---
title: "팀 모델 API 를 pN.sumzip.com/model-api 로 공개하기 — 학생 절차 (vite 프록시 방식)"
description: "각 팀이 project2608 웹 프로젝트 안에서 독립 구동하는 FastAPI 모델 API(uvicorn · 가상환경)를 자기 팀 도메인 pN.sumzip.com 아래 /model-api 경로로 공개하는 학생용 절차. nginx 를 건드리지 않고 frontend/vite.config.ts 의 proxy 항목 하나와 uvicorn 의 --root-path 로 끝낸다. 팀별 포트 규약(954N)과 Swagger(/model-api/docs)·OpenAPI·health·predict 의 정확한 팀별 주소, 확인 명령, 막힐 때 세 증상, 안전 규칙을 담았다."
dikw_level: "data"
target: []
tags:
  - "model-serving"
  - "vite-proxy"
  - "uvicorn"
  - "fastapi"
  - "swagger"
  - "학생절차"
created: "2026-09-22"
modified: "2026-09-22"
author: "AI Assistant"
---

# 팀 모델 API 를 `pN.sumzip.com/model-api` 로 공개하기 — 학생 절차

> 팀 웹(`https://pN.sumzip.com`)은 이미 밖에서 열린다. 같은 도메인 아래 **`/model-api`** 경로로 팀 모델 API 를 붙인다.
> 바꾸는 파일은 **둘** — `frontend/vite.config.ts` 한 항목, uvicorn 기동 명령 한 옵션. 서버 관리자에게 요청할 것은 없다.

## 0. 완성되면 이렇게 열린다 (팀별 주소)

| 팀 | 모델 API 포트 | Swagger UI | OpenAPI 문서 | 상태 확인 | 예측 |
|---|---|---|---|---|---|
| 1팀 | 9541 | https://p1.sumzip.com/model-api/docs | https://p1.sumzip.com/model-api/openapi.json | https://p1.sumzip.com/model-api/health | `POST https://p1.sumzip.com/model-api/predict` |
| 2팀 | 9542 | https://p2.sumzip.com/model-api/docs | https://p2.sumzip.com/model-api/openapi.json | https://p2.sumzip.com/model-api/health | `POST https://p2.sumzip.com/model-api/predict` |
| 3팀 | 9543 | https://p3.sumzip.com/model-api/docs | https://p3.sumzip.com/model-api/openapi.json | https://p3.sumzip.com/model-api/health | `POST https://p3.sumzip.com/model-api/predict` |
| 4팀 | 9544 | https://p4.sumzip.com/model-api/docs | https://p4.sumzip.com/model-api/openapi.json | https://p4.sumzip.com/model-api/health | `POST https://p4.sumzip.com/model-api/predict` |

규칙은 하나다 — **N팀 = `pN.sumzip.com` · 포트 `954N` · 경로 `/model-api`**. 5팀 이후도 같다(5팀 `p5.sumzip.com` · 9545 …). ReDoc 은 `/model-api/redoc`.

`/api` 는 쓰지 않는다 — 팀 웹 백엔드가 이미 그 경로를 쓴다. 모델 API 는 언제나 `/model-api` 뒤에 있다.

## 1. 원리 — 두 줄이면 되는 이유

```
브라우저  https://p1.sumzip.com/model-api/predict
   │  (서버 관리자의 nginx 가 pN.sumzip.com/* 전부를 팀 vite 로 넘긴다 — 손댈 것 없음)
   ▼
팀 vite (9501)   proxy '/model-api' → 접두를 떼고 → http://127.0.0.1:9541/predict
   ▼
팀 uvicorn (9541)   --root-path /model-api   ← «나는 /model-api 뒤에 있다» 를 알려 준다
```

- vite 가 **접두를 뗀다** — 그래서 FastAPI 코드의 경로(`/predict` `/health`)는 그대로다.
- uvicorn 이 **접두를 안다** — 그래서 Swagger(`/model-api/docs`)가 `openapi.json` 을 제자리에서 찾는다. 이 옵션이 빠지면 `/docs` 는 열려도 «Failed to load API definition» 이 뜬다.

## 2. 절차 (약 10분)

### 2-1. 모델 API 를 규약대로 띄운다

모델 API 폴더(예: `~/project2608/models/modelapi/`)에서, 자기 팀 포트로:

```bash
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 9541 --root-path /model-api
```

- `--host 127.0.0.1` — 포트를 밖에 열지 않는다. 같은 기계의 vite 만 부른다. (`0.0.0.0` 으로 띄우면 내부망 누구나 포트로 직접 닿는다.)
- `--port 954N` — 자기 팀 번호. 이미 다른 포트로 떠 있으면 그 프로세스를 끝내고 다시 띄운다.
- `--root-path /model-api` — 코드에서 `FastAPI(root_path=...)` 를 이미 주고 있다면 둘 중 하나만 둔다(둘 다 주면 `/model-api/model-api` 가 된다).

바로 확인:

```bash
curl -s localhost:9541/health          # 200 이어야 다음으로
```

### 2-2. `frontend/vite.config.ts` 에 `/model-api` 항목을 더한다

`server` 와 `preview` **양쪽** 에 같은 `proxy` 를 둔다 — `vite`(개발)와 `vite preview`(배포) 어느 쪽으로 띄워도 규칙이 타야 한다.

```ts
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';          // 팀 프레임워크에 맞게

const FRONT = 9501;                            // 자기 팀 웹 포트 (950N)
const BACK = 9521;                             // 자기 팀 웹 백엔드 포트 (이미 쓰던 값)
const MODEL_API = 9541;                        // 자기 팀 모델 API 포트 (954N)

const proxy = {
  '/api': { target: `http://127.0.0.1:${BACK}`, changeOrigin: true },        // 이미 있던 줄 — 그대로 둔다
  '/model-api': {
    target: `http://127.0.0.1:${MODEL_API}`,
    changeOrigin: true,
    rewrite: (p: string) => p.replace(/^\/model-api(?=\/|$)/, '') || '/',   // /model-api/predict → /predict
  },
};

export default defineConfig({
  plugins: [vue()],
  server:  { host: '0.0.0.0', port: FRONT, strictPort: true, allowedHosts: ['p1.sumzip.com'], proxy },
  preview: { host: '0.0.0.0', port: FRONT, strictPort: true, allowedHosts: ['p1.sumzip.com'], proxy },
});
```

- 포트 세 개는 **숫자를 파일에 직접 적는다.** 환경변수로 두면 vite 를 띄우는 셸에 그 값이 없을 때 조용히 기본값으로 붙어 404 가 난다(원인을 찾기 가장 어려운 실패).
- `allowedHosts` 에 자기 도메인이 있어야 한다. 없으면 vite 가 «Blocked request» 를 낸다.
- 기존 `/api` 항목은 건드리지 않는다.

### 2-3. vite 를 다시 띄운다

설정 파일은 재기동해야 읽힌다. 자기 계정의 vite 프로세스를 끝내고 같은 명령으로 다시 띄운다.

```bash
# 예 — 팀이 쓰던 명령 그대로
npm run dev            # 또는  npm run build && npm run preview
```

이 순간 팀 웹이 몇 초 끊긴다. 수업·시연 중에는 하지 않는다.

### 2-4. 확인 — 네 줄이 전부 맞아야 완료

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://p1.sumzip.com/model-api/health         # 200
curl -s -o /dev/null -w '%{http_code}\n' https://p1.sumzip.com/model-api/openapi.json   # 200
curl -s -o /dev/null -w '%{http_code}\n' https://p1.sumzip.com/api/health               # 웹 백엔드가 그대로인가 (팀마다 경로 다름)
curl -s -X POST https://p1.sumzip.com/model-api/predict -H 'Content-Type: application/json' -d @examples/request.json
```

그리고 브라우저에서 **https://p1.sumzip.com/model-api/docs** 를 열어 `POST /predict` 의 Try it out 이 200 을 돌려주면 끝이다. Swagger 가 뜨는데 Try it out 이 404 면 `--root-path` 가 빠진 것이다.

## 3. 프론트에서 부르는 법

```ts
const r = await fetch('/model-api/predict', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
});
```

**절대 주소·포트를 쓰지 않는다.** `'/model-api/…'` 처럼 경로만 쓰면 같은 도메인이라 CORS 설정이 필요 없고, 로컬(`localhost:9501`)과 배포(`p1.sumzip.com`)에서 같은 코드가 돈다.

## 4. 막힐 때

| 증상 | 원인 | 처방 |
|---|---|---|
| `/model-api/health` 가 404 | vite 를 재기동하지 않았거나, proxy 의 target 포트가 uvicorn 포트와 다르다 | `lsof -nP -iTCP:954N -sTCP:LISTEN` 으로 실제 포트를 보고 vite.config 의 숫자와 맞춘 뒤 재기동 |
| `/model-api/docs` 는 뜨는데 «Failed to load API definition» 또는 Try it out 404 | `--root-path /model-api` 가 빠졌다 (또는 코드의 `root_path` 와 이중) | 2-1 명령으로 다시 띄운다 |
| 502 Bad Gateway | uvicorn 이 죽었거나 `--host` 가 달라 vite 가 못 붙는다 | `curl localhost:954N/health` 부터. 안 되면 uvicorn 로그를 본다 |
| «Blocked request. This host is not allowed» | vite `allowedHosts` 에 자기 도메인이 없다 | `server`·`preview` 둘 다에 `'pN.sumzip.com'` |
| 웹 `/api/...` 가 갑자기 404 | `/api` 항목을 지웠거나 순서를 바꿨다 | 2-2 의 `/api` 줄을 원래대로 |
| `/model-api/model-api/...` 로 붙는다 | `--root-path` 와 코드 `root_path` 를 둘 다 줬다 | 하나만 남긴다 |

## 5. 안전 규칙

- uvicorn 은 **`--host 127.0.0.1`** — 프록시 뒤에만 둔다.
- 경로는 **`/model-api` 하나** — `/docs` `/openapi.json` `/redoc` 도 그 아래로만 열린다.
- **`share=True`·ngrok 같은 외부 터널 금지** — 도메인 밖으로 새는 통로를 만들지 않는다.
- 요청·응답·로그에 **키·비밀값·개인정보 원문을 넣지 않는다.** `.env` 는 제출물에 없다.
- 스키마의 `extra="forbid"`·값 범위·`ValueError → 422` 를 그대로 둔다 — 밖에서 오는 요청은 노트북보다 거칠다. 계약이 첫 방어선이다.
- 배치 요청은 `items` 개수 상한을 둔다(예: `max_length=100`). 무한히 큰 요청을 받지 않는다.
- 다른 팀의 포트(954M)를 부르지 않는다. 자기 팀 것만.
- 제출 뒤 Swagger 를 닫고 싶으면 `FastAPI(docs_url=None, redoc_url=None)` — API 는 그대로 돌고 문서만 사라진다.

## 6. 제출 확인표

| 항목 | 값 |
|---|---|
| 팀 도메인 | https://pN.sumzip.com |
| Swagger | https://pN.sumzip.com/model-api/docs → [ ] 열림 · Try it out 200 |
| health | https://pN.sumzip.com/model-api/health → [ ] 200 |
| predict | `POST /model-api/predict` 예시 요청 → [ ] 200 · 값이 노트북 대조값과 같다 |
| 웹 `/api` | [ ] 그대로 200 |
| uvicorn 명령 | `--host 127.0.0.1 --port 954N --root-path /model-api` → [ ] 확인 |
| vite.config.ts | `server`·`preview` 양쪽에 `/model-api` → [ ] 확인 |
