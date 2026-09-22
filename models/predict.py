"""predict.py — 4팀 두 모델(승패 예측 lolwin · 이상탐지 anomaly_detect)을 MS 과정의 «한 건 예측» 모양으로 감싼다.

팀 패키지는 이미 «확률을 계산하는 유일한 곳»(lolwin.predict.predict) 과 «점수를 계산하는 유일한 곳»(detect.detect) 을
갖추었다. 이 파일은 둘을 그대로 부르고 세 가지만 한다:
  1. 두 모델을 한 번만 적재한다(warmup)           model/artifacts · model/anomaly_detect
  2. 입력 하나로 두 모델을 부르고 한 응답으로 합친다  — 입력 형식이 완전히 같기 때문에 가능하다
  3. 접전(확률이 0.5 근처)이면 «판단보류» — 팀 모델에는 없던 정책이다. 값은 settings.close_margin

    from predict import predict
    predict({"FirstBlood": 1, "KillsDiff": 5, "GoldDiff": 4500, ...})
    → {"label": "블루 승리 예측", "win_prob_blue": 0.946, "top_factors": [...], "anomaly": {...}, ...}
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from settings import settings

# pandas 3 의 기본 문자열(pyarrow) 이 서버의 작업 스레드에서 간헐적으로 죽는다(2026-09-21 실측, 세그폴트 — 1팀과 같다).
# 팀 코드가 요청마다 DataFrame 을 만들므로, 팀 코드를 고치지 않고 여기서 옛 object 문자열로 되돌린다.
pd.set_option("future.infer_string", False)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "model"))                     # lolwin 패키지 (artifacts/ 는 패키지가 스스로 찾는다)
sys.path.insert(0, str(HERE / "model" / "anomaly_detect"))  # detect 모듈

MODEL_VERSION = settings.model_version
_ready = False


def _load():
    global _ready
    if not _ready:
        from lolwin.artifacts import load_model, load_schema
        import detect as anomaly
        load_model(), load_schema()
        anomaly.load_model(), anomaly.load_schema()
        _ready = True


def warmup():
    _load()


def predict(payload: dict) -> dict:
    _load()
    from lolwin.predict import predict as lol_predict
    import detect as anomaly

    win = lol_predict(payload)                              # 빠진 피처가 있으면 ValueError → 422
    prob = win["win_prob_blue"]
    close = abs(prob - 0.5) < settings.close_margin
    label = "판단보류" if close else win["pred_label"]
    warnings = list(win["warnings"])
    if close:
        warnings.append(f"접전 — 승리 확률 {prob:.3f} 이 0.5±{settings.close_margin} 안이라 예측을 보류한다")

    out = {
        "label": label,
        "win_prob_blue": prob,
        "pred": win["pred"],
        "top_factors": [{"feature": f["feature"], "name": f["name"], "value": f["value"],
                         "contribution": f["contribution"], "direction": f["direction"]}
                        for f in win["top_factors"][:5]],
        "anomaly": None,
        "warnings": warnings,
        "model_version": MODEL_VERSION,
    }
    if settings.include_anomaly:
        a = anomaly.detect(payload)
        out["anomaly"] = {"is_anomaly": a["is_anomaly"], "score": a["anomaly_score"],
                          "percentile": a["percentile"], "label": a["label"],
                          "unusual_features": [u["feature"] for u in a["unusual_features"]]}
    return out


if __name__ == "__main__":
    from lolwin.predict import DEMOS
    for name, row in DEMOS:
        print(name, "->", predict(row))


def coach(payload: dict) -> dict:
    """감독 — 이 상태에서 무엇을 했다면 승률이 얼마나 올랐나. 계산은 lolwin.coach 한 곳에서만 한다."""
    _load()
    from lolwin.coach import advise, verdict_advice

    verdict = payload.pop("verdict", None)
    out = advise(payload)                                   # 빠진 피처 → ValueError → 422
    out["verdict_advice"] = verdict_advice(verdict) if verdict else None
    out["model_version"] = MODEL_VERSION
    return out


def examples() -> list[dict]:
    """예시 입력 3건 — 외부 호출자가 입력 폼·버튼을 만들 때 쓴다 (lolwin.predict.DEMOS 와 같은 값)."""
    from lolwin.predict import DEMOS
    return [{"id": ["close", "blue", "red"][i], "label": name, "payload": row} for i, (name, row) in enumerate(DEMOS)]


def input_schema() -> dict:
    """입력 계약 원문 — 피처 13개의 뜻·형·학습 범위 (artifacts/schema.json)."""
    from lolwin.artifacts import load_schema
    return load_schema()
