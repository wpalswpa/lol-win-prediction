"""이상탐지 — 이 모델의 점수를 계산하는 유일한 곳 (웹·CLI·에이전트 전부 여기를 부른다).

    from models.anomaly_detect.detect import detect
    detect({"FirstBlood": 1, "KillsDiff": 0, ..., "TotalJungleMinionsKilledDiff": -2})

입력  : 13개 차이 피처 dict (승패 모델 /api/predict 와 완전히 같은 형식)
출력  : anomaly_score · is_anomaly · percentile · unusual_features · fence_violations · warnings · meta
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(HERE, "model.joblib")
SCHEMA_PATH = os.path.join(HERE, "schema.json")
TOP_N = 3

_model = None
_schema = None


def load_schema() -> dict:
    global _schema
    if _schema is None:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            _schema = json.load(f)
    return _schema


def load_model():
    global _model
    if _model is None:
        import joblib
        if not os.path.exists(MODEL_PATH):
            raise FileNotFoundError(f"{MODEL_PATH} 가 없습니다 — python models/anomaly_detect/train.py")
        _model = joblib.load(MODEL_PATH)
    return _model


def reset_cache() -> None:
    global _model, _schema
    _model = _schema = None


def detect(payload: dict) -> dict:
    """경기 상태 13개 피처 → 이상 여부.

    빠진 피처가 있거나 숫자가 아니면 ValueError (호출한 쪽이 400 으로 바꾼다).
    """
    import numpy as np
    import pandas as pd

    model, schema = load_model(), load_schema()
    features = list(schema["features"].keys())

    missing = [f for f in features if f not in payload]
    if missing:
        raise ValueError(f"입력에 빠진 피처 {len(missing)}개: {missing}")
    try:
        row = {f: float(payload[f]) for f in features}
    except (TypeError, ValueError) as e:
        raise ValueError(f"숫자가 아닌 값이 있습니다: {e}") from e
    if any(np.isnan(v) or np.isinf(v) for v in row.values()):
        raise ValueError("NaN/Inf 는 받지 않습니다")

    X = pd.DataFrame([row])[features]
    score = float(-model.score_samples(X)[0])
    sc = schema["score"]
    threshold = sc["threshold"]
    grid = np.asarray(sc["train_quantiles_0_100"])
    percentile = float(np.interp(score, grid, np.arange(101), left=0.0, right=100.0))

    # 어떤 피처가 유별난가 — 표준화 z 절대값 상위 TOP_N
    scaler = model.named_steps["scaler"]
    z = (X.values[0] - scaler.mean_) / scaler.scale_
    order = np.argsort(-np.abs(z))[:TOP_N]
    unusual = [{
        "feature": features[i], "name": schema["features"][features[i]]["korean"],
        "value": row[features[i]], "z": round(float(z[i]), 3),
        "train_mean": schema["features"][features[i]]["train_mean"],
    } for i in order]

    # IQR 울타리 위반 (docs/data_analysis.md 5장 규칙)
    fences = []
    warnings = []
    for f in features:
        fs = schema["features"][f]
        v = row[f]
        if not (fs["fence_low"] <= v <= fs["fence_high"]):
            fences.append({"feature": f, "name": fs["korean"], "value": v,
                           "fence": [round(fs["fence_low"], 2), round(fs["fence_high"], 2)]})
        if not (fs["train_min"] <= v <= fs["train_max"]):
            warnings.append(f"{f}={v} 가 학습 범위 [{fs['train_min']}, {fs['train_max']}] 밖")

    is_anomaly = bool(score > threshold)
    if is_anomaly:
        label = "이상 — 학습 데이터에서 보기 드문 경기 상태"
    elif fences or percentile >= 90:
        label = "주의 — 임계값 이내지만 드문 값이 있음"
    else:
        label = "정상 — 흔한 경기 상태"

    return {
        "anomaly_score": round(score, 6),
        "is_anomaly": is_anomaly,
        "percentile": round(percentile, 1),
        "label": label,
        "unusual_features": unusual,
        "fence_violations": fences,
        "warnings": warnings,
        "meta": {
            "model": schema["model_name"], "version": schema["version"],
            "time_point_min": schema["time_point_min"],
            "threshold": threshold, "contamination": sc["contamination"],
        },
    }


def detect_batch(rows: list[dict]) -> list[dict]:
    return [detect(r) for r in rows]


EXAMPLES = [
    ("정상 — 팽팽한 접전", dict(FirstBlood=1, KillsDiff=0, GoldDiff=150, ExpDiff=-100,
                          WardsPlacedDiff=2, WardsDestroyedDiff=0, AssistsDiff=1,
                          DragonsDiff=0, HeraldsDiff=0, TowersDestroyedDiff=0,
                          AvgLevelDiff=0.0, TotalMinionsKilledDiff=5,
                          TotalJungleMinionsKilledDiff=-2)),
    ("주의 — 와드 200개 차이(단일 피처 극단)", dict(FirstBlood=1, KillsDiff=1, GoldDiff=300, ExpDiff=200,
                             WardsPlacedDiff=210, WardsDestroyedDiff=0, AssistsDiff=1,
                             DragonsDiff=0, HeraldsDiff=0, TowersDestroyedDiff=0,
                             AvgLevelDiff=0.1, TotalMinionsKilledDiff=3,
                             TotalJungleMinionsKilledDiff=0)),
    ("이상 — 골드는 크게 앞서는데 킬·경험치는 크게 뒤짐", dict(
        FirstBlood=0, KillsDiff=-9, GoldDiff=8000, ExpDiff=-6000,
        WardsPlacedDiff=0, WardsDestroyedDiff=0, AssistsDiff=-12,
        DragonsDiff=-1, HeraldsDiff=-1, TowersDestroyedDiff=0,
        AvgLevelDiff=-2.0, TotalMinionsKilledDiff=-90,
        TotalJungleMinionsKilledDiff=-50)),
]


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:                      # python detect.py '{"FirstBlood":1,...}'
        print(json.dumps(detect(json.loads(sys.argv[1])), ensure_ascii=False, indent=2))
    else:
        for name, row in EXAMPLES:
            r = detect(row)
            print(f"{name:40s} score={r['anomaly_score']:.4f}  pct={r['percentile']:5.1f}  "
                  f"anomaly={r['is_anomaly']}  → {r['label']}")
