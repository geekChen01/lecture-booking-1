import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CACHE_DIR = "cache"
AUTH_DIR = "auth"
LOG_DIR = os.path.join(BASE_DIR, "log")

RUN_CACHE_DIR = os.path.join(CACHE_DIR, "log")
RESULTS_DIR = os.path.join(BASE_DIR, "results")

SUMMARY_FILE = os.path.join(CACHE_DIR, "token_capture_summary.json")

CACHE_EXPIRE_HOURS = 3
DEBUG_MODE = True

FETCH_DELAY = (0.1, 0.7)
BOOK_DELAY = (0.1, 1.0)
MAX_PAGES = 2

REQUEST_TIMEOUT = (2.5, 7.5)
MAX_RETRIES = 3
RETRY_DELAY = 1.0
CONCURRENT_SIGNUP_THREADS = 15 # todo  change here

DRY_RUN = False
MULTI_ACCOUNT_MODE = True
FILTER_NANJING = True
## CHANGE HERE 
TARGET_ACCOUNTS = []  # 指定账号列表，如 ["2512345", "2512346"；为空则运行所有账号

CONCURRENT_ACCOUNTS = 10
ACCOUNT_DELAY_RANGE = (0.1, 0.5)

_h = lambda s: bytes.fromhex(s).decode()
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

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(AUTH_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(RUN_CACHE_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)
