# 프런트엔드 서버 — 포트 F9504 (도메인 p4.sumzip.com 이 여기로 온다).
#
# 하는 일 두 가지뿐:
#   1) 화면(web/templates/index.html)을 내려준다
#   2) /api/* 요청을 백엔드(127.0.0.1:9524)로 그대로 중계한다
#   3) /model-api/* 요청은 접두를 떼고 모델 API 서버(127.0.0.1:9544)로 중계한다 — Swagger 는 /model-api/docs (docs/api_guide.md)
#   4) /model-api-kit/* 로 외부 개발자용 연계 키트(매뉴얼·OpenAPI·예시·클라이언트·zip)를 내려준다 — 화면의 «API 연계» 탭
# 그래서 화면 JS 는 상대경로 /api/... 만 부르면 되고, 도메인·포트가 바뀌어도 화면 코드는 안 바뀐다.
# 예측 로직은 여기에 한 줄도 없다 (서빙 파리티).
import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import date

from flask import Flask, Response, render_template, request, send_from_directory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", 9504))
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", 9524))
BACKEND = os.environ.get("BACKEND_URL", f"http://127.0.0.1:{BACKEND_PORT}")
DOMAIN = os.environ.get("DOMAIN", "p4.sumzip.com")
# 모델 API 서버(models/app.py · FastAPI 9544). /model-api/* 을 중계해 공개 도메인 하나로 Swagger(/model-api/docs)까지 연다.
# 예측 로직은 여전히 여기 없다 — 바깥 문 9504 하나로 두 서비스를 내보낼 뿐이다 (docs/api_guide.md).
MODEL_API_PORT = int(os.environ.get("API_PORT", 9544))
MODEL_API = os.environ.get("MODEL_API_URL", f"http://127.0.0.1:{MODEL_API_PORT}").rstrip("/")
MODEL_PREFIX = "/model-api"                    # 학생절차 규약: pN.sumzip.com/model-api → 127.0.0.1:954N (접두를 떼고 넘긴다)

app = Flask(__name__, template_folder=os.path.join(ROOT, "web", "templates"))


def _backend(path, method="GET", body=None, timeout=30):
    req = urllib.request.Request(BACKEND + path, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", "application/json"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "application/json"), e.read()
    except Exception as e:
        return 502, "application/json", json.dumps({"error": f"백엔드({BACKEND})에 연결할 수 없습니다: {e}"}, ensure_ascii=False).encode()


@app.route("/")
def index():
    status, _, raw = _backend("/api/health", timeout=5)
    riot_ready = False
    if status == 200:
        try:
            riot_ready = bool(json.loads(raw).get("riot_ready"))
        except Exception:
            pass
    return render_template("index.html", riot_ready=riot_ready, domain=DOMAIN, backend_ok=(status == 200))


@app.after_request
def _no_cache_html(resp):
    """화면(HTML)은 캐시하지 않는다.

    배포해도 사용자 브라우저가 옛 index.html 을 계속 쓰면 새 기능이 안 보인다 —
    실제로 그랬다. 화면은 매번 새로 받게 하고, 그림 같은 정적 파일은 그대로 캐시한다.
    """
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
    return resp


@app.route("/figures/<path:name>")
def figures(name):
    """reports/ 의 그림을 그대로 내려준다.

    web/static/ 에 사본을 두면 그림을 다시 만들 때마다 손으로 복사해야 하고,
    깜빡하면 화면만 옛 그림이 남는다(실제로 그런 적이 있다).
    사본을 없애고 원본을 직접 서빙하므로, git pull 만 하면 화면도 같이 최신이 된다.
    send_from_directory 가 경로 탈출(../)을 막아 준다.
    """
    for sub in ("", "figures"):
        d = os.path.join(ROOT, "reports", sub)
        if os.path.isfile(os.path.join(d, name)):
            return send_from_directory(d, name, max_age=60)
    return ({"error": f"reports 에 {name} 이 없습니다"}, 404)


@app.route("/healthz")
def healthz():
    status, _, _ = _backend("/api/health", timeout=5)
    try:
        with urllib.request.urlopen(MODEL_API + "/health", timeout=3) as r:
            model_api_ok = r.status == 200
    except Exception:
        model_api_ok = False                       # 모델 API 가 꺼져 있어도 화면·/api 는 정상이다 (독립 서비스)
    return {"status": "ok", "service": "frontend", "port": FRONTEND_PORT, "domain": DOMAIN, "backend": BACKEND, "backend_ok": status == 200,
            "model_api": MODEL_API, "model_api_ok": model_api_ok}



# ── 외부 개발자용 연계 키트 ─────────────────────────────────────────────
# 화면의 «API 연계» 탭에서 내려받는다. 사본을 두지 않고 원본(docs/api_guide.md · models/examples/)을 그대로 내보내고,
# openapi.json 은 모델 API 에서 그때그때 받아 오므로 계약이 바뀌어도 여기를 고칠 일이 없다.
# 이름은 고정 목록에서만 고른다(경로 탈출 방지). 목록에 없으면 404.
KIT_FILES = {
    "api_guide.md":       (os.path.join(ROOT, "docs", "api_guide.md"),                    "text/markdown; charset=utf-8"),
    "request.json":       (os.path.join(ROOT, "models", "examples", "request.json"),       "application/json"),
    "request_batch.json": (os.path.join(ROOT, "models", "examples", "request_batch.json"), "application/json"),
}
KIT_PREFIX = "/model-api-kit"
KIT_ZIP = "lol-model-api-kit.zip"
KIT_CLIENT = "lol_model_api_client.py"

CLIENT_PY = '''"""lol_model_api_client.py — LoL 10분 승패 예측 모델 API 를 부르는 최소 클라이언트 (표준 라이브러리만).

    python lol_model_api_client.py                  # 예시 입력 3건을 서버에서 받아 차례로 판단
    python lol_model_api_client.py request.json     # 파일의 경기 상태 1건을 판단

BASE 를 바꾸면 다른 서버를 부른다. 키가 필요한 서버면 API_KEY 를 넣는다.
계약(입력 13개·응답 필드·오류 규약)은 api_guide.md 또는 {base}/docs 를 본다.
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "{base}"
API_KEY = ""                                   # 서버가 X-API-Key 를 요구할 때만


def _call(path, payload=None, timeout=15):
    headers = {{"Content-Type": "application/json"}}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:            # 401·422·503 은 여기로 온다 — 본문의 detail 에 이유가 있다
        return e.code, json.loads(e.read() or b"{{}}")


def health():
    return _call("/health")[1]


def predict(state: dict) -> dict:
    """경기 상태 1건 → label · win_prob_blue · top_factors · anomaly · warnings"""
    status, body = _call("/predict", state)
    if status != 200:
        raise RuntimeError(f"{{status}}: {{body.get('detail')}}")
    return body


def predict_batch(states: list) -> list:
    """최대 32건. 입력 순서가 그대로 보존된다."""
    status, body = _call("/predict/batch", {{"items": states}})
    if status != 200:
        raise RuntimeError(f"{{status}}: {{body.get('detail')}}")
    return body["results"]


def coach(state: dict) -> dict:
    """이 상태에서 무엇을 했다면 승률이 얼마나 올랐나 (actions 3개)"""
    status, body = _call("/coach", state)
    if status != 200:
        raise RuntimeError(f"{{status}}: {{body.get('detail')}}")
    return body


if __name__ == "__main__":
    h = health()
    print("health:", h["status"], h["model_name"], h["model_version"])
    if h["status"] != "ok":
        sys.exit("모델이 아직 준비 중입니다. 잠시 후 다시 실행하세요.")
    if len(sys.argv) > 1:
        states = [json.load(open(sys.argv[1], encoding="utf-8"))]
    else:
        states = [e["payload"] for e in _call("/examples")[1]]
    for st in states:
        r = predict(st)
        print(f"{{r['label']:10s}} 블루 승률 {{r['win_prob_blue']:.3f}}  1위 요인 {{r['top_factors'][0]['name']}}"
              + (f"  경고 {{r['warnings']}}" if r["warnings"] else ""))
'''


def _kit_client_source():
    return CLIENT_PY.format(base=f"https://{DOMAIN}{MODEL_PREFIX}")


def _kit_openapi():
    """모델 API 의 OpenAPI 명세를 그때그때 받아 온다. 서버가 꺼져 있으면 None."""
    try:
        with urllib.request.urlopen(MODEL_API + "/openapi.json", timeout=5) as r:
            return r.read()
    except Exception:
        return None


def _attachment(data, name, ctype):
    return Response(data, content_type=ctype,
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@app.route(KIT_PREFIX + "/<name>")
def kit_file(name):
    """연계 키트 파일 하나. 목록: api_guide.md · openapi.json · request.json · request_batch.json · 클라이언트 .py · 전체 zip"""
    if name == KIT_ZIP:
        return kit_zip()
    if name == KIT_CLIENT:
        return _attachment(_kit_client_source().encode("utf-8"), name, "text/x-python; charset=utf-8")
    if name == "openapi.json":
        raw = _kit_openapi()
        if raw is None:
            return ({"error": f"모델 API({MODEL_API})가 응답하지 않아 openapi.json 을 받을 수 없습니다"}, 502)
        return _attachment(raw, name, "application/json")
    if name in KIT_FILES:
        path, ctype = KIT_FILES[name]
        if not os.path.isfile(path):
            return ({"error": f"{name} 원본이 없습니다"}, 404)
        with open(path, "rb") as f:
            return _attachment(f.read(), name, ctype)
    return ({"error": f"연계 키트에 {name} 은 없습니다",
             "available": sorted([*KIT_FILES, "openapi.json", KIT_CLIENT, KIT_ZIP])}, 404)


def kit_zip():
    """한 번에 받는 묶음 — 매뉴얼 + OpenAPI(있으면) + 예시 2 + 클라이언트 + README. 메모리에서 만들고 디스크에 남기지 않는다."""
    buf = io.BytesIO()
    base = f"https://{DOMAIN}{MODEL_PREFIX}"
    raw = _kit_openapi()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, (path, _) in KIT_FILES.items():
            if os.path.isfile(path):
                z.write(path, name)
        if raw is not None:
            z.writestr("openapi.json", raw)
        z.writestr(KIT_CLIENT, _kit_client_source())
        z.writestr("README.txt",
                   f"LoL 10분 승패 예측 모델 API — 연계 키트 ({date.today().isoformat()})\n\n"
                   f"주소     {base}\n"
                   f"Swagger  {base}/docs      (브라우저에서 바로 호출해 볼 수 있다)\n"
                   f"상태     {base}/health\n\n"
                   "파일\n"
                   "  api_guide.md               연계 매뉴얼 — 엔드포인트·입력 13개·응답 필드·오류 규약·언어별 예시\n"
                   "  openapi.json               OpenAPI 3 명세 — Postman·Insomnia·코드 생성기에 그대로 넣는다"
                   + ("" if raw is not None else " (서버가 꺼져 있어 이번 묶음에는 빠짐 — /model-api/openapi.json 에서 받는다)") + "\n"
                   "  request.json               단건 예시 입력 (블루 우세)\n"
                   "  request_batch.json         배치 예시 입력 3건\n"
                   f"  {KIT_CLIENT}   파이썬 클라이언트 — python {KIT_CLIENT} 로 바로 확인\n\n"
                   "시작\n"
                   f"  curl -s {base}/health\n"
                   f"  curl -s -X POST {base}/predict -H 'Content-Type: application/json' -d @request.json\n"
                   f"  python {KIT_CLIENT} request.json\n")
    return _attachment(buf.getvalue(), KIT_ZIP, "application/zip")


@app.route(MODEL_PREFIX, defaults={"path": ""}, methods=["GET", "POST", "OPTIONS"])
@app.route(MODEL_PREFIX + "/", defaults={"path": ""}, methods=["GET", "POST", "OPTIONS"])
@app.route(MODEL_PREFIX + "/<path:path>", methods=["GET", "POST", "OPTIONS"])
def proxy_model(path):
    """모델 API 중계 — /model-api 접두를 떼고 경로·본문·질의를 그대로 넘긴다 (학생절차 1장의 vite proxy rewrite 와 같은 일).

    접두를 아는 것은 uvicorn 쪽(--root-path /model-api)이라 여기서는 헤더를 붙이지 않는다.
    /model-api 와 /model-api/ 는 Swagger(/model-api/docs)로 보낸다. X-API-Key 는 그대로 통과시킨다(키 검사는 API 서버 몫).
    """
    if request.method == "OPTIONS":
        return ("", 204)
    if path == "":
        return Response(status=302, headers={"Location": MODEL_PREFIX + "/docs"})
    q = ("?" + request.query_string.decode()) if request.query_string else ""
    headers = {"Content-Type": request.headers.get("Content-Type", "application/json")}
    if request.headers.get("X-API-Key"):
        headers["X-API-Key"] = request.headers["X-API-Key"]
    body = request.get_data() if request.method == "POST" else None
    req = urllib.request.Request(MODEL_API + "/" + path + q, data=body, method=request.method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return Response(r.read(), status=r.status, content_type=r.headers.get("Content-Type", "application/json"))
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code, content_type=e.headers.get("Content-Type", "application/json"))
    except Exception as e:
        return Response(json.dumps({"detail": f"모델 API({MODEL_API})에 연결할 수 없습니다: {e}"}, ensure_ascii=False),
                        status=502, content_type="application/json")


@app.route("/api/<path:path>", methods=["GET", "POST", "OPTIONS"])
def proxy(path):
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_data() if request.method == "POST" else None
    q = ("?" + request.query_string.decode()) if request.query_string else ""
    status, ctype, raw = _backend("/api/" + path + q, request.method, body)
    return Response(raw, status=status, content_type=ctype)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=FRONTEND_PORT)
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    print(f"[frontend] http://{a.host}:{a.port}  domain={DOMAIN}  backend={BACKEND}", flush=True)
    app.run(host=a.host, port=a.port, debug=False, threaded=True)
