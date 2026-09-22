# 백엔드 API 서버 (FR-9) — 포트 B9524. 경기 상태를 받아 모델 API 서버의 예측 결과를 돌려준다.
#
# 실행: python web/app.py            (환경변수 BACKEND_PORT, 기본 9524)
#       ./check_project.sh start     (모델 API 9544 + 백엔드 9524 + 프런트 9504 함께)
#
# 중요: 이 서버는 예측을 직접 하지 않는다. 모델을 읽지도 않는다.
#       예측·분류(승패 예측 · 코칭 · 시험셋 복기 · 소환사 복기)는 전부 독립 모델 API 서버
#       (models/app.py · FastAPI · 127.0.0.1:9544 · 환경변수 MODEL_API_URL)에 HTTP 로 넘기고
#       응답을 기존 서빙 계약(docs/serving.md 3장) 모양으로 돌려줄 뿐이다.
#       모델 API 를 부르는 곳은 아래 _api() 하나다. web/test_parity.py 가 "직접 호출 · 모델 API ·
#       백엔드 · 프런트" 네 경로의 확률이 같은지 검증한다.
import argparse
import csv
import json
from collections import defaultdict
import os
import time
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)                      # 루트의 predict.py
sys.path.insert(0, os.path.join(ROOT, "src")) # src/riot_api.py

from flask import Flask, jsonify, render_template, request

from lolwin.features import DIFF13, KOREAN, gold_bin_bounds   # 피처 이름·한글명·구간 경계 (계산 아님)
from lolwin.artifacts import SCHEMA_PATH
from lolwin.predict import DEMOS                                # 예시 입력 3건 (모델 API /examples 와 같은 값)

# Riot API 연동은 선택 기능 — 키가 없어도 나머지는 정상 동작해야 한다
try:
    from riot_api import RateLimited, RiotApiError, analyze_recent, key_works
    # 키가 '있는지' 가 아니라 '지금 통하는지' 를 본다. 개발용 키는 24시간마다 죽는데,
    # 죽은 키로 화면이 소환사 검색을 권하면 시연 중에 고장 난 것처럼 보인다.
    RIOT_READY = key_works()
except Exception:
    RIOT_READY = False

BACKEND_PORT = int(os.environ.get("BACKEND_PORT", 9524))
DOMAIN = os.environ.get("DOMAIN", "p4.sumzip.com")
REPORTS = os.path.join(ROOT, "reports")
STARTED_AT = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ── 모델 API 클라이언트 ────────────────────────────────────────────
# 예측·분류는 이 서버가 계산하지 않는다. 독립 모델 API 서버(models/app.py · FastAPI)에 HTTP 로 넘긴다.
# 주소는 프런트(web/frontend.py)와 같은 환경변수를 읽는다: MODEL_API_URL, 없으면 127.0.0.1:API_PORT(9544).
MODEL_API_PORT = int(os.environ.get("API_PORT", 9544))
MODEL_API = os.environ.get("MODEL_API_URL", f"http://127.0.0.1:{MODEL_API_PORT}").rstrip("/")
MODEL_API_TIMEOUT = float(os.environ.get("MODEL_API_TIMEOUT", 30))
API_BATCH_MAX = 32          # models/schemas.py BatchRequest.max_length — 더 많으면 나눠 보낸다


class ModelApiUnavailable(Exception):
    """모델 API 에 닿지 못했다(연결 거부·시간 초과) 또는 준비 중(503). 호출자는 503 으로 답한다."""


class InputError(ValueError):
    """모델 API 가 입력을 거부했다(422). index 는 일괄 요청에서 몇 번째 항목인지(없으면 None)."""

    def __init__(self, message, index=None):
        super().__init__(message)
        self.index = index


def _format_422(detail):
    """pydantic 오류 목록 → 사람이 읽는 한 줄. 빠진 피처는 lolwin.predict 와 같은 문구로 알린다."""
    if isinstance(detail, str):
        return detail, None
    if not isinstance(detail, list):
        return "입력 형식이 맞지 않습니다", None
    index = None
    for d in detail:
        loc = list(d.get("loc", []))
        if "items" in loc and loc.index("items") + 1 < len(loc) and isinstance(loc[loc.index("items") + 1], int):
            index = loc[loc.index("items") + 1]
            break
    missing = [str(d.get("loc", ["?"])[-1]) for d in detail if d.get("type") == "missing"]
    others = [f"{d.get('loc', ['?'])[-1]}: {d.get('msg')}" for d in detail if d.get("type") != "missing"]
    parts = ([f"입력에 빠진 피처 {len(missing)}개: {missing}"] if missing else []) + others
    return " · ".join(parts) or "입력 형식이 맞지 않습니다", index


def _api(path, body=None, timeout=MODEL_API_TIMEOUT):
    """모델 API 를 한 번 부른다 — 이 서버에서 모델 API 를 부르는 유일한 곳.

    422(입력 문제) → InputError, 503·연결 실패 → ModelApiUnavailable, 그 밖 → RuntimeError.
    """
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(MODEL_API + path, data=data, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        if e.code == 422:
            msg, index = _format_422(detail)
            raise InputError(msg, index)
        if e.code == 503:
            raise ModelApiUnavailable(detail or "모델 API 가 준비 중입니다")
        raise RuntimeError(f"모델 API 오류 {e.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ModelApiUnavailable(f"모델 API({MODEL_API})에 연결할 수 없습니다: {e}")


def _clean(payload):
    """모델 API 계약(피처 13개 · 추가 키 금지)에 맞춰 아는 피처만 남긴다. 빠진 것은 그대로 두어 API 가 알려준다."""
    return {k: payload[k] for k in DIFF13 if k in payload}


_META = None


def _meta():
    """예측 응답에 얹는 모델 메타 — schema.json 에서 한 번만 읽는다 (docs/serving.md 3장 계약 유지)."""
    global _META
    if _META is None:
        s = _schema()
        _META = {"model": s["model_name"], "version": s["version"], "time_point_min": s["time_point_min"],
                 "holdout_accuracy": s["metrics_holdout"]["accuracy"]}
    return _META


def _to_contract(out):
    """모델 API 응답(models/schemas.py PredictResponse) → 이 서버의 서빙 계약(docs/serving.md 3장).

    화면과 기존 연동은 pred_label 을 읽으므로 그대로 두고, 모델 API 가 더 주는
    label(접전이면 «판단보류») · anomaly(이상탐지) · model_version 은 그대로 얹는다.
    """
    return {"win_prob_blue": out["win_prob_blue"], "pred": out["pred"],
            "pred_label": "블루 승리 예측" if out["pred"] else "레드 승리 예측",
            "label": out["label"], "top_factors": out["top_factors"], "warnings": out["warnings"],
            "anomaly": out.get("anomaly"), "model_version": out.get("model_version"), "meta": _meta()}


def predict(payload):
    """한 건 — 모델 API POST /predict. 이 서버의 모든 예측 호출이 이 함수 하나를 지난다."""
    return _to_contract(_api("/predict", _clean(payload)))


def predict_batch(rows):
    """여러 건 — POST /predict/batch 를 32건씩. 단건과 같은 서버·같은 모델이라 같은 입력엔 같은 결과."""
    out = []
    for i in range(0, len(rows), API_BATCH_MAX):
        chunk = [_clean(r) for r in rows[i:i + API_BATCH_MAX]]
        try:
            res = _api("/predict/batch", {"items": chunk}, timeout=max(MODEL_API_TIMEOUT, 120))
        except InputError as e:
            raise InputError(str(e), None if e.index is None else i + e.index)
        out += [_to_contract(r) for r in res["results"]]
    return out


def _model_api_status():
    """/api/health 에 싣는 모델 API 상태 — 켜져 있나, 어떤 버전인가."""
    try:
        h = _api("/health", timeout=5)
        return {"url": MODEL_API, "ok": h.get("status") == "ok", "model_version": h.get("model_version")}
    except Exception as e:
        return {"url": MODEL_API, "ok": False, "error": str(e)}

app = Flask(__name__)


@app.after_request
def cors(resp):
    # 프런트(9504)는 /api 를 같은 오리진으로 프록시하지만, 백엔드 포트를 직접 부르는 경우도 허용
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


# 숫자로 변환하면 안 되는 열 — 앞자리 0 이 사라지거나 뜻이 달라진다
TEXT_COLUMNS = {"tag", "name", "champion", "position", "구간", "유형", "label",
                # match_id 는 117030752644841619 같은 18자리다. 숫자로 바꾸면
                # ① 서버에서 문자열 비교가 어긋나고 ② JS 의 안전 정수 한계(2^53)를
                # 넘어 브라우저에서 값이 뭉개진다. 반드시 문자열로 둔다.
                "match_id", "team1_code", "team2_code", "bo"}


def _csv(name):
    """reports 의 csv 를 dict 목록으로 (BOM 포함 파일 대응, 숫자는 숫자로).

    표는 reports/tables/ 로 옮겨졌지만 예전 경로(reports/)에 있을 수도 있어 둘 다 찾는다.
    한쪽만 보면 파일이 이동했을 때 API 가 조용히 빈 배열을 돌려준다(실제로 그런 적이 있다).
    """
    for path in (os.path.join(REPORTS, "tables", name), os.path.join(REPORTS, name)):
        if os.path.exists(path):
            break
    else:
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = []
        for row in csv.DictReader(f):
            out = {}
            for k, v in row.items():
                k = k or "name"
                # 사람 이름·Riot 태그는 숫자로 보여도 문자열이다.
                # 태그 "0223" 을 int 로 바꾸면 223 이 되어 링크가 죽는다(실제로 죽었다).
                if k in TEXT_COLUMNS:
                    out[k] = v
                    continue
                try:
                    out[k] = float(v) if ("." in v or "e" in v.lower()) else int(v)
                except (ValueError, AttributeError):
                    out[k] = v
            rows.append(out)
        return rows


def _schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _parity():
    """모델 API 경로(이 서버의 predict)와 lolwin.predict 직접 호출이 같은 확률을 내는지 실측한다.

    예측은 모델 API 가 하므로 이제 «같은 함수» 라서 같은 게 아니라, 모델 API 의 모델 사본
    (models/model/artifacts)이 정본(artifacts/)과 같아야 같다. 어긋나면 여기서 잡힌다.
    API 가 꺼져 있으면 passed=False 로 두고 /api/health 때마다 다시 잰다.
    """
    from lolwin.predict import predict as local_predict   # 검증용 기준값. 서빙 경로에서는 쓰지 않는다
    method = "backend calls model API POST /predict; compared with lolwin.predict; web/test_parity.py verifies over HTTP"
    try:
        diff = max(abs(local_predict(p)["win_prob_blue"] - predict(p)["win_prob_blue"]) for _, p in DEMOS)
    except ModelApiUnavailable as e:
        return {"passed": False, "max_abs_diff": None, "verified_at": _now(), "error": str(e), "method": method}
    return {"passed": diff == 0.0, "max_abs_diff": diff, "verified_at": _now(), "model_api": MODEL_API, "method": method}


PARITY = None


@app.route("/")
def index():
    return render_template("index.html", riot_ready=RIOT_READY)


@app.route("/api/health")
def api_health():
    global PARITY
    if PARITY is None or not PARITY.get("passed"):     # 기동 때 API 가 꺼져 있었으면 지금 다시 잰다
        PARITY = _parity()
    s = _schema()
    return jsonify({"status": "ok", "service": "backend", "port": BACKEND_PORT, "domain": DOMAIN, "riot_ready": RIOT_READY,
                    "model": {"name": s["model_name"], "version": s["version"], "time_point_min": s["time_point_min"],
                              "trained_at": s.get("trained_at"), "holdout_accuracy": s["metrics_holdout"]["accuracy"],
                              "n_features": len(s["features"])},
                    "model_api": _model_api_status(),
                    "parity": PARITY, "started_at": STARTED_AT})


@app.route("/api/schema")
def api_schema():
    """화면이 입력 폼을 그리기 위해 읽는 입력 계약 — schema.json 원문."""
    return jsonify(_schema())


@app.route("/api/examples")
def api_examples():
    return jsonify([{"id": ["close", "blue", "red"][i], "label": name, "payload": payload} for i, (name, payload) in enumerate(DEMOS)])


@app.route("/api/report")
def api_report():
    """성능·오류 리포트 — 기준선 대비 개선 폭과 교차검증 평균±표준편차를 항상 함께."""
    s = _schema()
    repeat = _csv("repeat_experimentA.csv")
    accs = [r["정확도"] for r in repeat if isinstance(r.get("정확도"), (int, float))]
    mean = sum(accs) / len(accs) if accs else None
    std = (sum((a - mean) ** 2 for a in accs) / len(accs)) ** 0.5 if accs else None
    bins = _csv("day4_error_analysis.csv")
    # 화면이 "이 경기는 어느 구간인가"를 판정하려면 경계값이 필요하다.
    # 화면에 숫자를 다시 적지 않도록 서버가 상한을 함께 내려준다 (경계 정본은 lolwin/features.py).
    bounds = dict(gold_bin_bounds())
    for b in bins:
        hi = bounds.get(b.get("구간"))
        b["max_gold"] = None if hi is None or hi == float("inf") else hi
    return jsonify({
        "performance": {**s.get("metrics_holdout", {}), **{k: s[k] for k in ("metrics_cv", "baseline") if k in s},
                        "repeat_seeds": {"n": len(accs), "mean": round(mean, 4) if mean else None, "std": round(std, 4) if std else None}},
        "errors": {"bins": bins, "weakest_bin": min(bins, key=lambda b: b["정확도"])["구간"] if bins else None},
        "win_factors": _csv("win_factor_ranking.csv"),
        "experiment_b": {"close_games": _csv("expB_close_games.csv"), "coef_shift": _csv("expB_coef_shift.csv"),
                         "repeat": _csv("repeat_experimentB.csv")},
        "feature_names": KOREAN,
    })


def _type_label(row, rows):
    """군집 번호 → 사람이 읽는 이름. 표시용 규칙이지 예측 규칙이 아니다 (docs/data_analysis.md 6장)."""
    if row["시야전_총와드"] == max(r["시야전_총와드"] for r in rows):
        return "시야전"
    if row["일방성_골드차"] == max(r["일방성_골드차"] for r in rows):
        return "일방적 경기"
    return "난타전" if row["난타전_총킬"] >= 13 else "운영전"


@app.route("/api/match-types")
def api_match_types():
    rows = _csv("day2b_game_type_profile.csv")
    neutral = ["일방성_골드차", "난타전_총킬", "오브젝트_총획득", "시야전_총와드", "성장_총CS"]
    types = [{"id": int(r["cluster"]), "label": _type_label(r, rows), "count": int(r["n"]), "share_pct": r["비중_%"],
              "lead_team_win_rate": r["리드팀승률"], "centroid": {c: r[c] for c in neutral}} for r in rows]
    return jsonify({"neutral_features": neutral, "k": len(types), "types": sorted(types, key=lambda t: -t["count"]),
                    "note": "k=4 는 실루엣이 아니라 해석 가능성으로 고른 값. 예측 입력으로 쓰지 않는다."})


# ── 실제 경기 복기 (/api/matches) ──────────────────────────────
# 왜 이 기능이 필요한가:
#   Riot API 키 없이도 "진짜 경기"를 보여줘야 서비스가 성립한다.
#   시험셋 1,976판은 학습에 한 번도 안 쓴 실제 경기이고 최종 승패를 안다.
#   그래서 "10분 시점 예측 vs 실제 결과" 를 그대로 복기할 수 있다.
#   모델이 맞힌 판뿐 아니라 **틀린 판도 숨기지 않고** 보여주는 것이 이 서비스의 태도다.
_MATCHES = None            # 서버 기동 후 첫 요청 때 한 번만 계산해 캐시


def _build_matches():
    """시험셋 전체를 미리 예측해 복기 카드 목록으로 만든다.

    1,976판을 매 요청마다 예측하면 느리므로 한 번만 계산한다.
    (13개 피처 * 1,976행이라 메모리 부담은 없다)
    """
    from lolwin.coach import verdict_of        # 판정 이름표(우세승·역전패…) — 예측이 아니라 표시 규칙
    from lolwin.data import load

    _, _, X_te, y_te, _ = load()
    bounds = gold_bin_bounds()
    rows = {idx: {f: float(X_te.at[idx, f]) for f in DIFF13} for idx in X_te.index}
    preds = dict(zip(rows, predict_batch(list(rows.values()))))   # 모델 API /predict/batch 32건씩
    out = []
    for idx in X_te.index:
        row, r = rows[idx], preds[idx]
        gold = abs(row["GoldDiff"])
        band = next((lab for lab, hi in bounds if gold < hi), bounds[-1][0])
        actual = int(y_te.loc[idx])
        out.append({
            "id": int(idx),
            "gold_diff": int(row["GoldDiff"]),
            "kills_diff": int(row["KillsDiff"]),
            "dragons_diff": int(row["DragonsDiff"]),
            "exp_diff": int(row["ExpDiff"]),
            "band": band,
            "win_prob_blue": r["win_prob_blue"],
            "pred": r["pred"],
            "actual": actual,
            "correct": r["pred"] == actual,
            # 샘플 경기는 '블루 팀 관점'으로 판정한다 (소환사 경기는 그 사람 팀 관점)
            "verdict": verdict_of(r["win_prob_blue"], bool(actual)),
            # 5개 전부 — 펼침 패널의 왼쪽 칸을 채우고, 4~5위가 0 근처라는 것도 보인다
            "top_factors": [{"feature": f["feature"], "name": f["name"],
                             "value": f["value"], "contribution": f["contribution"]}
                            for f in r["top_factors"]],
            "features": row,
        })
    return out


def _warm_matches():
    """기동 직후 백그라운드에서 복기 목록을 미리 만든다. 실패해도(모델 API 꺼짐 등) 첫 요청 때 다시 시도한다."""
    global _MATCHES
    try:
        t = time.time()
        built = _build_matches()
        _MATCHES = built
        print(f"[backend] 복기 목록 예열 완료 {len(built)}건 {time.time() - t:.1f}s", flush=True)
    except Exception as e:
        print(f"[backend] 복기 목록 예열 실패(첫 요청 때 다시 시도): {e}", flush=True)


@app.route("/api/coach", methods=["POST"])
def api_coach():
    """감독 — 이 경기 상태에서 무엇을 했다면 승률이 얼마나 올랐나.

    진단(어디서 졌나)에서 멈추지 않고 처방까지 준다.
    계산은 모델 API POST /coach (그 안의 lolwin.coach) 한 곳에서만 한다 — 화면은 문장만 만든다.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON 본문이 필요합니다."}), 400
    body = _clean(data)
    if data.get("verdict"):
        body["verdict"] = data["verdict"]
    try:
        return jsonify(_api("/coach", body))
    except InputError as e:
        return jsonify({"error": str(e)}), 400
    except ModelApiUnavailable as e:
        return jsonify({"error": f"모델 API 가 꺼져 있어 코칭을 할 수 없습니다: {e}", "hint": "./check_api.sh start"}), 503


@app.route("/api/matches")
def api_matches():
    """실제 경기 복기 — 시험셋에서 조건에 맞는 경기를 돌려준다.

    질의 인자
      band     구간 이름으로 거르기 (예: "접전(<1k)")
      correct  "1" 맞힌 것만 · "0" 틀린 것만
      limit    최대 개수 (기본 12, 최대 60)
      offset   더 보기용 시작 위치
      seed     같은 seed 면 같은 순서 — "더 보기" 가 중복되지 않게
    """
    global _MATCHES
    if _MATCHES is None:
        _MATCHES = _build_matches()

    rows = _MATCHES

    # 경기 번호 직접 조회 — 검색창의 "#7758 찾기" 용. 있으면 그 한 판만 돌려준다.
    match_id = request.args.get("id")
    if match_id:
        try:
            want = int(match_id)
        except ValueError:
            return jsonify({"error": "경기 번호는 숫자입니다."}), 400
        hitrows = [m for m in rows if m["id"] == want]
        return jsonify({"total": len(hitrows), "accuracy": None, "offset": 0,
                        "returned": len(hitrows), "matches": hitrows,
                        "note": "경기 번호로 찾은 결과입니다."})

    band = request.args.get("band")
    if band:
        rows = [m for m in rows if m["band"] == band]
    correct = request.args.get("correct")
    if correct in ("0", "1"):
        rows = [m for m in rows if m["correct"] == (correct == "1")]

    # 매번 같은 순서로 보여주면 지루하므로 seed 로 섞되, 같은 seed 면 재현된다
    try:
        seed = int(request.args.get("seed", 0))
    except ValueError:
        seed = 0
    if seed:
        import random
        rows = rows[:]
        random.Random(seed).shuffle(rows)

    try:
        limit = max(1, min(60, int(request.args.get("limit", 12))))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        limit, offset = 12, 0

    page = rows[offset:offset + limit]
    hit = sum(1 for m in rows if m["correct"])
    from collections import Counter
    vc = Counter(m["verdict"] for m in rows)
    return jsonify({
        "total": len(rows),
        "accuracy": round(hit / len(rows), 4) if rows else None,
        "summary": {
            "n": len(rows),
            "avg_win_prob_10min": (round(sum(m["win_prob_blue"] for m in rows) / len(rows), 4)
                                   if rows else None),
            "역전패": vc.get("역전패", 0), "열세패": vc.get("열세패", 0),
            "역전승": vc.get("역전승", 0), "우세승": vc.get("우세승", 0),
            "model_correct": hit,
        },
        "offset": offset,
        "returned": len(page),
        "matches": page,
        "note": "학습에 한 번도 쓰지 않은 시험셋 경기입니다. 틀린 판도 그대로 보여줍니다.",
    })


@app.route("/api/ranks", methods=["POST"])
def api_ranks():
    """puuid 목록의 솔로랭크 티어 — 행을 펼칠 때만 부른다.

    참가자 10명 티어를 기본 응답에 넣으면 판마다 최대 10콜이 붙어
    한 페이지에 22콜이 든다. Personal 키 예산(100회/120초)으로는 감당이 안 된다.
    """
    from riot_api import RateLimited, get_rank

    if not RIOT_READY:
        return jsonify({"ranks": {}}), 200
    data = request.get_json() or {}
    puuids = [p for p in (data.get("puuids") or []) if isinstance(p, str)][:10]
    out = {}
    try:
        for pu in puuids:
            out[pu] = get_rank(pu)
    except RateLimited as e:
        return jsonify({"error": str(e), "retry_after": e.retry_after, "ranks": out}), 429
    except Exception:
        pass
    return jsonify({"ranks": out})


# ── 승부예측 투표 ─────────────────────────────────────────────
# 돈이 오가지 않는 참여형 예측이다(네이버·치지직이 하는 이벤트와 같은 형태).
# model_card 금지 6번은 "베팅·배당 산출" 을 막는 것이지 이런 투표를 막지 않는다.
# 배당을 만들지 않고, 참가비도 상금도 없다.
VOTE_DB = os.path.join(ROOT, "db", "votes.sqlite3")


def _vote_db():
    import sqlite3

    os.makedirs(os.path.dirname(VOTE_DB), exist_ok=True)
    conn = sqlite3.connect(VOTE_DB, timeout=5)
    conn.execute("""CREATE TABLE IF NOT EXISTS votes (
        match_id TEXT NOT NULL,
        voter    TEXT NOT NULL,
        pick     INTEGER NOT NULL,
        voted_at TEXT NOT NULL,
        PRIMARY KEY (match_id, voter))""")
    return conn


@app.route("/api/schedule")
def api_schedule():
    """예정 경기 + 현재 표. 일정은 CSV 스냅샷이라 Riot·외부 호출이 0회다."""
    rows = _csv("schedule.csv") or []
    voter = (request.args.get("voter") or "")[:64]
    tally, mine = {}, {}
    try:
        conn = _vote_db()
        for mid, pick, n in conn.execute(
                "SELECT match_id, pick, COUNT(*) FROM votes GROUP BY match_id, pick"):
            tally.setdefault(mid, {1: 0, 2: 0})[pick] = n
        if voter:
            mine = {mid: pick for mid, pick in conn.execute(
                "SELECT match_id, pick FROM votes WHERE voter = ?", (voter,))}
        conn.close()
    except Exception:
        pass                       # 투표 저장이 안 되더라도 일정은 보여준다

    out = []
    now = datetime.now(timezone.utc)
    for r in rows:
        # 이미 시작한 경기는 목록에서 뺀다 — 투표도 못 하는데(409) 화면에 남아
        # "죽은 경기" 가 쌓인다. CSV 가 낡아도 화면은 항상 예정 경기만 보인다.
        try:
            if datetime.fromisoformat(str(r["start_at"]).replace("Z", "+00:00")) <= now:
                continue
        except (ValueError, TypeError):
            pass
        t = tally.get(r["match_id"], {1: 0, 2: 0})
        out.append({**{k: r.get(k) for k in
                       ("match_id", "start_at", "league", "block", "bo",
                        "team1", "team1_code", "team1_img",
                        "team2", "team2_code", "team2_img")},
                    "votes1": t.get(1, 0), "votes2": t.get(2, 0),
                    "my_pick": mine.get(r["match_id"])})
    meta = None
    mp = os.path.join(ROOT, "reports", "tables", "schedule_meta.json")
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            meta = json.load(f)
    return jsonify({"matches": out, "meta": meta})


@app.route("/api/vote", methods=["POST"])
def api_vote():
    """한 경기에 한 번. 같은 사람이 다시 누르면 바꾼 것으로 본다."""
    body = request.get_json(silent=True) or {}
    mid = str(body.get("match_id", ""))[:64]
    voter = str(body.get("voter", ""))[:64]
    pick = body.get("pick")
    if not mid or not voter or pick not in (1, 2):
        return jsonify({"error": "match_id · voter · pick(1|2) 가 필요합니다"}), 400

    # 이미 시작한 경기에는 투표를 받지 않는다 — 결과를 보고 찍는 것을 막는다
    row = next((r for r in (_csv("schedule.csv") or []) if r["match_id"] == mid), None)
    if not row:
        return jsonify({"error": "예정 경기 목록에 없습니다"}), 404
    try:
        started = datetime.fromisoformat(row["start_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) >= started:
            return jsonify({"error": "이미 시작한 경기입니다"}), 409
    except ValueError:
        pass

    conn = _vote_db()
    with conn:
        conn.execute("INSERT INTO votes(match_id, voter, pick, voted_at) VALUES(?,?,?,?) "
                     "ON CONFLICT(match_id, voter) DO UPDATE SET pick=excluded.pick, "
                     "voted_at=excluded.voted_at",
                     (mid, voter, int(pick), datetime.now(timezone.utc).isoformat(timespec="seconds")))
    t = {1: 0, 2: 0}
    for p, n in conn.execute("SELECT pick, COUNT(*) FROM votes WHERE match_id=? GROUP BY pick", (mid,)):
        t[p] = n
    conn.close()
    return jsonify({"match_id": mid, "my_pick": int(pick),
                    "votes1": t.get(1, 0), "votes2": t.get(2, 0)})


@app.route("/api/ranking")
def api_ranking():
    """솔로랭크 상위 1,000명 — 100명씩 페이지.

    league-v4 는 이름을 주지 않아 1인당 account-v1 을 한 번 더 불러야 한다.
    1,000명이면 1,000회라 라이브에서는 불가능하므로, src/collect_ranking.py 가
    미리 모아둔 CSV 만 읽는다 (Riot 호출 0회).
    """
    rows = _csv("ranking.csv") or []
    try:
        page = max(1, min(10, int(request.args.get("page", 1))))
    except ValueError:
        page = 1
    per = 100
    start = (page - 1) * per
    out = []
    for r in rows[start:start + per]:
        wins, losses = int(r.get("wins") or 0), int(r.get("losses") or 0)
        total = wins + losses
        out.append({"rank": int(r["rank"]), "tier": r["tier"],
                    "name": r.get("name") or "", "tag": r.get("tag") or "",
                    "lp": int(r.get("lp") or 0), "wins": wins, "losses": losses,
                    "win_rate": round(wins / total, 4) if total else None})
    meta = None
    mpath = os.path.join(ROOT, "reports", "tables", "ranking_meta.json")
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as f:
            meta = json.load(f)
    return jsonify({"rows": out, "page": page, "per_page": per,
                    "total": len(rows), "pages": min(10, -(-len(rows) // per)), "meta": meta})


@app.route("/api/champions")
def api_champions():
    """챔피언 x 라인 승률표 — 마스터 이상 솔로랭크 실측.

    src/collect_champion_stats.py 가 만든 집계 CSV 를 그대로 내려준다.
    이 값은 우리 모델의 예측이 아니라 **관찰된 승률**이다 (인과 아님).
    """
    data = _csv("champion_stats.csv")
    if not data:
        return jsonify({"rows": [], "meta": None,
                        "note": "아직 수집된 챔피언 통계가 없습니다."}), 200
    pos = (request.args.get("position") or "").upper()
    rows = [{"champion": r["champion"], "position": r["position"],
             "games": int(r["경기수"]), "win_rate": float(r["승률"]),
             "pick_rate": float(r["픽률"])}
            for r in data if not pos or r["position"] == pos]
    # 표본이 적은 조합을 승률만으로 위에 올리면 "7판 85%" 가 1위가 된다.
    # 충분한 표본(MIN)을 먼저 세우고, 부족한 것은 아래로 내려 표시만 한다.
    MIN_GAMES = 10
    for r in rows:
        r["low_sample"] = r["games"] < MIN_GAMES
    rows.sort(key=lambda x: (x["low_sample"], -x["win_rate"]))

    meta = None
    mpath = os.path.join(ROOT, "reports", "tables", "champion_stats_meta.json")
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as f:
            meta = json.load(f)
    # 챔피언별 "잘하는 사람" — 공부용 참고. 없으면 조용히 빈 목록.
    tops = defaultdict(list)
    for r in (_csv("champion_top_players.csv") or []):
        key = f"{r['champion']}|{r['position']}"
        if len(tops[key]) < 5:
            tops[key].append({"name": r["name"], "tag": r["tag"],
                              "games": int(r["판수"]), "win_rate": float(r["승률"])})
    for row in rows:
        row["top_players"] = tops.get(f"{row['champion']}|{row['position']}", [])

    return jsonify({"rows": rows, "meta": meta,
                    "note": "마스터 이상 솔로랭크 관찰 승률입니다. 표본 5판 미만은 제외했습니다."})


# 소환사 조회 결과 캐시 — 방문자 한 명이 페이지를 열 때마다 Riot 을 12번 부르면
# 다섯 명만 동시에 들어와도 예산(100회/120초)이 끝난다. 같은 요청은 재사용한다.
# 경기 기록은 이미 끝난 경기라 몇 분 사이에 바뀌지 않는다.
_SUMMONER_CACHE: dict = {}
SUMMONER_TTL = 300          # 5분


def _cached_summoner(riot_id: str, count: int, start: int):
    """캐시에 있으면 그대로, 없으면 조회 후 저장. (값, 캐시적중여부)"""
    key = (riot_id.lower(), count, start)
    hit = _SUMMONER_CACHE.get(key)
    if hit and time.time() - hit[0] < SUMMONER_TTL:
        return hit[1], True
    data = analyze_recent(riot_id, count=count, start=start, predict_fn=predict)   # 예측은 모델 API
    _SUMMONER_CACHE[key] = (time.time(), data)
    # 오래된 항목 정리 (메모리가 무한정 늘지 않게)
    if len(_SUMMONER_CACHE) > 200:
        cutoff = time.time() - SUMMONER_TTL
        for k in [k for k, v in _SUMMONER_CACHE.items() if v[0] < cutoff]:
            _SUMMONER_CACHE.pop(k, None)
    return data, False


@app.route("/api/summoner", methods=["POST"])
def api_summoner():
    """Riot ID 로 최근 솔로랭크 경기들을 10분 시점에서 복기한다 (끝난 경기만 가능)."""
    if not RIOT_READY:
        # 키가 아예 없는 것과, 있는데 만료된 것은 다른 상황이다.
        # 뭉뚱그리면 "넣으라"는 안내를 받고 이미 넣은 사람이 혼란스러워진다.
        if os.environ.get("RIOT_API_KEY"):
            return jsonify({"error": "Riot API 키가 만료되어 소환사 조회를 일시 중단했습니다. "
                                     "잠시 후 다시 시도해 주세요."}), 503
        return jsonify({"error": "서버에 Riot API 키가 없어 소환사 조회를 쓸 수 없습니다."}), 503
    try:
        data = request.get_json() or {}
        riot_id = data.get("riot_id", "").strip()
        # 한 판마다 타임라인을 받아야 해서 count 가 크면 느리다 — 상한 10.
        # start 는 "더 보기" 용으로 건너뛸 개수다.
        try:
            count = max(1, min(10, int(data.get("count", 5))))
            start = max(0, int(data.get("start", 0)))
        except (TypeError, ValueError):
            count, start = 5, 0
        data, cached = _cached_summoner(riot_id, count, start)
        data["cached"] = cached
        return jsonify(data)
    except RateLimited as e:
        # 기다리지 않고 바로 알린다 — 스레드를 붙잡으면 프록시가 502 를 낸다
        return jsonify({"error": str(e), "retry_after": e.retry_after}), 429
    except RiotApiError as e:
        return jsonify({"error": str(e)}), 400
    except ModelApiUnavailable as e:
        return jsonify({"error": f"모델 API 가 꺼져 있어 복기를 할 수 없습니다: {e}", "hint": "./check_api.sh start"}), 503
    except Exception as e:
        return jsonify({"error": f"서버 오류: {e}"}), 500


@app.route("/api/predict", methods=["POST", "OPTIONS"])
def api_predict():
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "JSON 객체(13개 피처)를 보내주세요"}), 400
        payload = {k: float(v) for k, v in body.items()}
        return jsonify(predict(payload))
    except (ValueError, TypeError) as e:   # 빠진 피처·숫자 아님·형식 한계 밖 등 입력 문제 (모델 API 의 422 포함)
        return jsonify({"error": str(e)}), 400
    except ModelApiUnavailable as e:        # 모델 API 가 꺼져 있다 — 이 서버는 대신 계산하지 않는다
        return jsonify({"error": f"모델 API 가 꺼져 있어 예측할 수 없습니다: {e}", "hint": "./check_api.sh start"}), 503
    except Exception as e:                  # 그 외 서버 문제
        return jsonify({"error": f"서버 오류: {e}"}), 500


@app.route("/api/predict/batch", methods=["POST"])
def api_predict_batch():
    """일괄 예측 — 모델 API /predict/batch 를 32건씩 부른다. 단건과 같은 서버·모델이라 같은 입력엔 같은 결과."""
    body = request.get_json(silent=True)
    if not isinstance(body, list) or not body or len(body) > 1000:
        return jsonify({"error": "1~1000개 경기의 JSON 배열을 보내주세요"}), 400
    rows = []
    for i, item in enumerate(body):
        try:
            rows.append({k: float(v) for k, v in item.items()})
        except (ValueError, TypeError, AttributeError) as e:
            return jsonify({"error": str(e), "index": i}), 400
    try:
        return jsonify(predict_batch(rows))
    except InputError as e:
        return jsonify({"error": str(e), "index": e.index}), 400
    except ModelApiUnavailable as e:
        return jsonify({"error": f"모델 API 가 꺼져 있어 예측할 수 없습니다: {e}", "hint": "./check_api.sh start"}), 503


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "not found"}), 404


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=BACKEND_PORT)
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    PARITY = _parity()
    # 시험셋 복기(/api/matches)는 1,976건을 모델 API 에 32건씩 보내 20초쯤 걸린다 — 첫 방문자가 기다리지 않게 미리 만든다
    threading.Thread(target=_warm_matches, daemon=True, name="warm-matches").start()
    print(f"[backend] http://{a.host}:{a.port}  domain={DOMAIN}  riot={'on' if RIOT_READY else 'off'}  "
          f"model_api={MODEL_API}  parity={PARITY['passed']}", flush=True)
    if not PARITY["passed"]:
        if PARITY.get("error"):
            print(f"[backend] 경고: {PARITY['error']} — 예측·코칭은 503 이 된다. ./check_api.sh start 후 /api/health 가 다시 잰다", flush=True)
        else:
            print(f"[backend] 경고: 모델 API 의 확률이 lolwin.predict 와 다르다 (최대 차이 {PARITY['max_abs_diff']}) — "
                  "models/model/artifacts 사본이 정본 artifacts/ 와 어긋났는지 확인할 것", flush=True)
    app.run(host=a.host, port=a.port, debug=False, threaded=True)
