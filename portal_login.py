"""纯 HTTP 门户密码登录（逆向自小程序 wxapkg，2026-10-18）。

链路：getCaptcha 拿验证码 -> decryptLogin(学号/密码/验证码) 拿 satoken
      -> personnelLogin(satoken) 换 chair-personnel-token -> 写 auth_cache。

签名算法（app-service.js 请求封装）：
  sign = MD5( sort("k"+v for k,v in data) join("") + token + randkey + ts + nonce ).upper()
  key  = "04" + SM2_encrypt(randkey, 公钥, C1C3C2)
  data 的 wxCode 永远是字面量 "wxCode"（服务端不校验该参数本身）
用法：
  python portal_login.py <账号>            # 全流程：验证码图存 cache/shots/captcha_py.png 人工/视觉识别
  python portal_login.py <账号> --captcha ABCD   # 带验证码直接登录
"""
import hashlib
import json
import random
import re
import sys
import time
from datetime import datetime

import requests
import urllib3
from gmssl import sm2

urllib3.disable_warnings()

AUTH_HOST = "https://portal.nudt.edu.cn/certification"
SM2_PUBKEY = ("04759df7678fc8668f59f7acdfc1c6b59f4b29a0ee0b4125ea5136d78c9ea0d"
              "28993c517ec6444c4e3b3b44c819a5fb5f7f7618806221e8ca1ac52849a4ce5e92c")
CHARSET = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"

_sm2 = sm2.CryptSM2(public_key=SM2_PUBKEY[2:], private_key="")


def rnd(n):
    return "".join(random.choice(CHARSET) for _ in range(n))


def make_sign(token, data, key, ts, nonce):
    parts = []
    for d in data:
        v = data[d]
        if isinstance(v, (dict, list)):
            v = json.dumps(v, separators=(",", ":"))
        parts.append(f"{d}{v}")
    parts.sort()
    raw = "".join(parts) + token + key + str(ts) + nonce
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


def sm2_encrypt_hex(plain):
    ct = _sm2.encrypt(plain.encode("utf-8"))  # C1C3C2，含 04 前缀
    return ct.hex().lstrip("0") if False else ct.hex()


class PortalClient:
    def __init__(self):
        self.s = requests.Session()
        self.s.verify = False
        self.s.trust_env = False
        self.s.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781"
                           "(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF"),
            "Origin": "https://portal.nudt.edu.cn",
            "Referer": "https://portal.nudt.edu.cn/",
        })
        self.token = ""

    def _call(self, method, url, data, verify_info="", auth_code=""):
        data = dict(data)
        data["wxCode"] = "wxCode"  # needWxCode 占位符（源码证实）
        ts = int(time.time() * 1000)
        key = rnd(16)
        nonce = rnd(10)
        sign = make_sign(self.token, data, key, ts, nonce)
        try:
            key_hdr = "04" + sm2_encrypt_hex(key)
        except Exception as e:
            raise RuntimeError(f"SM2 encrypt failed: {e}")
        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "ts": str(ts), "nonce": nonce, "sign": sign,
            "login_type": "weChat", "key": key_hdr,
            "Access-Control-Max-Age": "1800",
        }
        # 空值头不发：服务端会把空 satoken 当"过期 token"拒掉（A00107）
        if self.token:
            headers["id_token"] = self.token
            headers["satoken"] = self.token
        if auth_code:
            headers["auth_code"] = auth_code
        if verify_info:
            headers["verify_info"] = verify_info
        if method == "GET":
            r = self.s.get(url, params=data, headers=headers, timeout=(2, 6))
        else:
            r = self.s.post(url, data=data, headers=headers, timeout=(2, 6))
        return r

    def get_is_not_verify(self):
        r = self._call("GET", AUTH_HOST + "/getIsNotVerify.do", {})
        return r.json()

    def get_captcha(self, login_account):
        r = self._call("GET", AUTH_HOST + "/getCaptcha.do", {"loginAccount": login_account})
        return r

    def decrypt_login(self, account, password, code, verify_info=""):
        r = self._call("POST", AUTH_HOST + "/decryptLogin.do",
                       {"loginUser": account, "loginPassword": password, "code": code},
                       verify_info=verify_info)
        return r

    def exchange_chair(self, satoken):
        s2 = requests.Session()
        s2.verify = False
        s2.trust_env = False
        s2.headers.update({
            "User-Agent": self.s.headers["User-Agent"],
            "Origin": "https://app.nudt.edu.cn",
            "Referer": "https://app.nudt.edu.cn/chair/h5/",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        r = s2.post("https://app.nudt.edu.cn/chair/h5/api/personnelLogin.do",
                    data={"accessToken": satoken}, timeout=(2, 6))
        ct = r.cookies.get("chair-personnel-token")
        return r, ct


def load_cred(account):
    for line in open(r"F:\GIT\lecture-booking\accounts.csv", encoding="utf-8-sig"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace(",", " ").split()
        if parts[0] == account:
            return parts[1] if len(parts) > 1 else ""
    return None


def main():
    account = sys.argv[1]
    captcha = ""
    verify_info = ""
    if "--captcha" in sys.argv:
        captcha = sys.argv[sys.argv.index("--captcha") + 1]
    pwd = load_cred(account)
    if not pwd:
        print(f"账号 {account} 无密码")
        return 1

    c = PortalClient()
    print("[1] getIsNotVerify:", c.get_is_not_verify())

    r = c.get_captcha(account)
    print("[2] getCaptcha:", r.status_code, "len", len(r.text))
    try:
        j = r.json()
        print("    resp keys:", list(j.keys()) if isinstance(j, dict) else type(j))
        # 常见形态：{code, data: {image: base64, verify_info/uuid...}}
        data = j.get("data") if isinstance(j, dict) else None
        if isinstance(data, dict):
            print("    data keys:", list(data.keys()))
            img = None
            for k in ("image", "img", "base64", "captcha", "codeImage", "verifyCode"):
                if data.get(k):
                    img = data[k]; img_key = k; break
            if img and "," in img:  # data:image/png;base64,....
                img = img.split(",", 1)[1]
            if img:
                import base64
                raw = base64.b64decode(img)
                out = rf"F:\GIT\lecture-booking\cache\shots\captcha_py.png"
                open(out, "wb").write(raw)
                print(f"    验证码图已存 {out} ({len(raw)} bytes)")
    except Exception as e:
        print("    非 JSON 响应:", r.text[:120])

    if not captcha:
        print("    用 --captcha XXX 传验证码后重跑")
        return 2

    r = c.decrypt_login(account, pwd, captcha, verify_info)
    print("[3] decryptLogin:", r.status_code, r.text[:300])
    try:
        j = r.json()
        if j.get("code") == "00000":
            tok = j.get("data")
            if isinstance(tok, str) and len(tok) > 50:
                print(">>> satoken 拿到:", tok[:20] + "...")
                r2, ct = c.exchange_chair(tok)
                print("[4] personnelLogin:", r2.status_code, r2.text[:120])
                if ct:
                    cache = {"chair_token": ct, "id_token": tok, "loginAccount": account,
                             "captured_at": datetime.now().isoformat()}
                    json.dump(cache, open(rf"F:\GIT\lecture-booking\auth\auth_cache_{account}.json", "w",
                                           encoding="utf-8"), ensure_ascii=False, indent=2)
                    print(f">>> auth_cache_{account}.json 已写入，chair-token len {len(ct)}")
                    return 0
        else:
            print("    登录失败:", j.get("msg"), "| error 字段(verify_info?):", str(j.get("error"))[:80])
    except Exception:
        pass
    return 1


if __name__ == "__main__":
    sys.exit(main())
