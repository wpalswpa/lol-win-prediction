# API 스모크 테스트 — 프런트(9504)를 통해 모든 엔드포인트가 살아 있고 형태가 맞는지 확인
# 실행: ./check_project.sh test   또는  python web/test_api.py
#
# 예측·코칭은 백엔드가 모델 API(9544)에 넘기므로 모델 API 도 떠 있어야 한다 (./check_api.sh start).
# 모델 API 의 입력 형식 한계(models/schemas.py, 예: GoldDiff ±30000)를 넘는 값은 400 이고,
# 학습 범위(schema.json train_min~max)만 넘는 값은 200 + warnings 다 — 둘을 구분해 검사한다.
import io
import json
import os
import sys
import urllib.request
import zipfile

PORT = int(os.environ.get("FRONTEND_PORT", 9504))
BASE = os.environ.get("BASE_URL", f"http://127.0.0.1:{PORT}")


def get(path, data=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def raw(path):
    """본문을 그대로 (다운로드 파일용). (status, content_type, content_disposition, bytes)"""
    try:
        with urllib.request.urlopen(BASE + path, timeout=20) as r:
            return r.status, r.headers.get("Content-Type", ""), r.headers.get("Content-Disposition", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), "", e.read()


def main():
    fails = 0
    def check(name, cond, extra=""):
        nonlocal fails
        print(f"[{'통과' if cond else '실패'}] {name} {extra}")
        fails += 0 if cond else 1
    st, h = get("/healthz");            check("GET /healthz", st == 200 and h["backend_ok"], f"→ backend_ok={h.get('backend_ok')}")
    st, h = get("/api/health");         check("GET /api/health", st == 200 and h["status"] == "ok" and h["parity"]["passed"], f"→ 모델 {h['model']['name']} v{h['model']['version']} 홀드아웃 {h['model']['holdout_accuracy']}")
    check("GET /api/health 모델 API 연결", h.get("model_api", {}).get("ok") is True, f"→ {h.get('model_api', {}).get('url')} {h.get('model_api', {}).get('model_version') or h.get('model_api', {}).get('error')}")
    st, s = get("/api/schema");         check("GET /api/schema", st == 200 and len(s["features"]) == 13, f"→ 피처 {len(s.get('features', {}))}개")
    st, ex = get("/api/examples");      check("GET /api/examples", st == 200 and len(ex) == 3)
    st, p = get("/api/predict", ex[1]["payload"]); check("POST /api/predict (블루 우세)", st == 200 and p["pred"] == 1 and len(p["top_factors"]) == 5, f"→ {p.get('win_prob_blue')}")
    check("POST /api/predict 응답 계약 (pred_label·meta 유지 + 모델 API 의 label·anomaly·model_version)",
          p.get("pred_label") == "블루 승리 예측" and "meta" in p and p.get("label") and "anomaly" in p and p.get("model_version"),
          f"→ label={p.get('label')} model_version={p.get('model_version')}")
    st, b = get("/api/predict/batch", [e["payload"] for e in ex]); check("POST /api/predict/batch", st == 200 and len(b) == 3 and b[1]["win_prob_blue"] == p["win_prob_blue"])
    st, e = get("/api/predict", {"GoldDiff": 100}); check("POST /api/predict 누락 피처 → 400", st == 400 and "빠진" in e.get("error", ""))
    hi = s["features"]["GoldDiff"]["train_max"]
    st, w = get("/api/predict", {**ex[0]["payload"], "GoldDiff": hi + 5000}); check("POST /api/predict 학습 범위 밖 → 200+경고", st == 200 and any("GoldDiff" in x for x in w.get("warnings", [])), f"→ GoldDiff={hi + 5000}")
    st, e = get("/api/predict", {**ex[0]["payload"], "GoldDiff": 99999}); check("POST /api/predict 형식 한계 밖(±30000) → 400", st == 400 and "GoldDiff" in e.get("error", ""), f"→ {e.get('error')}")
    st, e = get("/api/predict/batch", [ex[0]["payload"], {k: v for k, v in ex[0]["payload"].items() if k != "GoldDiff"}]); check("POST /api/predict/batch 2번째 누락 → 400+index", st == 400 and e.get("index") == 1 and "빠진" in e.get("error", ""))
    st, c = get("/api/coach", ex[1]["payload"]); check("POST /api/coach", st == 200 and c.get("actions") and all(a["gain"] > 0 for a in c["actions"]) and c.get("how_to_read"), f"→ 조언 {len(c.get('actions', []))}건")
    st, c = get("/api/coach", {**ex[1]["payload"], "verdict": "역전패"}); check("POST /api/coach verdict → verdict_advice", st == 200 and (c.get("verdict_advice") or {}).get("headline"))
    st, r = get("/api/report");         check("GET /api/report", st == 200 and r["errors"]["bins"] and r["win_factors"], f"→ 최약 구간 {r['errors'].get('weakest_bin')}")
    st, m = get("/api/match-types");    check("GET /api/match-types", st == 200 and m["k"] == 4, "→ " + " · ".join(f"{t['label']} {t['share_pct']}%" for t in m.get("types", [])))
    st, _ = get("/api/nope");           check("GET /api/nope → 404", st == 404)
    # 모델 API 연계 키트 — 화면 «API 연계» 탭의 다운로드. 외부 개발자가 받아서 바로 쓸 수 있어야 한다
    st, ct, cd, body = raw("/model-api-kit/lol-model-api-kit.zip")
    names = sorted(zipfile.ZipFile(io.BytesIO(body)).namelist()) if st == 200 and ct.startswith("application/zip") else []
    need = ["README.txt", "api_guide.md", "lol_model_api_client.py", "openapi.json", "request.json", "request_batch.json"]
    check("GET /model-api-kit/lol-model-api-kit.zip", st == 200 and 'filename="lol-model-api-kit.zip"' in cd and names == need, f"→ {len(names)}개 파일")
    st, ct, cd, body = raw("/model-api-kit/api_guide.md")
    check("GET /model-api-kit/api_guide.md", st == 200 and "attachment" in cd and b"/model-api" in body and b"POST /predict" in body)
    st, ct, cd, body = raw("/model-api-kit/lol_model_api_client.py")
    check("GET /model-api-kit/lol_model_api_client.py", st == 200 and b"def predict(" in body and b"/model-api" in body)
    st, ct, cd, body = raw("/model-api-kit/openapi.json")
    paths = json.loads(body).get("paths", {}) if st == 200 else {}
    check("GET /model-api-kit/openapi.json", st == 200 and "/predict" in paths and "/coach" in paths, f"→ 경로 {len(paths)}개")
    st, _, _, _ = raw("/model-api-kit/nope.txt"); check("GET /model-api-kit/nope.txt → 404", st == 404)
    st, h = get("/model-api/health");   check("GET /model-api/health (모델 API 중계)", st == 200 and h.get("status") == "ok", f"→ {h.get('model_version')}")
    print("\n" + ("전부 통과" if not fails else f"{fails}건 실패"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
