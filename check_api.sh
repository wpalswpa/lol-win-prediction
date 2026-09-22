#!/usr/bin/env bash
# 모델 API 서버(models/app.py · FastAPI) — 시작/중지/재시작/상태/로그/테스트
#
#   ./check_api.sh start | stop | restart | status | logs [n] | health | test | setup
#   setup = models/.venv 를 만들고 models/requirements.txt 를 설치한다 (처음 한 번)
#   test  = 서버 없이 도는 단위 테스트(test_app.py) + 서버가 떠 있으면 HTTP 스모크
#
# 웹서비스(check_project.sh 의 프런트 9504 · 백엔드 9524)와는 독립이다 — 이 스크립트는 그 둘을 건드리지 않는다.
# 포트·호스트는 models/.env (PORT · HOST) 또는 환경변수 API_PORT 로 바꾼다. 기본 9544.
# 파이썬: models/.venv (Python 3.11 + models/requirements.txt 고정 버전). 운영 venv311 에는 FastAPI 를 넣지 않는다.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="$ROOT/models"
cd "$ROOT" || exit 1
RUN="$ROOT/run"; LOG="$ROOT/logs"; mkdir -p "$RUN" "$LOG"
NAME=api

# 포트: 환경변수 API_PORT → models/.env 의 PORT → 9544
if [ -z "${API_PORT:-}" ] && [ -f "$API_DIR/.env" ]; then
  API_PORT="$(sed -n 's/^PORT=\([0-9]*\).*/\1/p' "$API_DIR/.env" | tail -1)"
fi
API_PORT="${API_PORT:-9544}"
API_HOST="${API_HOST:-$(sed -n 's/^HOST=\([^ #]*\).*/\1/p' "$API_DIR/.env" 2>/dev/null | tail -1)}"
API_HOST="${API_HOST:-0.0.0.0}"
HEALTH="http://127.0.0.1:$API_PORT/health"

PY="$API_DIR/.venv/bin/python"
PY_SYS=""
for cand in /opt/homebrew/bin/python3.11 python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import sys; assert sys.version_info[:2] >= (3, 11)" >/dev/null 2>&1; then PY_SYS="$cand"; break; fi
done

c_ok()  { printf "\033[32m%s\033[0m\n" "$*"; }
c_bad() { printf "\033[31m%s\033[0m\n" "$*"; }
pid_of() { [ -f "$RUN/$NAME.pid" ] && cat "$RUN/$NAME.pid" 2>/dev/null; }
alive()  { local p; p=$(pid_of); [ -n "${p:-}" ] && kill -0 "$p" 2>/dev/null; }
port_pids() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null; }
http_code() { local c; c=$(curl -s -m "${2:-5}" -o /dev/null -w "%{http_code}" "$1" 2>/dev/null); echo "${c:-000}"; }
wait_up() { local i=0; while [ $i -lt "$2" ]; do [ "$(http_code "$1" 3)" = "200" ] && return 0; sleep 1; i=$((i+1)); done; return 1; }

need_venv() {
  [ -x "$PY" ] && "$PY" -c "import fastapi, uvicorn, sklearn" >/dev/null 2>&1 && return 0
  c_bad "models/.venv 가 없거나 불완전하다 — 먼저 ./check_api.sh setup"; return 1
}

cmd_setup() {
  echo "▶ 설치  (models/.venv · models/requirements.txt)"
  [ -n "$PY_SYS" ] || { c_bad "Python 3.11 이상을 못 찾았다 (brew install python@3.11)"; return 1; }
  [ -x "$PY" ] || "$PY_SYS" -m venv "$API_DIR/.venv" || return 1
  "$API_DIR/.venv/bin/pip" install -q -r "$API_DIR/requirements.txt" || return 1
  [ -f "$API_DIR/.env" ] || cp "$API_DIR/.env.example" "$API_DIR/.env"
  need_venv && c_ok "설치 완료 → ./check_api.sh start"
}

cmd_start() {
  echo "▶ 시작  (모델 API $API_HOST:$API_PORT · python: $PY)"
  need_venv || return 1
  [ -f "$API_DIR/model/artifacts/model.joblib" ] || { c_bad "models/model/artifacts/model.joblib 이 없다"; return 1; }
  if alive; then echo "$NAME: 이미 실행 중 (pid $(pid_of))"; return 0; fi
  if [ -n "$(port_pids "$API_PORT")" ]; then c_bad "$NAME: 포트 $API_PORT 를 다른 프로세스가 쓰고 있다 (pid $(port_pids "$API_PORT" | tr '\n' ' '))"; return 1; fi
  # stdin 을 끊고(</dev/null) 완전히 분리해야 이 스크립트를 부른 셸·자동화 도구가 서버가 뜬 뒤에도 멈추지 않는다
  ( cd "$API_DIR" && exec nohup "$PY" -m uvicorn app:app --host "$API_HOST" --port "$API_PORT" </dev/null >> "$LOG/$NAME.log" 2>&1 ) &
  echo $! > "$RUN/$NAME.pid"; disown 2>/dev/null || true
  if wait_up "$HEALTH" 30; then
    c_ok "$NAME: 시작됨 → http://$API_HOST:$API_PORT (pid $(pid_of))"
    echo "Swagger: http://127.0.0.1:$API_PORT/docs  ·  health: $HEALTH"
  else
    c_bad "$NAME: 기동 실패 — logs/$NAME.log 마지막 줄:"; tail -n 15 "$LOG/$NAME.log"; return 1
  fi
}

cmd_stop() {
  echo "▶ 중지"
  local p; p=$(pid_of)
  if [ -n "${p:-}" ] && kill -0 "$p" 2>/dev/null; then
    kill "$p" 2>/dev/null; for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$p" 2>/dev/null || break; sleep 0.5; done
    kill -0 "$p" 2>/dev/null && kill -9 "$p" 2>/dev/null
    echo "$NAME: 중지됨 (pid $p)"
  else echo "$NAME: 실행 중이 아님"; fi
  rm -f "$RUN/$NAME.pid"
  for q in $(port_pids "$API_PORT"); do kill "$q" 2>/dev/null && echo "$NAME: 포트 $API_PORT 잔여 프로세스 정리 (pid $q)"; done
}
cmd_restart() { cmd_stop; sleep 1; cmd_start; }

cmd_status() {
  echo "▶ 상태  (모델 API $API_PORT)"
  local p code; p=$(pid_of); code=$(http_code "$HEALTH")
  if alive && [ "$code" = "200" ]; then
    c_ok "  $NAME   실행 중  pid ${p}  포트 $API_PORT  health $code"
    curl -s -m 5 "$HEALTH"; echo
    return 0
  fi
  c_bad "  $NAME   중지/이상  pid ${p:-없음}  포트 $API_PORT  health $code  (listen: $(port_pids "$API_PORT" | tr '\n' ' '))"; return 1
}
cmd_logs()   { echo "▶ logs/$NAME.log"; tail -n "${1:-40}" "$LOG/$NAME.log" 2>/dev/null; }
cmd_health() { [ "$(http_code "$HEALTH")" = "200" ] && { c_ok "healthy"; return 0; } || { c_bad "unhealthy"; return 1; }; }

cmd_test() {
  need_venv || return 1
  echo "▶ 단위 테스트 (서버 없이 · test_app.py)"
  ( cd "$API_DIR" && "$PY" -m pytest -q test_app.py ) || return 1
  if [ "$(http_code "$HEALTH")" = "200" ]; then
    echo; echo "▶ HTTP 스모크 (서버 $API_PORT)"
    local fail=0 base="http://127.0.0.1:$API_PORT"
    chk() { if [ "$2" = "$3" ]; then c_ok "  [통과] $1 → $2"; else c_bad "  [실패] $1 → $2 (기대 $3)"; fail=1; fi; }
    chk "GET /health"  "$(http_code "$base/health")" 200
    chk "GET /docs"    "$(http_code "$base/docs")" 200
    chk "GET /openapi.json" "$(http_code "$base/openapi.json")" 200
    chk "GET /schema"  "$(http_code "$base/schema")" 200
    chk "GET /examples" "$(http_code "$base/examples")" 200
    chk "POST /predict" "$(curl -s -m 10 -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d @"$API_DIR/examples/request.json" "$base/predict")" 200
    chk "POST /predict/batch" "$(curl -s -m 10 -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d @"$API_DIR/examples/request_batch.json" "$base/predict/batch")" 200
    chk "POST /coach"  "$(curl -s -m 10 -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d @"$API_DIR/examples/request.json" "$base/coach")" 200
    chk "POST /predict 누락 → 422" "$(curl -s -m 10 -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d '{"GoldDiff": 100}' "$base/predict")" 422
    chk "GET /metrics" "$(http_code "$base/metrics")" 200
    [ "$fail" = 0 ] && c_ok "전부 통과" || { c_bad "실패 있음"; return 1; }
  else
    echo; echo "(서버가 꺼져 있어 HTTP 스모크는 건너뜀 — ./check_api.sh start 후 다시)"
  fi
}

case "${1:-}" in
  setup) cmd_setup ;;  start) cmd_start ;;  stop) cmd_stop ;;  restart) cmd_restart ;;
  status) cmd_status ;;  logs) cmd_logs "${2:-40}" ;;  health) cmd_health ;;  test) cmd_test ;;
  *) echo "사용법: $0 {setup|start|stop|restart|status|logs [n]|health|test}"; exit 2 ;;
esac
