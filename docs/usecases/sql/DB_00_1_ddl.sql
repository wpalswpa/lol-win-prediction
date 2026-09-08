-- =====================================================================
-- DB_00 — LoL 인게임 시점별 승패 예측·핵심 승리요인 분석 서비스 데이터 저장 구조
-- 1/5 구조 DDL — 테이블 32 · 인덱스 6 (CREATE TABLE · CREATE INDEX 만)
--
-- 문서: docs/usecases/DB_00_데이터베이스구조설계.md
-- 실행 순서: 1_ddl → 2_seed → 3_views → 4_grants(MariaDB·PostgreSQL 만) → 5_queries 는 필요할 때
--   MariaDB : mysql -h <서버> -P <포트> -u <사용자> -p <팀DB명> < docs/usecases/sql/DB_00_1_ddl.sql
--   SQLite  : sqlite3 lolex.sqlite3 ".read docs/usecases/sql/DB_00_1_ddl.sql"   (PRAGMA foreign_keys = ON 필요)
-- 표기: ISO/IEC 9075 SQL:2016 의 벤더 중립 부분집합 — IDENTITY·JSON 형·ENGINE·COMMENT·ON DUPLICATE KEY 를 쓰지 않는다.
-- =====================================================================
--
-- 원천: UC_00~UC_14 · BP_00 §5 · UI_00 · SCR-00 · SCR-H1-H5 · docs/serving.md · db/create_ml_views.sql ·
--       web/app.py · src/collect_*.py · src/riot_api.py analyze_recent · artifacts/schema.json · tests/golden_predictions.json
-- 영역: A 학습·모델·평가(§1) · B 스냅샷·투표(§2) · C 복기 캐시(§3, 5분 보존) · D 비공개·운영(§4, 배포 금지)
-- 기존 테이블(lol_matches_10min · ml_split · lol_analysis_10min)은 동결 대상이라 이름·컬럼을 그대로 두었다.
-- 팀 MariaDB 에 이미 있는 §1.1~1.3 은 건너뛴다 (docs/plan.md "DROP 금지").
-- 대리키(snapshot_id · review_key)는 IDENTITY 대신 응용이 발급 규칙대로 넣는다(각 표 주석).
-- =====================================================================

-- =====================================================================
-- §1. A. 학습·모델·평가
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1.1 lol_matches_10min — 학습 원천 (기존, db/load_mysql.py 가 적재)
--     9,879행 × 40컬럼. gameId 가 기본키라 같은 경기가 두 번 들어가지 않는다.
-- ---------------------------------------------------------------------
CREATE TABLE lol_matches_10min (
  gameId                        BIGINT            NOT NULL,
  blueWins                      SMALLINT          NOT NULL CHECK (blueWins IN (0, 1)),
  blueWardsPlaced               INTEGER           NOT NULL,
  blueWardsDestroyed            INTEGER           NOT NULL,
  blueFirstBlood                SMALLINT          NOT NULL CHECK (blueFirstBlood IN (0, 1)),
  blueKills                     INTEGER           NOT NULL,
  blueDeaths                    INTEGER           NOT NULL,
  blueAssists                   INTEGER           NOT NULL,
  blueEliteMonsters             INTEGER           NOT NULL,
  blueDragons                   INTEGER           NOT NULL,
  blueHeralds                   INTEGER           NOT NULL,
  blueTowersDestroyed           INTEGER           NOT NULL,
  blueTotalGold                 INTEGER           NOT NULL,
  blueAvgLevel                  DOUBLE PRECISION  NOT NULL,
  blueTotalExperience           INTEGER           NOT NULL,
  blueTotalMinionsKilled        INTEGER           NOT NULL,
  blueTotalJungleMinionsKilled  INTEGER           NOT NULL,
  blueGoldDiff                  INTEGER           NOT NULL,
  blueExperienceDiff            INTEGER           NOT NULL,
  blueCSPerMin                  DOUBLE PRECISION  NOT NULL,
  blueGoldPerMin                DOUBLE PRECISION  NOT NULL,
  redWardsPlaced                INTEGER           NOT NULL,
  redWardsDestroyed             INTEGER           NOT NULL,
  redFirstBlood                 SMALLINT          NOT NULL CHECK (redFirstBlood IN (0, 1)),
  redKills                      INTEGER           NOT NULL,
  redDeaths                     INTEGER           NOT NULL,
  redAssists                    INTEGER           NOT NULL,
  redEliteMonsters              INTEGER           NOT NULL,
  redDragons                    INTEGER           NOT NULL,
  redHeralds                    INTEGER           NOT NULL,
  redTowersDestroyed            INTEGER           NOT NULL,
  redTotalGold                  INTEGER           NOT NULL,
  redAvgLevel                   DOUBLE PRECISION  NOT NULL,
  redTotalExperience            INTEGER           NOT NULL,
  redTotalMinionsKilled         INTEGER           NOT NULL,
  redTotalJungleMinionsKilled   INTEGER           NOT NULL,
  redGoldDiff                   INTEGER           NOT NULL,
  redExperienceDiff             INTEGER           NOT NULL,
  redCSPerMin                   DOUBLE PRECISION  NOT NULL,
  redGoldPerMin                 DOUBLE PRECISION  NOT NULL,
  CONSTRAINT pk_lol_matches_10min PRIMARY KEY (gameId),
  -- 거울 관계 2건만 제약으로 고정 (db/create_ml_views.sql 머리말의 중복 11개 중 검증 비용이 0 인 것)
  CONSTRAINT ck_matches_firstblood_mirror CHECK (redFirstBlood = 1 - blueFirstBlood),
  CONSTRAINT ck_matches_golddiff_mirror   CHECK (redGoldDiff = -blueGoldDiff)
);

-- ---------------------------------------------------------------------
-- 1.2 ml_split — 층화 분할 라벨 (기존, db/load_split.py). seed 42 고정.
-- ---------------------------------------------------------------------
CREATE TABLE ml_split (
  gameId  BIGINT      NOT NULL,
  split   VARCHAR(5)  NOT NULL CHECK (split IN ('train', 'test')),
  CONSTRAINT pk_ml_split PRIMARY KEY (gameId),
  CONSTRAINT fk_ml_split_match FOREIGN KEY (gameId) REFERENCES lol_matches_10min (gameId)
);
CREATE INDEX ix_ml_split_split ON ml_split (split);

-- ---------------------------------------------------------------------
-- 1.3 lol_analysis_10min — 분석용 통합 스냅샷 (기존, db/build_analysis_table.py)
--     차이 피처 13 + 사이드 중립 피처 5 + 경기 유형(KMeans k=4, seed 42).
--     gameType 은 표시용 이름이며 예측 입력이 아니다 (BR-RPT-05).
-- ---------------------------------------------------------------------
CREATE TABLE lol_analysis_10min (
  gameId                        BIGINT            NOT NULL,
  y                             SMALLINT          NOT NULL CHECK (y IN (0, 1)),
  split                         VARCHAR(5)        NOT NULL CHECK (split IN ('train', 'test')),
  FirstBlood                    INTEGER           NOT NULL,
  KillsDiff                     INTEGER           NOT NULL,
  GoldDiff                      INTEGER           NOT NULL,
  ExpDiff                       INTEGER           NOT NULL,
  WardsPlacedDiff               INTEGER           NOT NULL,
  WardsDestroyedDiff            INTEGER           NOT NULL,
  AssistsDiff                   INTEGER           NOT NULL,
  DragonsDiff                   INTEGER           NOT NULL,
  HeraldsDiff                   INTEGER           NOT NULL,
  TowersDestroyedDiff           INTEGER           NOT NULL,
  AvgLevelDiff                  DOUBLE PRECISION  NOT NULL,
  TotalMinionsKilledDiff        INTEGER           NOT NULL,
  TotalJungleMinionsKilledDiff  INTEGER           NOT NULL,
  oneSidedGold                  INTEGER           NOT NULL,
  totalKills                    INTEGER           NOT NULL,
  totalObjects                  INTEGER           NOT NULL,
  totalWards                    INTEGER           NOT NULL,
  totalCS                       INTEGER           NOT NULL,
  gameType                      VARCHAR(12)       NOT NULL
      CHECK (gameType IN ('일방적경기', '시야전', '난타전', '운영전')),
  CONSTRAINT pk_lol_analysis_10min PRIMARY KEY (gameId),
  CONSTRAINT fk_analysis_match FOREIGN KEY (gameId) REFERENCES lol_matches_10min (gameId)
);
CREATE INDEX ix_analysis_split ON lol_analysis_10min (split);
CREATE INDEX ix_analysis_type  ON lol_analysis_10min (gameType);

-- ---------------------------------------------------------------------
-- 1.4 gold_band — 골드차 구간 이름 (코드표, 행은 DB_00_2_seed.sql)
--     경계 숫자의 정본은 lolwin/features.py GOLD_BINS 이다 (BR-RPT-02).
--     DB 에는 "이름과 순서" 만 두어 화면·평가·복기 행이 같은 이름을 쓰게 한다.
-- ---------------------------------------------------------------------
CREATE TABLE gold_band (
  band_name  VARCHAR(30)  NOT NULL,
  ordinal    SMALLINT     NOT NULL,
  CONSTRAINT pk_gold_band PRIMARY KEY (band_name),
  CONSTRAINT uq_gold_band_ordinal UNIQUE (ordinal)
);

-- ---------------------------------------------------------------------
-- 1.5 verdict_type — 네 갈래 판정 (코드표, BR-SUM-01 · lolwin/coach.py verdict_of, 행은 DB_00_2_seed.sql)
--     ahead = 10분 시점 내 팀 승률 >= 0.5, won = 실제 승리.
-- ---------------------------------------------------------------------
CREATE TABLE verdict_type (
  verdict_name  VARCHAR(10)  NOT NULL,
  ahead         BOOLEAN      NOT NULL,
  won           BOOLEAN      NOT NULL,
  ordinal       SMALLINT     NOT NULL,
  CONSTRAINT pk_verdict_type PRIMARY KEY (verdict_name),
  CONSTRAINT uq_verdict_rule UNIQUE (ahead, won)
);

-- ---------------------------------------------------------------------
-- 1.6 model_version — artifacts/schema.json 의 머리·성능·이력(provenance)
--     한 행 = 배포 가능한 모델 하나. is_active 인 행은 하나뿐이어야 한다(응용 규칙).
-- ---------------------------------------------------------------------
CREATE TABLE model_version (
  version           VARCHAR(16)   NOT NULL,
  model_name        VARCHAR(80)   NOT NULL,
  time_point_min    SMALLINT      NOT NULL CHECK (time_point_min > 0),
  target            VARCHAR(60)   NOT NULL,
  feature_set       VARCHAR(60)   NOT NULL,
  trained_at        TIMESTAMP     NOT NULL,
  data_source       VARCHAR(20)   NOT NULL,          -- 'db' | 'csv:fallback' (BR-TRN-10)
  sklearn_version   VARCHAR(12)   NOT NULL,
  seed              INTEGER       NOT NULL,
  holdout_accuracy  DECIMAL(5,4)  NOT NULL,
  holdout_f1        DECIMAL(5,4)  NOT NULL,
  holdout_auc       DECIMAL(5,4)  NOT NULL,
  holdout_brier     DECIMAL(5,4)  NOT NULL,
  baseline_accuracy DECIMAL(5,4)  NOT NULL,
  data_hash         VARCHAR(32)   NOT NULL,
  split_hash        VARCHAR(32)   NOT NULL,
  n_train           INTEGER       NOT NULL,
  n_test            INTEGER       NOT NULL,
  git_commit        VARCHAR(40)   NOT NULL,
  cv_accuracy       DECIMAL(5,4)  NOT NULL,
  cv_std            DECIMAL(5,4)  NOT NULL,
  train_minus_cv    DECIMAL(5,4)  NOT NULL,
  cm_tn             INTEGER       NOT NULL,
  cm_fp             INTEGER       NOT NULL,
  cm_fn             INTEGER       NOT NULL,
  cm_tp             INTEGER       NOT NULL,
  model_path        VARCHAR(255)  NOT NULL,          -- 'artifacts/model.joblib' (파일 자체는 DB 밖)
  is_active         BOOLEAN       NOT NULL DEFAULT FALSE,
  CONSTRAINT pk_model_version PRIMARY KEY (version),
  CONSTRAINT ck_model_cm_total CHECK (cm_tn + cm_fp + cm_fn + cm_tp = n_test)
);

-- ---------------------------------------------------------------------
-- 1.7 model_feature — 피처 13개의 형·학습 범위 (schema.json features) + 승리요인 순위
--     ordinal 은 DIFF13 의 입력 순서다 — 바꾸지 않는다 (BR-TRN-04).
-- ---------------------------------------------------------------------
CREATE TABLE model_feature (
  version          VARCHAR(16)    NOT NULL,
  feature_name     VARCHAR(40)    NOT NULL,
  ordinal          SMALLINT       NOT NULL CHECK (ordinal BETWEEN 1 AND 13),
  korean_name      VARCHAR(40)    NOT NULL,
  data_type        VARCHAR(8)     NOT NULL CHECK (data_type IN ('int', 'float')),
  train_min        DECIMAL(12,4)  NOT NULL,
  train_max        DECIMAL(12,4)  NOT NULL,
  train_mean       DECIMAL(12,4)  NOT NULL,
  coef_std         DECIMAL(8,4),                     -- reports/tables/win_factor_ranking.csv
  coef_rank        SMALLINT,
  perm_importance  DECIMAL(8,4),
  perm_rank        SMALLINT,
  CONSTRAINT pk_model_feature PRIMARY KEY (version, feature_name),
  CONSTRAINT uq_model_feature_ordinal UNIQUE (version, ordinal),
  CONSTRAINT fk_model_feature_version FOREIGN KEY (version) REFERENCES model_version (version),
  CONSTRAINT ck_model_feature_range CHECK (train_min <= train_max)
);

-- ---------------------------------------------------------------------
-- 1.8 training_run — 실험 기록 (runs.csv, BR-TRN-01 "실험 기록")
-- ---------------------------------------------------------------------
CREATE TABLE training_run (
  run_at          TIMESTAMP     NOT NULL,
  run_name        VARCHAR(80)   NOT NULL,
  cv_acc_mean     DECIMAL(5,4),
  cv_acc_std      DECIMAL(5,4),
  cv_f1_mean      DECIMAL(5,4),
  cv_f1_std       DECIMAL(5,4),
  train_acc_mean  DECIMAL(5,4),
  train_f1_mean   DECIMAL(5,4),
  elapsed_sec     DECIMAL(8,2),
  CONSTRAINT pk_training_run PRIMARY KEY (run_at, run_name)
);

-- ---------------------------------------------------------------------
-- 1.9 golden_case / golden_case_factor — 골든 정답지 (tests/golden_predictions.json)
--     입력 13개는 DIFF13 컬럼명 그대로. 재학습하면 version 이 바뀌고 정답지도 새로 만든다 (BR-TRN-05).
-- ---------------------------------------------------------------------
CREATE TABLE golden_case (
  version                       VARCHAR(16)       NOT NULL,
  case_no                       SMALLINT          NOT NULL CHECK (case_no >= 1),
  generator_seed                INTEGER           NOT NULL,
  FirstBlood                    INTEGER           NOT NULL CHECK (FirstBlood IN (0, 1)),
  KillsDiff                     INTEGER           NOT NULL,
  GoldDiff                      INTEGER           NOT NULL,
  ExpDiff                       INTEGER           NOT NULL,
  WardsPlacedDiff               INTEGER           NOT NULL,
  WardsDestroyedDiff            INTEGER           NOT NULL,
  AssistsDiff                   INTEGER           NOT NULL,
  DragonsDiff                   INTEGER           NOT NULL,
  HeraldsDiff                   INTEGER           NOT NULL,
  TowersDestroyedDiff           INTEGER           NOT NULL,
  AvgLevelDiff                  DOUBLE PRECISION  NOT NULL,
  TotalMinionsKilledDiff        INTEGER           NOT NULL,
  TotalJungleMinionsKilledDiff  INTEGER           NOT NULL,
  expected_win_prob_blue        DECIMAL(5,4)      NOT NULL CHECK (expected_win_prob_blue BETWEEN 0 AND 1),
  expected_pred                 SMALLINT          NOT NULL CHECK (expected_pred IN (0, 1)),
  CONSTRAINT pk_golden_case PRIMARY KEY (version, case_no),
  CONSTRAINT fk_golden_case_version FOREIGN KEY (version) REFERENCES model_version (version),
  -- serving.md §3: pred = 1 iff win_prob_blue >= 0.5
  CONSTRAINT ck_golden_pred_rule CHECK (expected_pred = CASE WHEN expected_win_prob_blue >= 0.5 THEN 1 ELSE 0 END)
);

CREATE TABLE golden_case_factor (
  version       VARCHAR(16)   NOT NULL,
  case_no       SMALLINT      NOT NULL,
  rank_no       SMALLINT      NOT NULL CHECK (rank_no BETWEEN 1 AND 5),   -- top_factors 는 정확히 5개
  feature_name  VARCHAR(40)   NOT NULL,
  contribution  DECIMAL(9,4)  NOT NULL,
  CONSTRAINT pk_golden_case_factor PRIMARY KEY (version, case_no, rank_no),
  CONSTRAINT fk_golden_factor_case FOREIGN KEY (version, case_no) REFERENCES golden_case (version, case_no),
  CONSTRAINT fk_golden_factor_feature FOREIGN KEY (version, feature_name) REFERENCES model_feature (version, feature_name)
);

-- ---------------------------------------------------------------------
-- 1.10 평가 결과 — reports/tables/*.csv 중 화면(SCR-T1)이 읽는 것
--      /api/report · /api/match-types 가 서빙한다. 문서 수치의 근거 (BR-RPT-01 · BR-TRN-09).
-- ---------------------------------------------------------------------
-- 반복 실험 A (repeat_experimentA.csv) — 시드별 홀드아웃 성능
CREATE TABLE eval_seed_run (
  version         VARCHAR(16)   NOT NULL,
  seed            INTEGER       NOT NULL,
  accuracy        DECIMAL(5,4)  NOT NULL,
  f1              DECIMAL(5,4)  NOT NULL,
  auc             DECIMAL(5,4)  NOT NULL,
  close_accuracy  DECIMAL(5,4)  NOT NULL,             -- 접전 구간 정확도
  close_share     DECIMAL(5,4)  NOT NULL,             -- 접전 비중
  CONSTRAINT pk_eval_seed_run PRIMARY KEY (version, seed),
  CONSTRAINT fk_eval_seed_run_version FOREIGN KEY (version) REFERENCES model_version (version)
);

-- 반복 실험 B (repeat_experimentB.csv) — 10분 vs 15분 (프로 경기, 같은 경기·피처·분할 안에서만 비교 BR-TRN-11)
CREATE TABLE eval_timepoint_seed (
  version            VARCHAR(16)   NOT NULL,
  seed               INTEGER       NOT NULL,
  accuracy_10min     DECIMAL(5,4)  NOT NULL,
  accuracy_15min     DECIMAL(5,4)  NOT NULL,
  gain               DECIMAL(6,4)  NOT NULL,
  close_acc_10min    DECIMAL(5,4)  NOT NULL,
  close_acc_15min    DECIMAL(5,4)  NOT NULL,
  close_gain         DECIMAL(6,4)  NOT NULL,
  CONSTRAINT pk_eval_timepoint_seed PRIMARY KEY (version, seed),
  CONSTRAINT fk_eval_timepoint_version FOREIGN KEY (version) REFERENCES model_version (version)
);

-- 구간별 정확도 (day4_error_analysis.csv = 실험 A, expB_close_games.csv = 실험 B)
--   검토에서 드러난 불일치: 실험 B(프로 경기)는 구간이 3개이고 셋째 이름이 '크게 우세(2.5k+)' 라
--   features.py 의 4구간 정본과 다르다. 그래서 CSV 의 이름은 band_label 에 그대로 두고,
--   정본 구간에 대응되는 행만 band_name(FK) 을 채운다. 대응이 없으면 NULL (문서 §9 미해결 1).
CREATE TABLE eval_gold_band (
  version         VARCHAR(16)   NOT NULL,
  experiment      CHAR(1)       NOT NULL CHECK (experiment IN ('A', 'B')),
  band_label      VARCHAR(30)   NOT NULL,             -- CSV 에 적힌 이름 그대로
  band_name       VARCHAR(30),                        -- 정본 구간(gold_band) 대응, 없으면 NULL
  n_games         INTEGER       NOT NULL CHECK (n_games >= 0),
  accuracy_10min  DECIMAL(5,4)  NOT NULL,
  accuracy_15min  DECIMAL(5,4),                       -- 실험 B 만
  gain            DECIMAL(6,4),                       -- 실험 B 만
  share_pct       DECIMAL(5,1)  NOT NULL,
  CONSTRAINT pk_eval_gold_band PRIMARY KEY (version, experiment, band_label),
  CONSTRAINT fk_eval_gold_band_version FOREIGN KEY (version) REFERENCES model_version (version),
  CONSTRAINT fk_eval_gold_band_band FOREIGN KEY (band_name) REFERENCES gold_band (band_name),
  CONSTRAINT ck_eval_gold_band_b CHECK (experiment = 'B' OR (accuracy_15min IS NULL AND gain IS NULL))
);

-- 계수 변화 (expB_coef_shift.csv)
--   검토에서 드러난 불일치: 실험 B 는 피처 5개이고 'CSDiff' 처럼 DIFF13 에 없는 이름을 쓴다.
--   CSV 이름은 feature_label 에 그대로, DIFF13 에 대응되는 것만 feature_name(FK) 을 채운다 (문서 §9 미해결 1).
CREATE TABLE eval_coef_shift (
  version        VARCHAR(16)   NOT NULL,
  feature_label  VARCHAR(40)   NOT NULL,
  feature_name   VARCHAR(40),
  coef_10min     DECIMAL(8,4)  NOT NULL,
  coef_15min     DECIMAL(8,4)  NOT NULL,
  delta          DECIMAL(8,4)  NOT NULL,
  CONSTRAINT pk_eval_coef_shift PRIMARY KEY (version, feature_label),
  CONSTRAINT fk_eval_coef_shift_version FOREIGN KEY (version) REFERENCES model_version (version),
  CONSTRAINT fk_eval_coef_shift_feature FOREIGN KEY (version, feature_name) REFERENCES model_feature (version, feature_name)
);

-- 티어 일반화 (tier_accuracy.csv, model_card 금지 2 의 근거)
CREATE TABLE eval_tier_accuracy (
  version         VARCHAR(16)   NOT NULL,
  tier            VARCHAR(12)   NOT NULL
      CHECK (tier IN ('IRON','BRONZE','SILVER','GOLD','PLATINUM','EMERALD','DIAMOND','MASTER','GRANDMASTER','CHALLENGER')),
  n_games         INTEGER       NOT NULL,
  accuracy        DECIMAL(5,4)  NOT NULL,
  avg_gold_diff   DECIMAL(8,1)  NOT NULL,
  n_big_gap       INTEGER       NOT NULL,
  big_gap_share   DECIMAL(5,4)  NOT NULL,
  std_error       DECIMAL(6,4)  NOT NULL,
  CONSTRAINT pk_eval_tier_accuracy PRIMARY KEY (version, tier),
  CONSTRAINT fk_eval_tier_version FOREIGN KEY (version) REFERENCES model_version (version)
);

-- 경기 유형 프로파일 (day2b_game_type_profile.csv, /api/match-types) — 표시용, 예측 입력 아님
CREATE TABLE match_type_profile (
  version             VARCHAR(16)   NOT NULL,
  cluster_no          SMALLINT      NOT NULL,
  type_label          VARCHAR(12)   NOT NULL CHECK (type_label IN ('일방적 경기', '시야전', '난타전', '운영전')),
  n_games             INTEGER       NOT NULL,
  share_pct           DECIMAL(5,1)  NOT NULL,
  lead_team_win_rate  DECIMAL(5,4)  NOT NULL,
  one_sided_gold      DECIMAL(9,2)  NOT NULL,
  total_kills         DECIMAL(7,2)  NOT NULL,
  total_objects       DECIMAL(6,2)  NOT NULL,
  total_wards         DECIMAL(7,2)  NOT NULL,
  total_cs            DECIMAL(8,2)  NOT NULL,
  CONSTRAINT pk_match_type_profile PRIMARY KEY (version, cluster_no),
  CONSTRAINT uq_match_type_label UNIQUE (version, type_label),
  CONSTRAINT fk_match_type_version FOREIGN KEY (version) REFERENCES model_version (version)
);


-- =====================================================================
-- §2. B. 스냅샷·투표
-- =====================================================================

-- ---------------------------------------------------------------------
-- 2.1 snapshot_run — 수집 실행 한 번 (= *_meta.json 한 개)
--     snapshot_id 발급 규칙: 수집기가 수집 시작 시각의 UTC epoch 초를 넣는다.
--     kind 별로 is_current = TRUE 인 행 하나가 "화면이 보는 스냅샷" 이다 (갱신 시각 = collected_at, BR-LDR-06).
-- ---------------------------------------------------------------------
CREATE TABLE snapshot_run (
  snapshot_id    BIGINT        NOT NULL,
  kind           VARCHAR(10)   NOT NULL CHECK (kind IN ('schedule', 'ranking', 'champion')),
  collected_at   TIMESTAMP     NOT NULL,
  item_count     INTEGER       NOT NULL CHECK (item_count >= 0),   -- 경기수 · 총원 · 수집_경기수
  description    VARCHAR(200)  NOT NULL,                           -- meta '설명' / '정의'
  period_from    DATE,                                             -- champion 만
  period_to      DATE,
  patches        VARCHAR(100),                                     -- champion 만, '16.15,16.16,16.17'
  status         VARCHAR(10)   NOT NULL DEFAULT 'complete' CHECK (status IN ('complete', 'partial')),
  is_current     BOOLEAN       NOT NULL DEFAULT FALSE,
  CONSTRAINT pk_snapshot_run PRIMARY KEY (snapshot_id),
  CONSTRAINT uq_snapshot_kind_time UNIQUE (kind, collected_at)
);

-- ---------------------------------------------------------------------
-- 2.2 pro_match — 예정 프로 경기 (schedule.csv). 투표 대상 (UC8 · UC9)
--     match_id · bo · 팀 코드는 문자열로 보존한다 (BR-VOT-07 · BR-SCH-07).
--     일정 수집은 통째로 덮어쓰지만(BR-SCH-05) 투표가 참조하므로 행을 지우지 않고
--     snapshot_id 를 최신으로 옮긴다. 목록·투표 가능 여부는 뷰 v_schedule_tally 가 정한다.
-- ---------------------------------------------------------------------
CREATE TABLE pro_match (
  match_id     VARCHAR(64)   NOT NULL,
  snapshot_id  BIGINT        NOT NULL,
  start_at     TIMESTAMP     NOT NULL,                 -- UTC
  league       VARCHAR(40)   NOT NULL,
  block        VARCHAR(40),
  bo           VARCHAR(4)    NOT NULL,
  team1_name   VARCHAR(60)   NOT NULL,
  team1_code   VARCHAR(10)   NOT NULL,
  team1_img    VARCHAR(255),
  team2_name   VARCHAR(60)   NOT NULL,
  team2_code   VARCHAR(10)   NOT NULL,
  team2_img    VARCHAR(255),
  priority     SMALLINT      NOT NULL DEFAULT 99,
  CONSTRAINT pk_pro_match PRIMARY KEY (match_id),
  CONSTRAINT fk_pro_match_snapshot FOREIGN KEY (snapshot_id) REFERENCES snapshot_run (snapshot_id),
  CONSTRAINT ck_pro_match_two_teams CHECK (team1_code <> team2_code)          -- BR-SCH-02
);
CREATE INDEX ix_pro_match_start ON pro_match (start_at);

-- ---------------------------------------------------------------------
-- 2.3 votes — 승부예측 표 (기존 db/votes.sqlite3 와 같은 정의 + FK)
--     한 경기에 한 표, 다시 누르면 바꾼다 (BR-VOT-04). voter 는 브라우저 식별자 (BR-VOT-05).
-- ---------------------------------------------------------------------
CREATE TABLE votes (
  match_id  VARCHAR(64)  NOT NULL,
  voter     VARCHAR(64)  NOT NULL,
  pick      SMALLINT     NOT NULL CHECK (pick IN (1, 2)),         -- BR-VOT-08
  voted_at  TIMESTAMP    NOT NULL,
  CONSTRAINT pk_votes PRIMARY KEY (match_id, voter),
  CONSTRAINT fk_votes_match FOREIGN KEY (match_id) REFERENCES pro_match (match_id)
);

-- ---------------------------------------------------------------------
-- 2.4 ranking_entry — 솔로랭크 상위 1,000명 (ranking.csv). puuid 없음 (BR-LDR-02).
--     LP 내림차순 = 등수 (BR-LDR-04). 이름·태그는 문자열 (BR-LDR-05).
-- ---------------------------------------------------------------------
CREATE TABLE ranking_entry (
  snapshot_id  BIGINT       NOT NULL,
  rank_no      SMALLINT     NOT NULL CHECK (rank_no BETWEEN 1 AND 1000),
  tier         VARCHAR(12)  NOT NULL CHECK (tier IN ('CHALLENGER', 'GRANDMASTER')),
  game_name    VARCHAR(40)  NOT NULL,
  tag_line     VARCHAR(10)  NOT NULL,
  lp           INTEGER      NOT NULL CHECK (lp >= 0),
  wins         INTEGER      NOT NULL CHECK (wins >= 0),
  losses       INTEGER      NOT NULL CHECK (losses >= 0),
  CONSTRAINT pk_ranking_entry PRIMARY KEY (snapshot_id, rank_no),
  CONSTRAINT fk_ranking_snapshot FOREIGN KEY (snapshot_id) REFERENCES snapshot_run (snapshot_id)
);

-- ---------------------------------------------------------------------
-- 2.5 champion_match_participant — 챔피언 수집 원본 (data/champion_raw.jsonl 한 줄 = 한 행)
--     공개 Riot ID 만 담고 puuid 는 없다 (BR-CHMP-07 · BR-COL-06).
--     한 경기에 같은 챔피언은 한 번만 나오므로 (match_id, champion) 이 키다.
--     이어받기(BR-COL-01)는 이 표의 존재 여부로 판단한다.
-- ---------------------------------------------------------------------
CREATE TABLE champion_match_participant (
  match_id     VARCHAR(20)  NOT NULL,
  champion     VARCHAR(30)  NOT NULL,
  played_on    DATE         NOT NULL,
  patch        VARCHAR(8)   NOT NULL,
  position     VARCHAR(8)   NOT NULL
      CHECK (position IN ('TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY', '')),
  win          BOOLEAN      NOT NULL,
  game_name    VARCHAR(40)  NOT NULL,
  tag_line     VARCHAR(10)  NOT NULL,
  snapshot_id  BIGINT,                                   -- 어느 실행이 받았나 (없을 수 있음)
  CONSTRAINT pk_champion_match_participant PRIMARY KEY (match_id, champion),
  CONSTRAINT fk_cmp_snapshot FOREIGN KEY (snapshot_id) REFERENCES snapshot_run (snapshot_id)
);
CREATE INDEX ix_cmp_champion_position ON champion_match_participant (champion, position);

-- ---------------------------------------------------------------------
-- 2.6 champion_lane_stat — 챔피언 x 라인 관찰 승률 (champion_stats.csv)
--     5판 이상만 집계 (BR-COL-08). 10판 미만은 서빙에서 low_sample (뷰).
-- ---------------------------------------------------------------------
CREATE TABLE champion_lane_stat (
  snapshot_id  BIGINT        NOT NULL,
  champion     VARCHAR(30)   NOT NULL,
  position     VARCHAR(8)    NOT NULL CHECK (position IN ('TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY')),
  n_games      INTEGER       NOT NULL CHECK (n_games >= 5),
  win_rate     DECIMAL(5,4)  NOT NULL CHECK (win_rate BETWEEN 0 AND 1),
  pick_rate    DECIMAL(6,4)  NOT NULL CHECK (pick_rate BETWEEN 0 AND 1),
  CONSTRAINT pk_champion_lane_stat PRIMARY KEY (snapshot_id, champion, position),
  CONSTRAINT fk_cls_snapshot FOREIGN KEY (snapshot_id) REFERENCES snapshot_run (snapshot_id)
);

-- ---------------------------------------------------------------------
-- 2.7 champion_top_player — 조합별 잘하는 유저 (champion_top_players.csv)
--     8판 이상 · 승률 50% 초과 · 조합당 최대 5명 (BR-CHMP-03). 이름 클릭 → UC3 새 시작 (SCR-C2).
-- ---------------------------------------------------------------------
CREATE TABLE champion_top_player (
  snapshot_id  BIGINT        NOT NULL,
  champion     VARCHAR(30)   NOT NULL,
  position     VARCHAR(8)    NOT NULL,
  game_name    VARCHAR(40)   NOT NULL,
  tag_line     VARCHAR(10)   NOT NULL,
  n_games      INTEGER       NOT NULL CHECK (n_games >= 8),
  win_rate     DECIMAL(5,4)  NOT NULL CHECK (win_rate > 0.5 AND win_rate <= 1),
  CONSTRAINT pk_champion_top_player PRIMARY KEY (snapshot_id, champion, position, game_name, tag_line),
  CONSTRAINT fk_ctp_stat FOREIGN KEY (snapshot_id, champion, position)
      REFERENCES champion_lane_stat (snapshot_id, champion, position)
);


-- =====================================================================
-- §3. C. 복기 캐시 — /api/summoner 응답의 정규화 (5분 보존, BR-SUM-05)
--     Riot ID 는 요청 중에만 쓰고 저장하지 않는다는 약속(BR-SUM-07, DATA HANDLING)을
--     지키기 위해 expires_at 이 지난 행은 반드시 지운다(§5 정리 규칙). 백업·덤프 대상에서 뺀다.
--     review_key 발급 규칙: lower(riot_id) || '|' || count || '|' || start  (web/app.py 캐시 키와 동일)
-- =====================================================================

-- ---------------------------------------------------------------------
-- 3.1 summoner_review — 조회 한 건의 머리 + summary + rank (SCR-H3 · SCR-H4 · SCR-H5 의 원천)
-- ---------------------------------------------------------------------
CREATE TABLE summoner_review (
  review_key            VARCHAR(120)  NOT NULL,
  riot_id_key           VARCHAR(80)   NOT NULL,                    -- lower('게임명#태그')
  count_n               SMALLINT      NOT NULL CHECK (count_n BETWEEN 1 AND 10),   -- BR-SUM-04
  start_n               INTEGER       NOT NULL CHECK (start_n >= 0),
  queue                 VARCHAR(10)   NOT NULL DEFAULT '솔로랭크',                  -- BR-SUM-02
  model_version         VARCHAR(16)   NOT NULL,
  fetched_at            TIMESTAMP     NOT NULL,
  expires_at            TIMESTAMP     NOT NULL,                    -- fetched_at + 5분
  next_start            INTEGER       NOT NULL,
  -- rank (조회 실패 시 전부 NULL → 화면 "언랭")
  rank_tier             VARCHAR(12),
  rank_division         VARCHAR(4),
  rank_lp               INTEGER,
  rank_wins             INTEGER,
  rank_losses           INTEGER,
  -- summary (H4 타일·COACH 요약이 세지 않도록 서버가 센 값)
  n_games               SMALLINT      NOT NULL CHECK (n_games >= 0),
  avg_win_prob_10min    DECIMAL(5,4),
  n_ahead_win           SMALLINT      NOT NULL DEFAULT 0,          -- 우세승
  n_comeback_win        SMALLINT      NOT NULL DEFAULT 0,          -- 역전승
  n_thrown_loss         SMALLINT      NOT NULL DEFAULT 0,          -- 역전패
  n_behind_loss         SMALLINT      NOT NULL DEFAULT 0,          -- 열세패
  n_model_correct       SMALLINT      NOT NULL DEFAULT 0,
  -- summary.radar (3판 미만이면 전부 NULL, BR-SUM-09) — H3-05 · H3-06
  radar_label           VARCHAR(20),
  radar_desc            VARCHAR(200),
  radar_fight           SMALLINT      CHECK (radar_fight BETWEEN 0 AND 100),
  radar_lane            SMALLINT      CHECK (radar_lane BETWEEN 0 AND 100),
  radar_jungle          SMALLINT      CHECK (radar_jungle BETWEEN 0 AND 100),
  radar_objective       SMALLINT      CHECK (radar_objective BETWEEN 0 AND 100),
  radar_vision          SMALLINT      CHECK (radar_vision BETWEEN 0 AND 100),
  -- summary.lane (3판 미만이면 전부 NULL) — H3-07
  lane_grade            VARCHAR(10)   CHECK (lane_grade IN ('과소평가', '제 실력', '박빙', '거품 주의')),
  lane_why              VARCHAR(100),
  lane_n                SMALLINT,
  lane_wins             SMALLINT,
  lane_win_rate         DECIMAL(5,4),
  lane_cs_diff          DECIMAL(6,1),
  lane_gold_diff        INTEGER,
  lane_level_diff       DECIMAL(5,2),
  lane_main_position    VARCHAR(8),
  lane_note             VARCHAR(200),
  -- summary.style 의 문장 (행은 review_style_row) — H5-03 · H5-04
  style_note            VARCHAR(200),
  style_caution         VARCHAR(200),
  CONSTRAINT pk_summoner_review PRIMARY KEY (review_key),
  CONSTRAINT uq_summoner_review_request UNIQUE (riot_id_key, count_n, start_n),
  CONSTRAINT fk_summoner_review_model FOREIGN KEY (model_version) REFERENCES model_version (version),
  CONSTRAINT ck_review_ttl CHECK (expires_at > fetched_at),
  CONSTRAINT ck_review_verdict_sum CHECK (n_ahead_win + n_comeback_win + n_thrown_loss + n_behind_loss = n_games),
  CONSTRAINT ck_review_next_start CHECK (next_start = start_n + count_n)
);
CREATE INDEX ix_summoner_review_expires ON summoner_review (expires_at);

-- ---------------------------------------------------------------------
-- 3.2 review_style_row — summary.style.rows (유형별 판수·승률, H5-02)
--     3판 미만 유형은 enough = FALSE 이고 승률을 내지 않는다 (BR-SUM-09).
-- ---------------------------------------------------------------------
CREATE TABLE review_style_row (
  review_key  VARCHAR(120)  NOT NULL,
  style_key   VARCHAR(10)   NOT NULL CHECK (style_key IN ('fight', 'farm', 'objective')),
  n_games     SMALLINT      NOT NULL CHECK (n_games >= 1),
  wins        SMALLINT      NOT NULL CHECK (wins >= 0),
  win_rate    DECIMAL(5,4)  NOT NULL,
  enough      BOOLEAN       NOT NULL,
  CONSTRAINT pk_review_style_row PRIMARY KEY (review_key, style_key),
  CONSTRAINT fk_review_style_review FOREIGN KEY (review_key) REFERENCES summoner_review (review_key) ON DELETE CASCADE,
  CONSTRAINT ck_review_style_enough CHECK (enough = (n_games >= 3)),
  CONSTRAINT ck_review_style_wins CHECK (wins <= n_games)
);

-- ---------------------------------------------------------------------
-- 3.3 reviewed_game — games[] 한 판 (SCR-H7 경기 행 · SCR-H8 상세의 원천)
--     피처 13개는 DIFF13 컬럼명 그대로. 확률·판정은 서버 값이며 화면은 계산하지 않는다 (BR-SUM-08).
-- ---------------------------------------------------------------------
CREATE TABLE reviewed_game (
  review_key                    VARCHAR(120)      NOT NULL,
  match_id                      VARCHAR(20)       NOT NULL,
  game_created_at               TIMESTAMP         NOT NULL,        -- 화면은 'MM.DD' 로 줄여 보인다 (played_at)
  my_champion                   VARCHAR(30)       NOT NULL,
  my_side                       VARCHAR(4)        NOT NULL CHECK (my_side IN ('블루', '레드')),
  duration_min                  SMALLINT          NOT NULL CHECK (duration_min >= 11),   -- BR-SUM-03
  game_version                  VARCHAR(8)        NOT NULL,
  FirstBlood                    INTEGER           NOT NULL CHECK (FirstBlood IN (0, 1)),
  KillsDiff                     INTEGER           NOT NULL,
  GoldDiff                      INTEGER           NOT NULL,
  ExpDiff                       INTEGER           NOT NULL,
  WardsPlacedDiff               INTEGER           NOT NULL,
  WardsDestroyedDiff            INTEGER           NOT NULL,
  AssistsDiff                   INTEGER           NOT NULL,
  DragonsDiff                   INTEGER           NOT NULL,
  HeraldsDiff                   INTEGER           NOT NULL,
  TowersDestroyedDiff           INTEGER           NOT NULL,
  AvgLevelDiff                  DOUBLE PRECISION  NOT NULL,
  TotalMinionsKilledDiff        INTEGER           NOT NULL,
  TotalJungleMinionsKilledDiff  INTEGER           NOT NULL,
  win_prob_blue                 DECIMAL(5,4)      NOT NULL CHECK (win_prob_blue BETWEEN 0 AND 1),
  pred                          SMALLINT          NOT NULL CHECK (pred IN (0, 1)),
  actual_blue_won               BOOLEAN           NOT NULL,        -- 화면 문자열 '블루 승리'/'레드 승리' 는 뷰가 만든다
  model_correct                 BOOLEAN           NOT NULL,
  band_name                     VARCHAR(30)       NOT NULL,
  my_win_prob                   DECIMAL(5,4)      NOT NULL CHECK (my_win_prob BETWEEN 0 AND 1),
  my_won                        BOOLEAN           NOT NULL,
  verdict_name                  VARCHAR(10)       NOT NULL,
  swing_minute                  SMALLINT          CHECK (swing_minute BETWEEN 0 AND 15),   -- NULL = 끝까지 접전
  -- style (격차 구성, 예측에 쓰지 않음 BR-SUM-10)
  style_key                     VARCHAR(10)       CHECK (style_key IN ('fight', 'farm', 'objective')),
  style_clear                   BOOLEAN,
  style_share_fight             DECIMAL(4,3),
  style_share_farm              DECIMAL(4,3),
  style_share_objective         DECIMAL(4,3),
  -- lane (같은 라인 상대와의 10분 비교, 포지션 미기록이면 전부 NULL)
  lane_position                 VARCHAR(8),
  lane_my_champion              VARCHAR(30),
  lane_my_cs                    SMALLINT,
  lane_my_gold                  INTEGER,
  lane_my_level                 SMALLINT,
  lane_opp_champion             VARCHAR(30),
  lane_opp_cs                   SMALLINT,
  lane_opp_gold                 INTEGER,
  lane_opp_level                SMALLINT,
  -- first_objectives (0~10분 첫 드래곤·전령·타워)
  first_dragon_minute           DECIMAL(4,1),
  first_dragon_side             VARCHAR(4)        CHECK (first_dragon_side IN ('블루', '레드')),
  first_herald_minute           DECIMAL(4,1),
  first_herald_side             VARCHAR(4)        CHECK (first_herald_side IN ('블루', '레드')),
  first_tower_minute            DECIMAL(4,1),
  first_tower_side              VARCHAR(4)        CHECK (first_tower_side IN ('블루', '레드')),
  warnings_text                 VARCHAR(1000),                     -- 학습 범위 밖 경고, 줄바꿈 구분 (BR-PRD-02)
  CONSTRAINT pk_reviewed_game PRIMARY KEY (review_key, match_id),
  CONSTRAINT fk_reviewed_game_review FOREIGN KEY (review_key) REFERENCES summoner_review (review_key) ON DELETE CASCADE,
  CONSTRAINT fk_reviewed_game_band FOREIGN KEY (band_name) REFERENCES gold_band (band_name),
  CONSTRAINT fk_reviewed_game_verdict FOREIGN KEY (verdict_name) REFERENCES verdict_type (verdict_name),
  -- serving.md §3 · §6: pred 규칙, 내 팀 기준 뒤집기, 적중 정의
  CONSTRAINT ck_reviewed_pred_rule CHECK (pred = CASE WHEN win_prob_blue >= 0.5 THEN 1 ELSE 0 END),
  CONSTRAINT ck_reviewed_my_prob CHECK (
      (my_side = '블루' AND ABS(my_win_prob - win_prob_blue) < 0.0001) OR
      (my_side = '레드' AND ABS(my_win_prob - (1 - win_prob_blue)) < 0.0001)),
  -- BR-SUM-01 네 갈래 판정을 행 안에서 검사 (verdict_type 코드표와 같은 규칙)
  CONSTRAINT ck_reviewed_verdict_rule CHECK (
      (my_win_prob >= 0.5 AND my_won = TRUE  AND verdict_name = '우세승') OR
      (my_win_prob >= 0.5 AND my_won = FALSE AND verdict_name = '역전패') OR
      (my_win_prob <  0.5 AND my_won = TRUE  AND verdict_name = '역전승') OR
      (my_win_prob <  0.5 AND my_won = FALSE AND verdict_name = '열세패')),
  CONSTRAINT ck_reviewed_my_won CHECK (
      (my_side = '블루' AND my_won = actual_blue_won) OR
      (my_side = '레드' AND my_won = NOT actual_blue_won)),
  CONSTRAINT ck_reviewed_correct CHECK (model_correct = ((pred = 1) = actual_blue_won))
);

-- ---------------------------------------------------------------------
-- 3.4 reviewed_game_factor — top_factors (정확히 5개, 기여도 절대값 내림차순) — SCR-H8 승리요인
-- ---------------------------------------------------------------------
CREATE TABLE reviewed_game_factor (
  review_key    VARCHAR(120)  NOT NULL,
  match_id      VARCHAR(20)   NOT NULL,
  rank_no       SMALLINT      NOT NULL CHECK (rank_no BETWEEN 1 AND 5),
  feature_name  VARCHAR(40)   NOT NULL,
  korean_name   VARCHAR(40)   NOT NULL,
  actual_value  DECIMAL(12,4) NOT NULL,                    -- 실제 격차 (BR-PRD-07: 숫자만 띄우지 않는다)
  contribution  DECIMAL(9,4)  NOT NULL,                    -- 양수 = 블루에 유리
  direction     VARCHAR(10)   NOT NULL CHECK (direction IN ('블루에 유리', '레드에 유리')),
  CONSTRAINT pk_reviewed_game_factor PRIMARY KEY (review_key, match_id, rank_no),
  CONSTRAINT fk_rgf_game FOREIGN KEY (review_key, match_id) REFERENCES reviewed_game (review_key, match_id) ON DELETE CASCADE,
  CONSTRAINT uq_rgf_feature UNIQUE (review_key, match_id, feature_name)
);

-- ---------------------------------------------------------------------
-- 3.5 reviewed_game_trajectory — 분 단위 골드차 궤적 (블루 − 레드, 0~15분) — SCR-H8 궤적 그래프
-- ---------------------------------------------------------------------
CREATE TABLE reviewed_game_trajectory (
  review_key  VARCHAR(120)  NOT NULL,
  match_id    VARCHAR(20)   NOT NULL,
  minute      SMALLINT      NOT NULL CHECK (minute BETWEEN 0 AND 15),
  gold_diff   INTEGER       NOT NULL,
  CONSTRAINT pk_reviewed_game_trajectory PRIMARY KEY (review_key, match_id, minute),
  CONSTRAINT fk_rgt_game FOREIGN KEY (review_key, match_id) REFERENCES reviewed_game (review_key, match_id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------
-- 3.6 reviewed_game_participant — roster 10명 (SCR-H8 팀 구성). puuid 는 UC4 티어 조회에만 쓰고
--     캐시 만료와 함께 지운다 (BR-SUM-07). 이름 클릭 → UC3 새 시작.
-- ---------------------------------------------------------------------
CREATE TABLE reviewed_game_participant (
  review_key      VARCHAR(120)  NOT NULL,
  match_id        VARCHAR(20)   NOT NULL,
  participant_no  SMALLINT      NOT NULL CHECK (participant_no BETWEEN 1 AND 10),
  side            VARCHAR(4)    NOT NULL CHECK (side IN ('블루', '레드')),
  game_name       VARCHAR(40)   NOT NULL,
  tag_line        VARCHAR(10)   NOT NULL,
  champion        VARCHAR(30)   NOT NULL,
  position        VARCHAR(8)    NOT NULL,
  kills           SMALLINT      NOT NULL CHECK (kills >= 0),
  deaths          SMALLINT      NOT NULL CHECK (deaths >= 0),
  assists         SMALLINT      NOT NULL CHECK (assists >= 0),
  is_me           BOOLEAN       NOT NULL,
  puuid           CHAR(78)      NOT NULL,                  -- 비공개. 만료 시 행째 삭제
  CONSTRAINT pk_reviewed_game_participant PRIMARY KEY (review_key, match_id, participant_no),
  CONSTRAINT fk_rgp_game FOREIGN KEY (review_key, match_id) REFERENCES reviewed_game (review_key, match_id) ON DELETE CASCADE,
  CONSTRAINT uq_rgp_champion UNIQUE (review_key, match_id, champion)
);


-- =====================================================================
-- §4. D. 비공개·운영 — 공개 덤프·커밋·API 응답에 절대 넣지 않는다 (BR-LDR-02 · BR-COL-06)
--     팀 DB 에서는 별도 데이터베이스(스키마)에 두고 서비스 계정에 SELECT 만 준다.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 4.1 ranking_name_cache — puuid → 공개 Riot ID (data/ranking_names.jsonl). 랭킹 수집 이어받기 전용.
-- ---------------------------------------------------------------------
CREATE TABLE ranking_name_cache (
  puuid       CHAR(78)     NOT NULL,
  game_name   VARCHAR(40)  NOT NULL,
  tag_line    VARCHAR(10)  NOT NULL,
  fetched_at  TIMESTAMP    NOT NULL,
  CONSTRAINT pk_ranking_name_cache PRIMARY KEY (puuid)
);

-- ---------------------------------------------------------------------
-- 4.2 participant_rank_cache — [추론] UC4 티어 조회 캐시 (현재는 프로세스 메모리 _RANK_CACHE)
--     429 로 못 받은 것은 저장하지 않는다(언랭으로 굳히지 않기 위해). 24시간 뒤 삭제.
-- ---------------------------------------------------------------------
CREATE TABLE participant_rank_cache (
  puuid       CHAR(78)     NOT NULL,
  is_ranked   BOOLEAN      NOT NULL,                  -- FALSE = 언랭 (진짜 없는 경우만)
  tier        VARCHAR(12),
  division    VARCHAR(4),
  lp          INTEGER,
  wins        INTEGER,
  losses      INTEGER,
  fetched_at  TIMESTAMP    NOT NULL,
  expires_at  TIMESTAMP    NOT NULL,
  CONSTRAINT pk_participant_rank_cache PRIMARY KEY (puuid),
  CONSTRAINT ck_prc_ranked CHECK ((is_ranked = TRUE AND tier IS NOT NULL) OR (is_ranked = FALSE AND tier IS NULL))
);

-- ---------------------------------------------------------------------
-- 4.3 service_health_log — [추론] /api/health 의 기록 (riot_ready · 파리티). 운영 확인용, 화면 없음 (UI_00 §7).
-- ---------------------------------------------------------------------
CREATE TABLE service_health_log (
  checked_at           TIMESTAMP     NOT NULL,
  started_at           TIMESTAMP     NOT NULL,
  model_version        VARCHAR(16)   NOT NULL,
  riot_key_present     BOOLEAN       NOT NULL,
  riot_ready           BOOLEAN       NOT NULL,       -- key_works() 결과 (UC14)
  parity_passed        BOOLEAN       NOT NULL,
  parity_max_abs_diff  DECIMAL(10,8) NOT NULL,
  CONSTRAINT pk_service_health_log PRIMARY KEY (checked_at),
  CONSTRAINT fk_health_model FOREIGN KEY (model_version) REFERENCES model_version (version)
);
