"""이상탐지 API — 독립 실행 서버 (기존 web/app.py 를 건드리지 않고 바로 쓸 수 있다).

    python models/anomaly_detect/serve.py --port 9534

    GET  /api/health          모델 버전·임계값·로드 상태
    GET  /api/schema          입력 피처 13개와 학습 범위·울타리 (입력 폼을 만들 때 참고)
    GET  /api/examples        예시 입력 3건 (버튼용)
    POST /api/detect          경기 1건 → 이상 여부
    POST /api/detect/batch    경기 1~1000건 배열 → 결과 배열

기존 백엔드(web/app.py)에 합치는 방법은 MANUAL.md 3장.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from flask import Flask, jsonify, request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from detect import EXAMPLES, detect, load_model, load_schema   # noqa: E402

app = Flask(__name__)
STARTED = datetime.now(timezone.utc).isoformat(timespec="seconds")


@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.route("/api/health")
def api_health():
    s = load_schema()
    return jsonify({"service": "anomaly_detect", "status": "ok", "started_at": STARTED,
                    "model": {"name": s["model_name"], "version": s["version"],
                              "algorithm": s["algorithm"], "trained_at": s["trained_at"],
                              "threshold": s["score"]["threshold"],
                              "n_features": len(s["features"])}})


@app.route("/api/schema")
def api_schema():
    s = load_schema()
    return jsonify({"features": s["features"], "score": {k: v for k, v in s["score"].items()
                                                         if k != "train_quantiles_0_100"}})


@app.route("/api/examples")
def api_examples():
    return jsonify([{"name": n, "input": r} for n, r in EXAMPLES])


@app.route("/api/detect", methods=["POST", "OPTIONS"])
def api_detect():
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "JSON 객체(13개 피처)를 보내주세요"}), 400
    try:
        return jsonify(detect(body))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"서버 오류: {e}"}), 500


@app.route("/api/detect/batch", methods=["POST"])
def api_detect_batch():
    body = request.get_json(silent=True)
    if not isinstance(body, list) or not body or len(body) > 1000:
        return jsonify({"error": "1~1000개 경기의 JSON 배열을 보내주세요"}), 400
    out = []
    for i, item in enumerate(body):
        if not isinstance(item, dict):
            return jsonify({"error": "각 항목은 JSON 객체여야 합니다", "index": i}), 400
        try:
            out.append(detect(item))
        except ValueError as e:
            return jsonify({"error": str(e), "index": i}), 400
    return jsonify(out)


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "not found"}), 404


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("ANOMALY_PORT", 9534)))
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    load_model()   # 기동 시 한 번 읽어 첫 요청이 느리지 않게
    print(f"[anomaly_detect] http://{a.host}:{a.port}", flush=True)
    app.run(host=a.host, port=a.port, debug=False, threaded=True)
