import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CACHE_DIR = "cache"
AUTH_DIR = "auth"
LOG_DIR = os.path.join(BASE_DIR, "log")

SUMMARY_FILE = os.path.join(CACHE_DIR, "token_capture_summary.json")

CACHE_EXPIRE_HOURS = 12
DEBUG_MODE = True

FETCH_DELAY = (0.4, 0.7)
BOOK_DELAY = (0.8, 1.2)
LIST_CACHE_TTL = 3
MAX_PAGES = 2

REQUEST_TIMEOUT = (2.5, 7.5)
MAX_RETRIES = 3
RETRY_DELAY = 1.0
CONCURRENT_SIGNUP_THREADS = 5

DRY_RUN = True
MULTI_ACCOUNT_MODE = False
FILTER_NANJING = False
## CHANGE HERE 
TARGET_ACCOUNTS = ["2512345"]  # 指定账号列表，如 ["2512345", "2512346"；为空则运行所有账号

CONCURRENT_ACCOUNTS = 3
ACCOUNT_DELAY_RANGE = (1, 3)

API_BASE = "https://app.nudt.edu.cn/chair/h5/api/chair"
PERSONNEL_LOGIN_URL = "https://app.nudt.edu.cn/chair/h5/api/personnelLogin.do"

AUTO_REFRESH = True

MITMDUMP_PATH = r"E:\Tools\mitmproxy-12.2.1-windows-x86_64\mitmdump.exe"
PROXY_ADDR = "127.0.0.1"
PROXY_PORT = 8891
CHECK_INTERVAL = 2
TARGET_KEYWORD = "getLoginUser.do"

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(AUTH_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
