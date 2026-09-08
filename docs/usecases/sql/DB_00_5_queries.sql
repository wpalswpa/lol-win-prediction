-- =====================================================================
-- DB_00 — LoL 인게임 시점별 승패 예측·핵심 승리요인 분석 서비스 데이터 저장 구조
-- 5/5 조회·운영 SQL — API 엔드포인트·화면별 조회문과 수집기·정리 작업 (표준 SQL, 실행은 필요할 때)
--
-- 문서: docs/usecases/DB_00_데이터베이스구조설계.md §5 · §6
-- 자리표시자: :riot_id_key · :count_n · :start_n · :match_id · :voter · :pick · :page · :position · :snapshot_id · :version · :now
--   (표준 SQL 호스트 변수 표기. 드라이버에 맞게 ? 또는 %s 로 바꾼다)
-- 각 문장은 어느 UC·화면·API 를 위한 것인지 앞에 적었다. 순서는 UI_00 화면목록의 화면 ID 순.
-- =====================================================================


-- =====================================================================
-- Q-1. 공통 프레임 · 운영 (SCR-00 · UC13 · UC14)
-- =====================================================================

-- Q-1.1 현재 활성 모델 (GET /api/health · /api/schema 머리)
SELECT version, model_name, time_point_min, trained_at, holdout_accuracy, sklearn_version
FROM model_version
WHERE is_active = TRUE;

-- Q-1.2 입력 폼 범위 (GET /api/schema → SCR-T2 13칸) — DIFF13 순서
SELECT f.ordinal, f.feature_name, f.korean_name, f.data_type, f.train_min, f.train_max, f.train_mean
FROM model_feature f
JOIN model_version m ON m.version = f.version AND m.is_active = TRUE
ORDER BY f.ordinal;

-- Q-1.3 최근 기동 상태 (운영 확인, 화면 없음 · UI_00 §7)
SELECT checked_at, riot_ready, parity_passed, parity_max_abs_diff, model_version
FROM service_health_log
ORDER BY checked_at DESC;


-- =====================================================================
-- Q-2. 유저 전적 분석 — 복기 캐시 (UC3 · UC4 · SCR-H2 ~ SCR-H8)
-- =====================================================================

-- Q-2.1 캐시 적중 확인 (BR-SUM-05: 있고 만료 전이면 cached = true)
SELECT review_key, fetched_at, expires_at
FROM summoner_review
WHERE riot_id_key = :riot_id_key AND count_n = :count_n AND start_n = :start_n
  AND expires_at > :now;

-- Q-2.2 프로필 카드 (SCR-H3: H3-01 ~ H3-08) + COACH 요약 건수 (SCR-H4: H4-01 ~ H4-03)
SELECT r.riot_id_key, r.rank_tier, r.rank_division, r.rank_lp, r.rank_wins, r.rank_losses,
       r.n_games, r.avg_win_prob_10min, r.n_model_correct,
       r.n_ahead_win, r.n_comeback_win, r.n_thrown_loss, r.n_behind_loss,
       r.radar_label, r.radar_desc, r.radar_fight, r.radar_lane, r.radar_jungle, r.radar_objective, r.radar_vision,
       r.lane_grade, r.lane_why, r.lane_n, r.lane_wins, r.lane_cs_diff, r.lane_gold_diff, r.lane_note,
       (SELECT MIN(g.game_created_at) FROM reviewed_game g WHERE g.review_key = r.review_key) AS period_from,
       (SELECT MAX(g.game_created_at) FROM reviewed_game g WHERE g.review_key = r.review_key) AS period_to,
       (SELECT COUNT(*) FROM reviewed_game g WHERE g.review_key = r.review_key AND g.band_name = '접전(<1k)') AS n_close
FROM summoner_review r
WHERE r.review_key = :review_key;

-- Q-2.3 판정 타일 5개 (SCR-H4: H4-04, 파생은 뷰가 한다 — H4-05)
SELECT n_games, avg_win_prob_10min, lead_rate, close_rate, save_rate, n_thrown, hit_rate
FROM v_review_tiles
WHERE review_key = :review_key;

-- Q-2.4 플레이 성향 행 (SCR-H5: H5-02) — 판수 내림차순, 3판 미만은 enough = FALSE
SELECT style_key, n_games, wins, win_rate, enough
FROM review_style_row
WHERE review_key = :review_key
ORDER BY n_games DESC;

-- Q-2.5 경기 행 목록 (SCR-H7) — 최신 판부터
SELECT match_id, game_created_at, my_champion, my_side, duration_min, game_version,
       my_win_prob, my_won, verdict_name, verdict_ordinal, band_name, band_ordinal,
       actual, model_correct, swing_minute, style_key, warnings_text
FROM v_reviewed_game_row
WHERE review_key = :review_key
ORDER BY game_created_at DESC;

-- Q-2.6 경기 상세 — 승리요인 5 (SCR-H8, BR-PRD-07: 실제 격차와 방향을 함께)
SELECT rank_no, feature_name, korean_name, actual_value, contribution, direction
FROM reviewed_game_factor
WHERE review_key = :review_key AND match_id = :match_id
ORDER BY rank_no;

-- Q-2.7 경기 상세 — 골드차 궤적 0~15분 (SCR-H8)
SELECT minute, gold_diff
FROM reviewed_game_trajectory
WHERE review_key = :review_key AND match_id = :match_id
ORDER BY minute;

-- Q-2.8 경기 상세 — 팀 구성 10명 + 캐시된 티어 (SCR-H8 · UC4, 펼칠 때만)
SELECT p.participant_no, p.side, p.game_name, p.tag_line, p.champion, p.position,
       p.kills, p.deaths, p.assists, p.is_me,
       c.is_ranked, c.tier, c.division, c.lp
FROM reviewed_game_participant p
LEFT JOIN participant_rank_cache c ON c.puuid = p.puuid AND c.expires_at > :now
WHERE p.review_key = :review_key AND p.match_id = :match_id
ORDER BY p.side, p.participant_no;

-- Q-2.9 티어 조회가 필요한 puuid 만 (UC4: 캐시에 없거나 만료된 것 → Riot 호출 대상)
SELECT p.puuid
FROM reviewed_game_participant p
LEFT JOIN participant_rank_cache c ON c.puuid = p.puuid AND c.expires_at > :now
WHERE p.review_key = :review_key AND p.match_id = :match_id AND c.puuid IS NULL;

-- Q-2.10 여러 페이지 합산 성향 (SCR-H5: H5-05 — 더 보기 뒤 같은 소환사의 캐시 행 전부)
SELECT g.style_key, COUNT(*) AS n_games, SUM(CASE WHEN g.my_won THEN 1 ELSE 0 END) AS wins,
       CAST(SUM(CASE WHEN g.my_won THEN 1 ELSE 0 END) AS DOUBLE PRECISION) / COUNT(*) AS win_rate,
       CASE WHEN COUNT(*) >= 3 THEN TRUE ELSE FALSE END AS enough
FROM reviewed_game g
JOIN summoner_review r ON r.review_key = g.review_key
WHERE r.riot_id_key = :riot_id_key AND r.expires_at > :now AND g.style_key IS NOT NULL
GROUP BY g.style_key
ORDER BY n_games DESC;


-- =====================================================================
-- Q-3. 이 판정을 믿어도 되나 (UC5 · SCR-T1 · GET /api/report · /api/match-types)
-- =====================================================================

-- Q-3.1 성능 + 반복 실험 평균·표준편차 (BR-RPT-03: 기준선·반복을 항상 함께)
SELECT m.version, m.holdout_accuracy, m.holdout_f1, m.holdout_auc, m.holdout_brier, m.baseline_accuracy,
       m.cv_accuracy, m.cv_std,
       (SELECT COUNT(*)       FROM eval_seed_run e WHERE e.version = m.version) AS repeat_n,
       (SELECT AVG(accuracy)  FROM eval_seed_run e WHERE e.version = m.version) AS repeat_mean
FROM model_version m
WHERE m.is_active = TRUE;

-- Q-3.2 구간별 정확도와 가장 약한 구간 (BR-RPT-04: 틀린 판을 숨기지 않는다) — 실험 A
SELECT e.band_label, e.band_name, b.ordinal, e.n_games, e.accuracy_10min, e.share_pct
FROM eval_gold_band e
LEFT JOIN gold_band b ON b.band_name = e.band_name
JOIN model_version m ON m.version = e.version AND m.is_active = TRUE
WHERE e.experiment = 'A'
ORDER BY b.ordinal;

SELECT band_label AS weakest_bin
FROM eval_gold_band e
JOIN model_version m ON m.version = e.version AND m.is_active = TRUE
WHERE e.experiment = 'A'
ORDER BY e.accuracy_10min ASC
FETCH FIRST 1 ROWS ONLY;

-- Q-3.3 승리요인 순위 (win_factor_ranking)
SELECT feature_name, korean_name, coef_std, coef_rank, perm_importance, perm_rank
FROM model_feature f
JOIN model_version m ON m.version = f.version AND m.is_active = TRUE
WHERE coef_rank IS NOT NULL
ORDER BY coef_rank;

-- Q-3.4 실험 B — 10분 vs 15분 (BR-TRN-11: 같은 경기·분할 안에서만)
SELECT e.band_label, e.n_games, e.accuracy_10min, e.accuracy_15min, e.gain, e.share_pct
FROM eval_gold_band e
JOIN model_version m ON m.version = e.version AND m.is_active = TRUE
WHERE e.experiment = 'B';

SELECT t.seed, t.accuracy_10min, t.accuracy_15min, t.gain, t.close_acc_10min, t.close_acc_15min, t.close_gain
FROM eval_timepoint_seed t
JOIN model_version m ON m.version = t.version AND m.is_active = TRUE
ORDER BY t.seed;

SELECT feature_label, feature_name, coef_10min, coef_15min, delta
FROM eval_coef_shift c
JOIN model_version m ON m.version = c.version AND m.is_active = TRUE
ORDER BY ABS(delta) DESC;

-- Q-3.5 경기 유형 4가지 (GET /api/match-types, BR-RPT-05 표시용)
SELECT cluster_no, type_label, n_games, share_pct, lead_team_win_rate,
       one_sided_gold, total_kills, total_objects, total_wards, total_cs
FROM match_type_profile p
JOIN model_version m ON m.version = p.version AND m.is_active = TRUE
ORDER BY n_games DESC;

-- Q-3.6 티어 일반화 (model_card 금지 2 의 근거)
SELECT tier, n_games, accuracy, avg_gold_diff, big_gap_share, std_error
FROM eval_tier_accuracy t
JOIN model_version m ON m.version = t.version AND m.is_active = TRUE
ORDER BY accuracy;


-- =====================================================================
-- Q-4. 최근 챔피언 승률 (UC6 · SCR-C1 ~ C3 · GET /api/champions?position=)
-- =====================================================================

-- Q-4.1 메타 줄 (CMP-23: 갱신 시각 · 수집 경기 수 · 기간 · 패치)
SELECT collected_at, item_count, description, period_from, period_to, patches
FROM v_current_snapshot
WHERE kind = 'champion';

-- Q-4.2 승률표 — 표본 충분 먼저, 승률 내림차순 (BR-CHMP-02). :position 이 NULL 이면 전체
SELECT champion, position, n_games, win_rate, pick_rate, low_sample, n_top_players
FROM v_champion_lane_serving
WHERE :position IS NULL OR position = :position
ORDER BY low_sample, win_rate DESC, n_games DESC;

-- Q-4.3 잘하는 유저 카드 (SCR-C2, 조합당 최대 5명 — BR-CHMP-03)
SELECT t.game_name, t.tag_line, t.n_games, t.win_rate
FROM champion_top_player t
JOIN v_current_snapshot s ON s.kind = 'champion' AND s.snapshot_id = t.snapshot_id
WHERE t.champion = :champion AND t.position = :position
ORDER BY t.win_rate DESC, t.n_games DESC
FETCH FIRST 5 ROWS ONLY;


-- =====================================================================
-- Q-5. 유저 랭킹 (UC7 · SCR-R1 ~ R2 · GET /api/ranking?page=)
-- =====================================================================

-- Q-5.1 메타 줄 + 이름 수집 중 여부 (UC7 A4: status = 'partial')
SELECT collected_at, item_count, description,
       (SELECT status FROM snapshot_run r WHERE r.snapshot_id = s.snapshot_id) AS status
FROM v_current_snapshot s
WHERE kind = 'ranking';

-- Q-5.2 페이지 조회 — 100명씩 1~10 (BR-LDR-03), 승률은 뷰가 계산 (BR-LDR-07)
SELECT rank_no, tier, game_name, tag_line, lp, wins, losses, win_rate
FROM v_ranking_page
WHERE page_no = :page
ORDER BY rank_no;


-- =====================================================================
-- Q-6. 승부예측 (UC8 · SCR-V1 ~ V2 · GET /api/schedule · POST /api/vote)
-- =====================================================================

-- Q-6.1 예정 경기 + 표 집계 + 내 선택 (BR-VOT-03: 시작한 경기는 제외, BR-SCH-03: 리그 우선순위 → 시작 시각)
SELECT t.match_id, t.start_at, t.league, t.block, t.bo,
       t.team1_name, t.team1_code, t.team1_img, t.team2_name, t.team2_code, t.team2_img,
       t.votes1, t.votes2,
       (SELECT v.pick FROM votes v WHERE v.match_id = t.match_id AND v.voter = :voter) AS my_pick
FROM v_schedule_tally t
WHERE t.start_at > :now
ORDER BY t.priority, t.start_at;

-- Q-6.2 투표 가능 여부 (404: 목록에 없음 / 409: 이미 시작)
SELECT p.match_id, p.start_at,
       CASE WHEN p.start_at <= :now THEN TRUE ELSE FALSE END AS started
FROM pro_match p
JOIN v_current_snapshot s ON s.kind = 'schedule' AND s.snapshot_id = p.snapshot_id
WHERE p.match_id = :match_id;

-- Q-6.3 표 저장 — 한 경기 한 표, 다시 누르면 바꾼다 (BR-VOT-04). 표준 SQL: INSERT 가 PK 충돌로 실패하면 UPDATE
INSERT INTO votes (match_id, voter, pick, voted_at) VALUES (:match_id, :voter, :pick, :now);
UPDATE votes SET pick = :pick, voted_at = :now WHERE match_id = :match_id AND voter = :voter;

-- Q-6.4 저장 직후 집계 (응답 votes1 · votes2)
SELECT SUM(CASE WHEN pick = 1 THEN 1 ELSE 0 END) AS votes1,
       SUM(CASE WHEN pick = 2 THEN 1 ELSE 0 END) AS votes2
FROM votes
WHERE match_id = :match_id;


-- =====================================================================
-- Q-7. 수집기 (UC9 · UC10) — 스냅샷 실행 등록과 현재 스냅샷 전환
-- =====================================================================

-- Q-7.1 새 실행 등록 (snapshot_id = 수집 시작 UTC epoch 초, status 는 끝나면 'complete')
INSERT INTO snapshot_run (snapshot_id, kind, collected_at, item_count, description, period_from, period_to, patches, status, is_current)
VALUES (:snapshot_id, :kind, :collected_at, :item_count, :description, :period_from, :period_to, :patches, 'partial', FALSE);

-- Q-7.2 일정 덮어쓰기 (BR-SCH-05) — 행을 지우지 않고 최신 실행으로 옮긴다 (votes FK 보호)
UPDATE pro_match SET snapshot_id = :snapshot_id, start_at = :start_at, league = :league, block = :block, bo = :bo,
       team1_name = :team1_name, team1_code = :team1_code, team1_img = :team1_img,
       team2_name = :team2_name, team2_code = :team2_code, team2_img = :team2_img, priority = :priority
WHERE match_id = :match_id;
-- (갱신된 행이 0이면 INSERT INTO pro_match (...) VALUES (...))

-- Q-7.3 랭킹 축소 덮어쓰기 금지 (BR-COL-10) — 새 실행의 행 수가 현재보다 적으면 전환하지 않는다
SELECT (SELECT COUNT(*) FROM ranking_entry WHERE snapshot_id = :snapshot_id) AS new_rows,
       (SELECT COUNT(*) FROM ranking_entry r JOIN v_current_snapshot s
          ON s.kind = 'ranking' AND s.snapshot_id = r.snapshot_id)            AS current_rows;

-- Q-7.4 현재 스냅샷 전환 (한 트랜잭션 안에서 두 문장)
UPDATE snapshot_run SET is_current = FALSE WHERE kind = :kind AND is_current = TRUE;
UPDATE snapshot_run SET is_current = TRUE, status = 'complete' WHERE snapshot_id = :snapshot_id;

-- Q-7.5 챔피언 이어받기 — 이미 받은 경기인가 (BR-COL-01)
SELECT 1 FROM champion_match_participant WHERE match_id = :match_id
FETCH FIRST 1 ROWS ONLY;

-- Q-7.6 챔피언 × 라인 재집계 (src/collect_champion_stats.py aggregate 와 같은 규칙: 5판 이상, 라인 5종)
SELECT champion, position, COUNT(*) AS n_games,
       CAST(SUM(CASE WHEN win THEN 1 ELSE 0 END) AS DOUBLE PRECISION) / COUNT(*) AS win_rate
FROM champion_match_participant
WHERE position IN ('TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY')
GROUP BY champion, position
HAVING COUNT(*) >= 5
ORDER BY win_rate DESC;

-- Q-7.7 조합별 잘하는 유저 후보 (8판 이상 · 승률 50% 초과 — BR-CHMP-03; 조합당 5명은 응용이 자른다)
SELECT champion, position, game_name, tag_line, COUNT(*) AS n_games,
       CAST(SUM(CASE WHEN win THEN 1 ELSE 0 END) AS DOUBLE PRECISION) / COUNT(*) AS win_rate
FROM champion_match_participant
WHERE game_name <> ''
GROUP BY champion, position, game_name, tag_line
HAVING COUNT(*) >= 8 AND CAST(SUM(CASE WHEN win THEN 1 ELSE 0 END) AS DOUBLE PRECISION) / COUNT(*) > 0.5
ORDER BY champion, position, win_rate DESC;


-- =====================================================================
-- Q-8. 정리·점검 (P-7: 트리거 대신 정리 규칙 — 요청 시 또는 스케줄러)
-- =====================================================================

-- Q-8.1 복기 캐시 5분 만료 (자식 5표는 ON DELETE CASCADE) — BR-SUM-05 · BR-SUM-07
DELETE FROM summoner_review WHERE expires_at <= :now;

-- Q-8.2 티어 캐시 24시간 만료
DELETE FROM participant_rank_cache WHERE expires_at <= :now;

-- Q-8.3 공개 표에 puuid 컬럼이 없는지 (tests/test_contract.py test_ranking_has_no_puuid 의 DB 판) — 정보 스키마
SELECT table_name, column_name
FROM information_schema.columns
WHERE column_name = 'puuid'
  AND table_name NOT IN ('ranking_name_cache', 'participant_rank_cache', 'reviewed_game_participant');
-- 결과가 0행이어야 한다.

-- Q-8.4 활성 모델이 정확히 하나인가 (model_version.is_active 응용 규칙)
SELECT COUNT(*) AS n_active FROM model_version WHERE is_active = TRUE;
-- 1 이어야 한다.

-- Q-8.5 골든 정답지가 활성 모델과 짝이 맞는가 (BR-TRN-05: 재학습 뒤 정답지 재생성)
SELECT m.version, COUNT(g.case_no) AS n_cases
FROM model_version m
LEFT JOIN golden_case g ON g.version = m.version
WHERE m.is_active = TRUE
GROUP BY m.version;
-- n_cases 가 50 이어야 한다.
