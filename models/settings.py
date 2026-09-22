"""settings.py — 4팀 LoL 승패 예측·이상탐지의 설정 (MS5 방식). 값은 «환경변수 → .env → 기본값»."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    model_name: str = "LoL 10분 시점 승패 예측"
    model_version: str = "lolwin-1.0"                      # artifacts/schema.json 의 version + 이상탐지 1.0
    model_description: str = ("10분 시점 경기 상태(블루−레드 차이 피처 13개)를 받아 블루팀 승리 확률·예측·승리요인을 돌려준다. "
                              "같은 입력으로 이상탐지(학습 데이터에서 보기 드문 경기 상태)도 함께 판정한다. "
                              "연계 방법은 docs/api_guide.md.")
    close_margin: float = 0.10                             # |확률 − 0.5| 가 이보다 작으면 «접전» — 판단보류로 사람이 본다 (MS4)
    include_anomaly: bool = True                           # 이상탐지 결과를 응답에 넣을지
    log_level: str = "INFO"
    max_batch_size: int = 32
    host: str = "127.0.0.1"                                # 프록시(프런트 9504) 뒤에만 둔다 — 포트를 밖에 직접 열지 않는다 (학생절차 5장)
    port: int = 9544                                       # 4팀 = 954N 규약. 프런트 9504 · 웹 백엔드 9524
    root_path: str = "/model-api"                          # 공개 경로 접두. check_api.sh 가 uvicorn --root-path 로 넘긴다 (코드에서 root_path 를 또 주지 않는다)
    cors_origins: str = "*"                                # 브라우저에서 직접 부르는 외부 서비스용. 쉼표로 여러 개, 비우면 CORS 없음
    api_keys: str = ""                                     # 쉼표 구분 허용 키 목록. 비어 있으면 키 검사 없음 (내부망 기본)


settings = Settings()
