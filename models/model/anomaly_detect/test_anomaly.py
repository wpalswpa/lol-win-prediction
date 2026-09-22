"""이상탐지 모델 검사 — 골든 정답지 재현 · 입력 계약 · 틀린 값이 잡히는지.

    python models/anomaly_detect/test_anomaly.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from detect import EXAMPLES, detect, load_schema   # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("[통과] " if cond else "[실패] ") + msg)
    if not cond:
        fails += 1


# 1. 골든 정답지 20건 — 점수·판정·백분위가 저장 당시와 같은가
golden = json.load(open(os.path.join(HERE, "golden.json"), encoding="utf-8"))
bad = []
for g in golden:
    r = detect(g["input"])
    if (abs(r["anomaly_score"] - g["anomaly_score"]) > 1e-6 or r["is_anomaly"] != g["is_anomaly"]
            or abs(r["percentile"] - g["percentile"]) > 0.05):
        bad.append((g["anomaly_score"], r["anomaly_score"]))
check(not bad, f"골든 {len(golden)}건 재현 (불일치 {len(bad)}건)")

# 2. 출력 계약 — 키와 형식
r = detect(EXAMPLES[0][1])
keys = {"anomaly_score", "is_anomaly", "percentile", "label", "unusual_features",
        "fence_violations", "warnings", "meta"}
check(keys <= set(r), f"출력 키 {sorted(keys)}")
check(isinstance(r["is_anomaly"], bool) and 0 <= r["percentile"] <= 100, "is_anomaly 는 bool · percentile 0~100")
check(len(r["unusual_features"]) == 3, "unusual_features 는 3개")

# 3. 예시가 의도한 판정을 내는가
check(detect(EXAMPLES[0][1])["is_anomaly"] is False, "접전 예시 → 정상")
check(detect(EXAMPLES[1][1])["fence_violations"][0]["feature"] == "WardsPlacedDiff", "와드 210 → 울타리 위반에 WardsPlacedDiff")
check(detect(EXAMPLES[2][1])["is_anomaly"] is True, "골드·킬 모순 예시 → 이상")

# 4. 틀린 입력이 잡히는가 (규칙을 만들면 틀린 값을 넣어 확인한다 — CLAUDE.md)
bad_inputs = {
    "피처 누락": {k: v for k, v in EXAMPLES[0][1].items() if k != "GoldDiff"},
    "문자열": {**EXAMPLES[0][1], "GoldDiff": "많이"},
    "NaN": {**EXAMPLES[0][1], "GoldDiff": float("nan")},
}
for name, payload in bad_inputs.items():
    try:
        detect(payload); check(False, f"{name} → ValueError 가 나야 하는데 통과함")
    except ValueError:
        check(True, f"{name} → ValueError")

# 5. 임계값이 학습 데이터 상위 2% 인가 (schema 내부 일관성)
s = load_schema()
check(abs(s["metrics"]["anomaly_rate_train"] - s["score"]["contamination"]) < 0.005,
      f"학습 이상 비율 {s['metrics']['anomaly_rate_train']} ≈ contamination {s['score']['contamination']}")

print("\n전부 통과" if fails == 0 else f"\n실패 {fails}건")
sys.exit(1 if fails else 0)
