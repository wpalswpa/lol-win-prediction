# 프런트엔드 서버 — 포트 F9504 (도메인 p4.sumzip.com 이 여기로 온다).
#
# 하는 일 두 가지뿐:
#   1) 화면(web/templates/index.html)을 내려준다
#   2) /api/* 요청을 백엔드(127.0.0.1:9524)로 그대로 중계한다
#   3) /model/* 요청을 모델 API 서버(127.0.0.1:9544)로 그대로 중계한다 — Swagger 는 /model/docs (docs/api_guide.md)
# 그래서 화면 JS 는 상대경로 /api/... 만 부르면 되고, 도메인·포트가 바뀌어도 화면 코드는 안 바뀐다.
# 예측 로직은 여기에 한 줄도 없다 (서빙 파리티).
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from flask import Flask, Response, render_template, request, send_from_directory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", 9504))
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", 9524))
BACKEND = os.environ.get("BACKEND_URL", f"http://127.0.0.1:{BACKEND_PORT}")
DOMAIN = os.environ.get("DOMAIN", "p4.sumzip.com")
# 모델 API 서버(models/app.py · FastAPI 9544). /model/* 을 그대로 중계해 공개 도메인 하나로 Swagger(/model/docs)까지 연다.
# 예측 로직은 여전히 여기 없다 — 바깥 문 9504 하나로 두 서비스를 내보낼 뿐이다 (docs/api_guide.md).
MODEL_API_PORT = int(os.environ.get("API_PORT", 9544))
MODEL_API = os.environ.get("MODEL_API_URL", f"http://127.0.0.1:{MODEL_API_PORT}").rstrip("/")
MODEL_PREFIX = "/model"

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


@app.route(MODEL_PREFIX, defaults={"path": ""}, methods=["GET", "POST", "OPTIONS"])
@app.route(MODEL_PREFIX + "/", defaults={"path": ""}, methods=["GET", "POST", "OPTIONS"])
@app.route(MODEL_PREFIX + "/<path:path>", methods=["GET", "POST", "OPTIONS"])
def proxy_model(path):
    """모델 API 중계 — 경로·본문·질의를 그대로 넘기고, X-Forwarded-Prefix 로 «/model 아래에 있다»는 것만 알려준다.

    /model 과 /model/ 은 Swagger(/model/docs)로 보낸다. X-API-Key 는 그대로 통과시킨다(키 검사는 API 서버 몫).
    """
    if request.method == "OPTIONS":
        return ("", 204)
    if path == "":
        return Response(status=302, headers={"Location": MODEL_PREFIX + "/docs"})
    q = ("?" + request.query_string.decode()) if request.query_string else ""
    headers = {"Content-Type": request.headers.get("Content-Type", "application/json"),
               "X-Forwarded-Prefix": MODEL_PREFIX}
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
