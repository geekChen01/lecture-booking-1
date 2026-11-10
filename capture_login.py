"""mitm 插件：记录小程序密码登录实验的登录/token 相关流量到 jsonl。
用法：mitmdump -s capture_login.py --listen-port 8891"""
import json
import time

LOG = r"F:\GIT\lecture-booking\cache\log\pw_login_capture.jsonl"


def response(flow):
    try:
        url = flow.request.pretty_url
        hdrs = dict(flow.response.headers)
        set_cookie = hdrs.get("Set-Cookie", "") or hdrs.get("set-cookie", "")
        body = flow.response.get_text() or ""
        low = url.lower()
        interesting = (
            "login" in low
            or "chair-personnel-token" in set_cookie
            or '"token"' in body[:600].lower()
            or "getuser" in low
            or "chair" in low and flow.request.method == "POST"
        )
        if not interesting:
            return
        rec = {
            "t": time.strftime("%H:%M:%S"),
            "url": url,
            "method": flow.request.method,
            "req_body": (flow.request.get_text() or "")[:600],
            "req_cookie": (flow.request.headers.get("Cookie", "") or "")[:400],
            "status": flow.response.status_code,
            "set_cookie": set_cookie[:300],
            "resp_body": body[:800],
        }
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
