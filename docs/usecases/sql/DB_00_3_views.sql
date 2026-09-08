-- =====================================================================
-- DB_00 — LoL 인게임 시점별 승패 예측·핵심 승리요인 분석 서비스 데이터 저장 구조
-- 3/5 서빙 뷰 10 — 화면이 계산하지 않도록 DB 가 주는 것 (SCR-00 R-04 · SCR-H I-04)
--
-- 문서: docs/usecases/DB_00_데이터베이스구조설계.md
-- 실행 순서: 1_ddl → 2_seed → 3_views → 4_grants(MariaDB·PostgreSQL 만) → 5_queries 는 필요할 때
--   MariaDB : mysql -h <서버> -P <포트> -u <사용자> -p <팀DB명> < docs/usecases/sql/DB_00_1_ddl.sql
--   SQLite  : sqlite3 lolex.sqlite3 ".read docs/usecases/sql/DB_00_1_ddl.sql"   (PRAGMA foreign_keys = ON 필요)
-- 표기: ISO/IEC 9075 SQL:2016 의 벤더 중립 부분집합 — IDENTITY·JSON 형·ENGINE·COMMENT·ON DUPLICATE KEY 를 쓰지 않는다.
-- =====================================================================


-- 5.1 기존 ML 뷰 (db/create_ml_views.sql 그대로 — 정본은 lolwin/features.py, 여기서는 참조만)
CREATE VIEW v_base AS
SELECT m.*, s.split
FROM lol_matches_10min m
JOIN ml_split s ON m.gameId = s.gameId;

CREATE VIEW v_diff13_all AS
SELECT
    gameId,
    blueWins                                                    AS y,
    blueFirstBlood                                              AS FirstBlood,
    blueKills                    - redKills                     AS KillsDiff,
    blueGoldDiff                                                AS GoldDiff,
    blueExperienceDiff                                          AS ExpDiff,
    blueWardsPlaced              - redWardsPlaced               AS WardsPlacedDiff,
    blueWardsDestroyed           - redWardsDestroyed            AS WardsDestroyedDiff,
    blueAssists                  - redAssists                   AS AssistsDiff,
    blueDragons                  - redDragons                   AS DragonsDiff,
    blueHeralds                  - redHeralds                   AS HeraldsDiff,
    blueTowersDestroyed          - redTowersDestroyed           AS TowersDestroyedDiff,
    blueAvgLevel                 - redAvgLevel                  AS AvgLevelDiff,
    blueTotalMinionsKilled       - redTotalMinionsKilled        AS TotalMinionsKilledDiff,
    blueTotalJungleMinionsKilled - redTotalJungleMinionsKilled  AS TotalJungleMinionsKilledDiff,
    split
FROM v_base;

CREATE VIEW v_diff13_train AS SELECT * FROM v_diff13_all WHERE split = 'train';
CREATE VIEW v_diff13_test  AS SELECT * FROM v_diff13_all WHERE split = 'test';

-- 5.2 v_current_snapshot — kind 별 현재 스냅샷 (갱신 시각·건수 = 메타 줄 CMP-23)
CREATE VIEW v_current_snapshot AS
SELECT kind, snapshot_id, collected_at, item_count, description, period_from, period_to, patches
FROM snapshot_run
WHERE is_current = TRUE;

-- 5.3 v_schedule_tally — /api/schedule (SCR-V1): 현재 스냅샷 + 표 집계. 시작 여부는 소비 측이 start_at 로 판정 (BR-VOT-03)
CREATE VIEW v_schedule_tally AS
SELECT p.match_id, p.start_at, p.league, p.block, p.bo,
       p.team1_name, p.team1_code, p.team1_img,
       p.team2_name, p.team2_code, p.team2_img, p.priority,
       (SELECT COUNT(*) FROM votes v WHERE v.match_id = p.match_id AND v.pick = 1) AS votes1,
       (SELECT COUNT(*) FROM votes v WHERE v.match_id = p.match_id AND v.pick = 2) AS votes2
FROM pro_match p
JOIN v_current_snapshot s ON s.kind = 'schedule' AND s.snapshot_id = p.snapshot_id;

-- 5.4 v_ranking_page — /api/ranking (SCR-R1): 승률은 서버가 계산, 전적 0 이면 NULL (BR-LDR-07)
CREATE VIEW v_ranking_page AS
SELECT r.rank_no, r.tier, r.game_name, r.tag_line, r.lp, r.wins, r.losses,
       CASE WHEN r.wins + r.losses = 0 THEN NULL
            ELSE CAST(r.wins AS DOUBLE PRECISION) / (r.wins + r.losses) END AS win_rate,
       ((r.rank_no - 1) / 100) + 1 AS page_no
FROM ranking_entry r
JOIN v_current_snapshot s ON s.kind = 'ranking' AND s.snapshot_id = r.snapshot_id;

-- 5.5 v_champion_lane_serving — /api/champions (SCR-C1): 10판 미만은 low_sample, "표본 충분 먼저 · 승률 내림차순" (BR-CHMP-02)
CREATE VIEW v_champion_lane_serving AS
SELECT c.champion, c.position, c.n_games, c.win_rate, c.pick_rate,
       CASE WHEN c.n_games < 10 THEN TRUE ELSE FALSE END AS low_sample,
       (SELECT COUNT(*) FROM champion_top_player t
         WHERE t.snapshot_id = c.snapshot_id AND t.champion = c.champion AND t.position = c.position) AS n_top_players
FROM champion_lane_stat c
JOIN v_current_snapshot s ON s.kind = 'champion' AND s.snapshot_id = c.snapshot_id;

-- 5.6 v_review_tiles — SCR-H4 타일 5개의 파생값 (H4-05 규칙을 DB 가 대신 계산)
CREATE VIEW v_review_tiles AS
SELECT review_key, riot_id_key, n_games, avg_win_prob_10min,
       n_ahead_win + n_thrown_loss                                   AS n_ahead,
       n_comeback_win + n_behind_loss                                AS n_behind,
       CASE WHEN n_games = 0 THEN NULL
            ELSE CAST(n_ahead_win + n_thrown_loss AS DOUBLE PRECISION) / n_games END              AS lead_rate,
       CASE WHEN n_ahead_win + n_thrown_loss = 0 THEN NULL
            ELSE CAST(n_ahead_win AS DOUBLE PRECISION) / (n_ahead_win + n_thrown_loss) END        AS close_rate,
       CASE WHEN n_comeback_win + n_behind_loss = 0 THEN NULL
            ELSE CAST(n_comeback_win AS DOUBLE PRECISION) / (n_comeback_win + n_behind_loss) END  AS save_rate,
       n_thrown_loss                                                 AS n_thrown,
       CASE WHEN n_games = 0 THEN NULL
            ELSE CAST(n_model_correct AS DOUBLE PRECISION) / n_games END                          AS hit_rate,
       expires_at
FROM summoner_review;

-- 5.7 v_reviewed_game_row — SCR-H7 경기 행 (actual 문자열은 여기서 만든다, serving.md "actual 은 문자열")
CREATE VIEW v_reviewed_game_row AS
SELECT g.review_key, g.match_id, g.game_created_at, g.my_champion, g.my_side, g.duration_min, g.game_version,
       g.win_prob_blue, g.pred,
       CASE WHEN g.actual_blue_won THEN '블루 승리' ELSE '레드 승리' END AS actual,
       g.model_correct, g.band_name, b.ordinal AS band_ordinal,
       g.my_win_prob, g.my_won, g.verdict_name, v.ordinal AS verdict_ordinal,
       g.swing_minute, g.style_key, g.style_clear, g.warnings_text
FROM reviewed_game g
JOIN gold_band b    ON b.band_name = g.band_name
JOIN verdict_type v ON v.verdict_name = g.verdict_name;
