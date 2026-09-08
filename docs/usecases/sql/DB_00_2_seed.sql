-- =====================================================================
-- DB_00 — LoL 인게임 시점별 승패 예측·핵심 승리요인 분석 서비스 데이터 저장 구조
-- 2/5 코드표 초기 데이터 — gold_band 4행 · verdict_type 4행 (DDL 직후 1회)
--
-- 문서: docs/usecases/DB_00_데이터베이스구조설계.md
-- 실행 순서: 1_ddl → 2_seed → 3_views → 4_grants(MariaDB·PostgreSQL 만) → 5_queries 는 필요할 때
--   MariaDB : mysql -h <서버> -P <포트> -u <사용자> -p <팀DB명> < docs/usecases/sql/DB_00_1_ddl.sql
--   SQLite  : sqlite3 lolex.sqlite3 ".read docs/usecases/sql/DB_00_1_ddl.sql"   (PRAGMA foreign_keys = ON 필요)
-- 표기: ISO/IEC 9075 SQL:2016 의 벤더 중립 부분집합 — IDENTITY·JSON 형·ENGINE·COMMENT·ON DUPLICATE KEY 를 쓰지 않는다.
-- =====================================================================
--
-- gold_band  : 이름·순서만. 경계 숫자의 정본은 lolwin/features.py GOLD_BINS (BR-RPT-02)
-- verdict_type: BR-SUM-01 네 갈래 판정 = lolwin/coach.py verdict_of 와 같은 규칙
-- =====================================================================

INSERT INTO gold_band VALUES ('접전(<1k)', 1);
INSERT INTO gold_band VALUES ('우세(1k~2.5k)', 2);
INSERT INTO gold_band VALUES ('크게 우세(2.5k~4.2k)', 3);
INSERT INTO gold_band VALUES ('사실상 결정(4.2k+)', 4);
INSERT INTO verdict_type VALUES ('우세승', TRUE,  TRUE,  1);
INSERT INTO verdict_type VALUES ('역전승', FALSE, TRUE,  2);
INSERT INTO verdict_type VALUES ('역전패', TRUE,  FALSE, 3);
INSERT INTO verdict_type VALUES ('열세패', FALSE, FALSE, 4);

