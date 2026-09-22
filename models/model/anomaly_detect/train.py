"""이상탐지 모델 학습 — 10분 시점 경기 상태(13개 차이 피처)가 '학습 데이터에서 본 적 없는 모양' 인지 판정.

    python models/anomaly_detect/train.py

승패 모델(artifacts/)과 같은 재료를 쓴다: lolwin.features.DIFF13 · data/splits 의 train 분할 · seed 42.
산출물(이 폴더):
    model.joblib   StandardScaler + IsolationForest 파이프라인
    schema.json    피처·학습 범위·IQR 울타리·임계값·학습 데이터 지문
    golden.json    예시 입력 20건의 정답 출력 — test_anomaly.py 가 대조
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from lolwin.data import load                       # noqa: E402
from lolwin.features import DIFF13, KOREAN, TIME_POINT_MIN   # noqa: E402

SEED = 42
CONTAMINATION = 0.02      # 학습 데이터 상위 2% 를 '이상' 으로 보는 임계값 (schema 에 저장)
IQR_MULT = 3.0            # docs/data_analysis.md 5장과 같은 규칙: Q1−3·IQR ~ Q3+3·IQR 밖이면 피처 이상치


def main() -> None:
    X_tr, y_tr, X_te, y_te, meta = load("csv")
    X_tr, X_te = X_tr[DIFF13], X_te[DIFF13]

    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("model", IsolationForest(n_estimators=300, max_samples=256,
                                  contamination="auto", random_state=SEED, n_jobs=1)),
    ]).fit(X_tr)

    # 점수: score_samples 는 음수(작을수록 이상). 부호를 뒤집어 "클수록 이상" 으로 통일.
    s_tr = -pipe.score_samples(X_tr)
    s_te = -pipe.score_samples(X_te)
    threshold = float(np.quantile(s_tr, 1 - CONTAMINATION))
    grid = [float(v) for v in np.quantile(s_tr, np.linspace(0, 1, 101))]

    feats = {}
    for f in DIFF13:
        col = X_tr[f].astype(float)
        q1, q3 = float(col.quantile(0.25)), float(col.quantile(0.75))
        iqr = q3 - q1
        feats[f] = {
            "type": "float" if f == "AvgLevelDiff" else "int",
            "korean": KOREAN[f],
            "train_min": float(col.min()), "train_max": float(col.max()),
            "train_mean": round(float(col.mean()), 3), "train_std": round(float(col.std()), 3),
            "q1": q1, "q3": q3, "iqr": iqr,
            # 울타리 = "학습에서 본 정상 범위". IQR 울타리와 학습 범위 중 좁은 쪽.
            # (FirstBlood 처럼 0/1 인 피처는 IQR 울타리가 [-3, 4] 로 무의미하므로 학습 범위가 대신 잡아 준다)
            # IQR 이 0 인 피처(Dragons·Heralds·Towers)는 Q1=Q3 라 울타리가 한 점이 되므로 학습 범위를 쓴다.
            "fence_low": max(q1 - IQR_MULT * iqr, float(col.min())) if iqr > 0 else float(col.min()),
            "fence_high": min(q3 + IQR_MULT * iqr, float(col.max())) if iqr > 0 else float(col.max()),
        }

    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                         text=True).strip()
    except Exception:
        commit = None

    import sklearn
    schema = {
        "model_name": "LoL 10분 경기상태 이상탐지",
        "version": "1.0",
        "algorithm": "StandardScaler + IsolationForest(n_estimators=300, max_samples=256)",
        "time_point_min": TIME_POINT_MIN,
        "task": "unsupervised anomaly detection (정답 없음 — 승패 라벨을 쓰지 않는다)",
        "feature_set": "diff13 (블루-레드 차이, 양수=블루 우세) — artifacts/schema.json 과 동일 순서",
        "trained_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sklearn_version": sklearn.__version__,
        "seed": SEED,
        "score": {
            "definition": "anomaly_score = -IsolationForest.score_samples (클수록 이상)",
            "threshold": round(threshold, 6),
            "contamination": CONTAMINATION,
            "train_quantiles_0_100": grid,
            "train_min": round(float(s_tr.min()), 6), "train_max": round(float(s_tr.max()), 6),
        },
        "iqr_fence_multiplier": IQR_MULT,
        "metrics": {
            "n_train": int(len(X_tr)), "n_test": int(len(X_te)),
            "anomaly_rate_train": round(float((s_tr > threshold).mean()), 4),
            "anomaly_rate_test": round(float((s_te > threshold).mean()), 4),
            "test_score_mean": round(float(s_te.mean()), 4),
        },
        "features": feats,
        "provenance": {"data_source": meta["data_source"], "data_hash": meta["data_hash"],
                       "split_hash": meta.get("split_hash"), "git_commit": commit},
    }

    joblib.dump(pipe, os.path.join(HERE, "model.joblib"))
    with open(os.path.join(HERE, "schema.json"), "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)

    # 골든 정답지 — 시험셋 점수 상위 10 + 하위 10 (학습에 안 쓴 행이라 순수 재현성 검사)
    from detect import detect, reset_cache   # noqa: E402  (방금 저장한 파일을 읽는다)
    reset_cache()
    order = np.argsort(-s_te)
    idx = list(order[:10]) + list(order[-10:])
    golden = []
    for i in idx:
        row = {f: float(X_te.iloc[i][f]) for f in DIFF13}
        out = detect(row)
        golden.append({"input": row, "anomaly_score": out["anomaly_score"],
                       "is_anomaly": out["is_anomaly"], "percentile": out["percentile"]})
    with open(os.path.join(HERE, "golden.json"), "w", encoding="utf-8") as f:
        json.dump(golden, f, ensure_ascii=False, indent=1)

    m = schema["metrics"]
    print(f"학습 {m['n_train']}건 · 시험 {m['n_test']}건 · 임계값 {threshold:.4f}")
    print(f"이상 판정 비율  학습 {m['anomaly_rate_train']:.2%} · 시험 {m['anomaly_rate_test']:.2%}")
    print(f"저장: {HERE}/model.joblib · schema.json · golden.json({len(golden)}건)")


if __name__ == "__main__":
    main()
