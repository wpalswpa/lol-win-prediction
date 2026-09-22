"""test_app.py — 배포 전 3분 (4팀). 실행: python -m pytest -q test_app.py  (서버는 꺼 둔다)

  test_health                 → 모델 없이 뜬 서버
  test_predict_normal         → 팀 데모(«블루가 크게 우세» 94.6%)와 같은 확률 (서빙 파리티)
  test_predict_missing_field  → 피처가 빠지면 422 (500 이 아니다)
  test_predict_close_game     → 접전은 판단보류
  test_batch_count_and_order  → 개수와 순서 (우세 → 열세 순으로 확률이 내려가야)
"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import app

REQ = json.loads(Path("examples/request.json").read_text(encoding="utf-8"))
BATCH = json.loads(Path("examples/request_batch.json").read_text(encoding="utf-8"))


def test_health():
    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"


def test_predict_normal():
    with TestClient(app) as c:
        r = c.post("/predict", json=REQ)
        assert r.status_code == 200
        d = r.json()
        assert d["label"] == "블루 승리 예측" and abs(d["win_prob_blue"] - 0.946) < 0.001     # 팀 데모 값
        assert d["top_factors"][0]["feature"] == "GoldDiff"                                # 1위 요인은 골드
        assert d["anomaly"]["is_anomaly"] is False


def test_predict_missing_field():
    bad = {k: v for k, v in REQ.items() if k != "GoldDiff"}
    with TestClient(app) as c:
        assert c.post("/predict", json=bad).status_code == 422


def test_predict_close_game():
    with TestClient(app) as c:
        r = c.post("/predict", json=BATCH["items"][2])                                     # 팽팽한 접전
        assert r.status_code == 200 and r.json()["label"] == "판단보류"


def test_batch_count_and_order():
    with TestClient(app) as c:
        r = c.post("/predict/batch", json=BATCH)
        assert r.status_code == 200
        d = r.json()
        assert d["count"] == 3
        probs = [x["win_prob_blue"] for x in d["results"]]
        assert probs[0] > probs[2] > probs[1]           # 우세 > 접전 > 열세


# ── 4팀 추가 계약 (routes_team.py · 웹 계약 정렬) ─────────────────────
GOLDEN = Path(__file__).resolve().parent.parent / "tests" / "golden_predictions.json"


def test_top_factors_five():
    """웹 화면·docs/serving.md 계약은 요인 5개 — 3개면 웹 파리티가 0/53 이 된다 (2026-09-22 실측)."""
    with TestClient(app) as c:
        f = c.post("/predict", json=REQ).json()["top_factors"]
        assert len(f) == 5
        mags = [abs(x["contribution"]) for x in f]
        assert mags == sorted(mags, reverse=True)


def test_golden_parity():
    """골든 50건 — API 확률·pred·요인 5개 기여도가 tests/golden_predictions.json 과 완전히 같다."""
    if not GOLDEN.exists():
        return
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
    with TestClient(app) as c:
        r = c.post("/predict/batch", json={"items": [x["input"] for x in cases[:32]]})
        assert r.status_code == 200
        outs = r.json()["results"] + c.post("/predict/batch", json={"items": [x["input"] for x in cases[32:]]}).json()["results"]
        for case, got in zip(cases, outs):
            exp = case["expected"]
            assert got["win_prob_blue"] == exp["win_prob_blue"] and got["pred"] == exp["pred"]
            assert [(f["feature"], f["contribution"]) for f in got["top_factors"]] == \
                   [(f["feature"], f["contribution"]) for f in exp["top_factors"]]


def test_schema_and_examples():
    with TestClient(app) as c:
        s = c.get("/schema").json()
        assert len(s["features"]) == 13 and "GoldDiff" in s["features"]
        ex = c.get("/examples").json()
        assert [e["id"] for e in ex] == ["close", "blue", "red"]
        assert c.post("/predict", json=ex[1]["payload"]).json()["win_prob_blue"] == c.post("/predict", json=REQ).json()["win_prob_blue"]


def test_coach():
    with TestClient(app) as c:
        r = c.post("/coach", json={**BATCH["items"][2], "verdict": "열세패"})
        assert r.status_code == 200
        d = r.json()
        assert len(d["actions"]) == 3 and d["actions"][0]["gain"] >= d["actions"][-1]["gain"]
        assert d["verdict_advice"]["verdict"] == "열세패" and d["how_to_read"]
        assert c.post("/coach", json={"GoldDiff": 1}).status_code == 422


def test_forwarded_prefix_docs():
    """역프록시 뒤(/model/*)에서도 Swagger 가 openapi.json 을 프리픽스 붙여 찾는다."""
    with TestClient(app) as c:
        html = c.get("/docs", headers={"X-Forwarded-Prefix": "/model"}).text
        assert "'/model/openapi.json'" in html
        assert "'/openapi.json'" in c.get("/docs").text
