"""schemas.py — 4팀 계약. 팀의 artifacts/schema.json(13피처·타입·학습 범위)을 pydantic 으로 옮겼다.

학습 범위 밖 값은 막지 않는다 — 팀 설계대로 warnings 에 담는다(막아버리면 이례적인 경기를 아예 못 본다).
막는 것은 «형식»뿐이다: 13개가 전부 있어야 하고, 숫자여야 하고, FirstBlood 는 0/1, 드래곤·전령은 -1~1.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PredictRequest(BaseModel):
    """블루 − 레드 차이 13개. 양수 = 블루 우세."""
    model_config = ConfigDict(extra="forbid")

    FirstBlood: int = Field(..., ge=0, le=1, description="블루가 첫 킬을 냈으면 1")
    KillsDiff: int = Field(..., ge=-50, le=50)
    GoldDiff: int = Field(..., ge=-30000, le=30000, description="골드 차이")
    ExpDiff: int = Field(..., ge=-30000, le=30000, description="경험치 차이")
    WardsPlacedDiff: int = Field(..., ge=-500, le=500)
    WardsDestroyedDiff: int = Field(..., ge=-100, le=100)
    AssistsDiff: int = Field(..., ge=-100, le=100)
    DragonsDiff: int = Field(..., ge=-1, le=1)
    HeraldsDiff: int = Field(..., ge=-1, le=1)
    TowersDestroyedDiff: int = Field(..., ge=-11, le=11)
    AvgLevelDiff: float = Field(..., ge=-10, le=10)
    TotalMinionsKilledDiff: int = Field(..., ge=-500, le=500)
    TotalJungleMinionsKilledDiff: int = Field(..., ge=-300, le=300)


class Factor(BaseModel):
    feature: str
    name: str
    value: float
    contribution: float
    direction: Literal["블루에 유리", "레드에 유리"]


class Anomaly(BaseModel):
    is_anomaly: bool
    score: float
    percentile: float = Field(..., ge=0, le=100, description="학습 데이터 기준 백분위. 98 이상이 이상")
    label: str
    unusual_features: list[str]


class PredictResponse(BaseModel):
    label: Literal["블루 승리 예측", "레드 승리 예측", "판단보류"]
    win_prob_blue: float = Field(..., ge=0, le=1)
    pred: Literal[0, 1]
    top_factors: list[Factor] = Field(..., max_length=5)
    anomaly: Anomaly | None
    warnings: list[str]
    model_version: str


class BatchRequest(BaseModel):
    items: list[PredictRequest] = Field(..., min_length=1, max_length=32)


class BatchResponse(BaseModel):
    results: list[PredictResponse]
    count: int


class HealthResponse(BaseModel):
    status: Literal["ok", "loading", "error"]
    model_name: str
    model_version: str
    load_seconds: float | None = None


# ── 4팀 추가 계약 (routes_team.py) ─────────────────────────────
class CoachAction(BaseModel):
    feature: str
    name: str
    action: str
    win_prob_after: float = Field(..., ge=0, le=1)
    gain: float


class CoachRequest(PredictRequest):
    """13개 피처 + 선택 verdict(역전패·열세패·역전승·우세승) — verdict 가 있으면 판정별 처방을 함께 준다."""
    verdict: Literal["역전패", "열세패", "역전승", "우세승"] | None = None


class CoachResponse(BaseModel):
    win_prob: float = Field(..., ge=0, le=1)
    actions: list[CoachAction] = Field(..., max_length=5)
    how_to_read: str
    verdict_advice: dict | None = None
    model_version: str


class Example(BaseModel):
    id: str
    label: str
    payload: PredictRequest
