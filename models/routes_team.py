"""routes_team.py — 4팀 전용 엔드포인트. app.py 는 팀 이름·필드를 모르므로 팀 것은 여기 둔다.

  GET  /schema     입력 피처 13개의 뜻·형·학습 범위 (입력 폼을 만들 때)
  GET  /examples   예시 입력 3건 (접전·블루 우세·레드 우세)
  POST /coach      이 상태에서 무엇을 했다면 승률이 얼마나 올랐나 (모델을 6번 부르므로 서버 안에서 한 번에)
"""
import logging

from fastapi import APIRouter, HTTPException, Request

from schemas import CoachRequest, CoachResponse, Example

logger = logging.getLogger("ms")
router = APIRouter()


@router.get("/schema", summary="입력 피처 13개의 뜻·형·학습 범위")
def api_schema(request: Request):
    return request.app.state.team.input_schema()


@router.get("/examples", response_model=list[Example], summary="예시 입력 3건")
def api_examples(request: Request):
    return request.app.state.team.examples()


@router.post("/coach", response_model=CoachResponse, summary="감독 — 무엇을 했다면 승률이 얼마나 올랐나")
def api_coach(req: CoachRequest, request: Request):
    if not request.app.state.ready():
        raise HTTPException(status_code=503, detail="모델을 준비 중입니다. 잠시 후 다시 시도하세요")
    try:
        return request.app.state.team.coach(req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        logger.exception("코칭 실패 req_id=%s", request.state.req_id)
        raise HTTPException(status_code=500, detail="서버에서 판단에 실패했습니다")
