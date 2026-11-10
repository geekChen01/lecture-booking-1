import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CACHE_DIR = os.path.join(BASE_DIR, "cache")
AUTH_DIR = os.path.join(BASE_DIR, "auth")
LOG_DIR = os.path.join(BASE_DIR, "log")

RUN_CACHE_DIR = os.path.join(CACHE_DIR, "log")
RESULTS_DIR = os.path.join(BASE_DIR, "results")

SUMMARY_FILE = os.path.join(CACHE_DIR, "token_capture_summary.json")

DEBUG_MODE = True

FETCH_DELAY = (0, 0.1)
BOOK_DELAY = (0, 0.05)
MAX_PAGES = 2

# 超时（连接, 读）：拥堵下宁可快速失败重试，不傻等长超时
CONNECT_TIMEOUT = 2
READ_TIMEOUT_SCAN = 4        # 扫描类请求读超时
READ_TIMEOUT_SIGNUP = 2      # 报名 POST 读超时：多发短超时代替少发长等待
REQUEST_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT_SCAN)
SIGNUP_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT_SIGNUP)
MAX_RETRIES = 2              # 扫描类总尝试次数（含首次）
SIGNUP_MAX_RETRIES = 5       # 报名 POST 总尝试次数：2s×5发≈11s（10-18 实战证明 2 发太少；
                             # 重发安全——服务端按"已报名/重复"去重，多发只增加中签机会）
RETRY_DELAY = 0.3
CONCURRENT_SIGNUP_THREADS = 15 # todo  change here

# 预热模式（--prewarm）
HEARTBEAT_TIMEOUT = (2, 2)      # 心跳迷你 GET 的超时
HEARTBEAT_INTERVAL = 10         # 保活心跳间隔：须短于常见服务器 keep-alive 下限(~15s)
HEARTBEAT_MAX_LATENCY = 0.3     # 心跳慢于此视为病径，立即重建连接
PREWARM_FINAL_LEAD = 0.5        # 开火前最后一次验活的提前量（秒）
PREWARM_FIRE_OFFSET = 0.1       # 开火 = 开放时刻 + 0.1s，避免到达时被判"未到报名时间"
PREWARM_MAX_WAIT = 600          # 卡点等待上限（秒）：开放时刻距现在超过它就按普通模式跑

DRY_RUN = False
MULTI_ACCOUNT_MODE = True
# 南京/合肥校区讲座一律屏蔽（不看状态）；测试报名脚本时可改 True 放开，拿它们练手
INCLUDE_BLOCKED_CAMPUS = False
# 要运行的账号从 accounts.csv 读取，每行一个账号，可带密码列（逗号或空格分隔）：
#   2131129,密码    或    2131129 密码    （密码列可省略）
# # 注释、空行跳过。文件不存在或为空 = 运行 auth/ 下全部账号
ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.csv")


def _load_accounts_csv(path: str) -> list:
    rows = []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.replace(",", " ").split()
                rows.append((parts[0], parts[1] if len(parts) > 1 else ""))
    except FileNotFoundError:
        pass
    return rows


_ACCOUNT_ROWS = _load_accounts_csv(ACCOUNTS_FILE)
TARGET_ACCOUNTS = [account for account, _pwd in _ACCOUNT_ROWS]
# 账号→密码（当前主流程不用密码——token 走微信授权链路；预留自动登录用）
ACCOUNT_CREDENTIALS = {account: pwd for account, pwd in _ACCOUNT_ROWS if pwd}

CONCURRENT_ACCOUNTS = 10

_h = lambda s: bytes.fromhex(s).decode()
APP_HOST = _h("6170702e6e7564742e6564752e636e")
APP_ORIGIN = _h("68747470733a2f2f") + APP_HOST
APP_REFERER = _h("68747470733a2f2f6170702e6e7564742e6564752e636e2f63686169722f6835")
API_BASE = _h("68747470733a2f2f6170702e6e7564742e6564752e636e2f63686169722f68352f6170692f6368616972")
PERSONNEL_LOGIN_URL = _h("68747470733a2f2f6170702e6e7564742e6564752e636e2f63686169722f68352f6170692f706572736f6e6e656c4c6f67696e2e646f")

AUTO_REFRESH = True

# Server酱微信推送（报名完成后推送结果）
SERVERCHAN_ENABLED = True   # 是否推送；False 则完全跳过
SERVERCHAN_KEY = ""         # SendKey（SCT 开头）；也可用环境变量 SERVERCHAN_KEY，两者均空则跳过推送

## DEBUG HERE
MITMDUMP_PATH = r"E:\Tools\mitmproxy-12.2.1-windows-x86_64\mitmdump.exe"
PROXY_ADDR = "127.0.0.1"
PROXY_PORT = 8891
CHECK_INTERVAL = 2
TARGET_KEYWORD = "getLoginUser.do"

# 抓 Token 时代理就绪后自动拉起"科大微门户"小程序（桌面快捷方式同款链接）；失败则提示手动打开
APPLET_LAUNCH_URL = "weixin://launchapplet/?app_id=wx1ca8aaf9c99f6cd1"

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(AUTH_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(RUN_CACHE_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)
