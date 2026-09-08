-- =====================================================================
-- DB_00 — LoL 인게임 시점별 승패 예측·핵심 승리요인 분석 서비스 데이터 저장 구조
-- 4/5 권한 — 역할 4개와 표별 GRANT (표준 SQL:1999 역할 문법. MariaDB 10.0.5+ · PostgreSQL 에서 실행)
--
-- 문서: docs/usecases/DB_00_데이터베이스구조설계.md §3 (공개 여부) · §8.4 적용 절차
-- SQLite 는 GRANT 가 없다 — 파일 권한(chmod 600)과 별도 파일 분리(비공개 영역 D 는 다른 파일)로 대신한다.
--
-- 역할 (누가 무엇을 하는가 — BP_00 §1 레인과 같다)
--   lolex_service   웹 서버(web/app.py). 읽기 + 투표·복기 캐시·운영 기록 쓰기. 비공개 영역은 SELECT 만
--   lolex_collector 수집기(src/collect_*.py, UC9·UC10). 스냅샷 영역 B 와 이름 캐시 쓰기
--   lolex_trainer   모델 재학습(src/finalize_model.py, UC11·UC12). 학습·모델·평가 영역 A 쓰기
--   lolex_analyst   팀원 분석·노트북. 공개 영역 SELECT 만. 비공개 영역·복기 캐시는 보이지 않는다
--
-- 원칙 (P-3): puuid 를 담는 표(ranking_name_cache · participant_rank_cache · reviewed_game_participant)와
--   Riot ID 를 담는 복기 캐시는 분석 역할에 주지 않는다. 덤프·백업 계정도 이 표들을 제외한다.
-- =====================================================================

CREATE ROLE lolex_service;
CREATE ROLE lolex_collector;
CREATE ROLE lolex_trainer;
CREATE ROLE lolex_analyst;

-- ---------------------------------------------------------------------
-- A. 학습·모델·평가 — trainer 가 쓰고, 나머지는 읽는다
-- ---------------------------------------------------------------------
GRANT SELECT ON lol_matches_10min      TO lolex_service, lolex_trainer, lolex_analyst;
GRANT SELECT ON ml_split               TO lolex_service, lolex_trainer, lolex_analyst;
GRANT SELECT ON lol_analysis_10min     TO lolex_service, lolex_trainer, lolex_analyst;
GRANT SELECT ON gold_band              TO lolex_service, lolex_collector, lolex_trainer, lolex_analyst;
GRANT SELECT ON verdict_type           TO lolex_service, lolex_collector, lolex_trainer, lolex_analyst;

GRANT SELECT, INSERT, UPDATE ON model_version        TO lolex_trainer;
GRANT SELECT, INSERT         ON model_feature        TO lolex_trainer;
GRANT SELECT, INSERT         ON training_run         TO lolex_trainer;
GRANT SELECT, INSERT, DELETE ON golden_case          TO lolex_trainer;
GRANT SELECT, INSERT, DELETE ON golden_case_factor   TO lolex_trainer;
GRANT SELECT, INSERT, DELETE ON eval_seed_run        TO lolex_trainer;
GRANT SELECT, INSERT, DELETE ON eval_timepoint_seed  TO lolex_trainer;
GRANT SELECT, INSERT, DELETE ON eval_gold_band       TO lolex_trainer;
GRANT SELECT, INSERT, DELETE ON eval_coef_shift      TO lolex_trainer;
GRANT SELECT, INSERT, DELETE ON eval_tier_accuracy   TO lolex_trainer;
GRANT SELECT, INSERT, DELETE ON match_type_profile   TO lolex_trainer;

GRANT SELECT ON model_version        TO lolex_service, lolex_analyst;
GRANT SELECT ON model_feature        TO lolex_service, lolex_analyst;
GRANT SELECT ON training_run         TO lolex_analyst;
GRANT SELECT ON golden_case          TO lolex_service, lolex_analyst;   -- UC12 회귀 검사는 서비스 계정으로 돈다
GRANT SELECT ON golden_case_factor   TO lolex_service, lolex_analyst;
GRANT SELECT ON eval_seed_run        TO lolex_service, lolex_analyst;
GRANT SELECT ON eval_timepoint_seed  TO lolex_service, lolex_analyst;
GRANT SELECT ON eval_gold_band       TO lolex_service, lolex_analyst;
GRANT SELECT ON eval_coef_shift      TO lolex_service, lolex_analyst;
GRANT SELECT ON eval_tier_accuracy   TO lolex_service, lolex_analyst;
GRANT SELECT ON match_type_profile   TO lolex_service, lolex_analyst;

-- ---------------------------------------------------------------------
-- B. 스냅샷·투표 — collector 가 스냅샷을 쓰고, service 가 표를 쓴다
-- ---------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE ON snapshot_run                TO lolex_collector;
GRANT SELECT, INSERT, UPDATE ON pro_match                   TO lolex_collector;   -- 덮어쓰기는 UPDATE(snapshot_id 이동), DELETE 없음
GRANT SELECT, INSERT         ON ranking_entry               TO lolex_collector;
GRANT SELECT, INSERT         ON champion_match_participant  TO lolex_collector;
GRANT SELECT, INSERT         ON champion_lane_stat          TO lolex_collector;
GRANT SELECT, INSERT         ON champion_top_player         TO lolex_collector;

GRANT SELECT                 ON snapshot_run                TO lolex_service, lolex_analyst;
GRANT SELECT                 ON pro_match                   TO lolex_service, lolex_analyst;
GRANT SELECT                 ON ranking_entry               TO lolex_service, lolex_analyst;
GRANT SELECT                 ON champion_lane_stat          TO lolex_service, lolex_analyst;
GRANT SELECT                 ON champion_top_player         TO lolex_service, lolex_analyst;
GRANT SELECT                 ON champion_match_participant  TO lolex_analyst;    -- 원본은 분석용, 서비스는 읽지 않는다(BR-COL-02)

GRANT SELECT, INSERT, UPDATE ON votes                       TO lolex_service;    -- 재투표 = UPDATE (BR-VOT-04), DELETE 없음
GRANT SELECT                 ON votes                       TO lolex_analyst;    -- voter 는 브라우저 식별자라 개인정보가 아니다(BR-VOT-05)

-- ---------------------------------------------------------------------
-- C. 복기 캐시 (5분 보존, Riot ID·puuid 포함) — service 만. 분석 역할에 주지 않는다 (BR-SUM-07)
-- ---------------------------------------------------------------------
GRANT SELECT, INSERT, DELETE ON summoner_review            TO lolex_service;
GRANT SELECT, INSERT, DELETE ON review_style_row           TO lolex_service;
GRANT SELECT, INSERT, DELETE ON reviewed_game              TO lolex_service;
GRANT SELECT, INSERT, DELETE ON reviewed_game_factor       TO lolex_service;
GRANT SELECT, INSERT, DELETE ON reviewed_game_trajectory   TO lolex_service;
GRANT SELECT, INSERT, DELETE ON reviewed_game_participant  TO lolex_service;

-- ---------------------------------------------------------------------
-- D. 비공개·운영 — puuid 표는 만든 역할만 쓰고, 다른 역할에는 주지 않는다 (BR-LDR-02 · BR-COL-06)
-- ---------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE         ON ranking_name_cache      TO lolex_collector;
GRANT SELECT, INSERT, UPDATE, DELETE ON participant_rank_cache  TO lolex_service;
GRANT SELECT, INSERT                 ON service_health_log      TO lolex_service;
GRANT SELECT                         ON service_health_log      TO lolex_analyst;

-- ---------------------------------------------------------------------
-- V. 뷰 — 서비스·분석 역할은 뷰로 읽는다 (표 직접 조회 대신)
-- ---------------------------------------------------------------------
GRANT SELECT ON v_base                    TO lolex_trainer, lolex_analyst;
GRANT SELECT ON v_diff13_all              TO lolex_trainer, lolex_analyst;
GRANT SELECT ON v_diff13_train            TO lolex_trainer, lolex_analyst;
GRANT SELECT ON v_diff13_test             TO lolex_trainer, lolex_analyst;
GRANT SELECT ON v_current_snapshot        TO lolex_service, lolex_collector, lolex_analyst;
GRANT SELECT ON v_schedule_tally          TO lolex_service, lolex_analyst;
GRANT SELECT ON v_ranking_page            TO lolex_service, lolex_analyst;
GRANT SELECT ON v_champion_lane_serving   TO lolex_service, lolex_analyst;
GRANT SELECT ON v_review_tiles            TO lolex_service;
GRANT SELECT ON v_reviewed_game_row       TO lolex_service;

-- ---------------------------------------------------------------------
-- 사용자에게 역할 부여 (계정 이름은 서버마다 다르므로 자리표시자)
-- ---------------------------------------------------------------------
-- GRANT lolex_service   TO 'lolex_web'@'%';        -- .env 의 DB_USER (web/app.py)
-- GRANT lolex_collector TO 'lolex_batch'@'%';      -- scripts/run_collectors.sh
-- GRANT lolex_trainer   TO 'lolex_train'@'%';      -- src/finalize_model.py
-- GRANT lolex_analyst   TO 'team_member'@'%';      -- 팀원 노트북
