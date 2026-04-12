#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import io
import json
import logging
import os
import sys

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
import random
import re
import threading
import time
from datetime import datetime
from typing import Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config1 import (
    CACHE_DIR, AUTH_DIR, DEBUG_MODE,
    DRY_RUN, MULTI_ACCOUNT_MODE, FILTER_NANJING, TARGET_ACCOUNTS,
    CONCURRENT_ACCOUNTS, ACCOUNT_DELAY_RANGE,
    API_BASE, PERSONNEL_LOGIN_URL,
)

try:
    from display import Colors, c, enable_win_colors, print_lecture_table, print_status, print_all_categories, AccountOutputBuffer
except ImportError:
    class _ColorsFallback:
        GREEN = "\033[92m"
        RED = "\033[91m"
        YELLOW = "\033[93m"
        BLUE = "\033[94m"
        CYAN = "\033[96m"
        RESET = "\033[0m"
    Colors = _ColorsFallback
    def c(text, code): return f"{code}{text}{Colors.RESET}"
    def enable_win_colors(): pass
    print_lecture_table = None
    print_status = None
    print_all_categories = None
    AccountOutputBuffer = None

from auth import TokenManager
from lecture import LectureScanner, LectureFilter, LectureSignup

RUN_LOG_FILE = os.path.join(
    CACHE_DIR,
    f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
)

log_level = logging.DEBUG if DEBUG_MODE else logging.INFO
logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(RUN_LOG_FILE, encoding="utf-8")]
)
logger = logging.getLogger(__name__)


def discover_captured_accounts(cache_dir: str = AUTH_DIR) -> Dict[str, Dict]:
    accounts = {}
    pattern = re.compile(r"^auth_cache_([^\s]+)\.json$")
    if not os.path.exists(cache_dir):
        return accounts

    for fname in os.listdir(cache_dir):
        match = pattern.match(fname)
        if not match:
            continue

        login_account = match.group(1)
        cache_path = os.path.join(cache_dir, fname)

        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)

            id_token = cache.get("id_token", "")
            expire_at = cache.get("expire_at")
            needs_refresh = False
            reason = "ok"

            if not id_token or len(id_token) < 50:
                needs_refresh = True
                reason = "invalid_token"
            elif expire_at:
                try:
                    if datetime.fromisoformat(expire_at) <= datetime.now():
                        needs_refresh = True
                        reason = "expired"
                except (ValueError, TypeError):
                    needs_refresh = True
                    reason = "invalid_expire_format"

            accounts[login_account] = {
                "cache_file": cache_path,
                "id_token": (id_token or "")[:20] + "...",
                "id_token_full": id_token,
                "userId": cache.get("userId"),
                "captured_at": cache.get("captured_at"),
                "expire_at": expire_at,
                "needs_refresh": needs_refresh,
                "refresh_reason": reason,
            }

            status = "🔄 需刷新" if needs_refresh else "✅ 有效"
            logger.debug(f"[{login_account}] {status} (reason:{reason})")

        except Exception as e:
            logger.warning(f"⚠️ 读取缓存失败 {fname}: {e}")
            accounts[login_account] = {
                "cache_file": cache_path,
                "needs_refresh": True,
                "refresh_reason": f"read_error:{str(e)[:30]}",
            }

    return accounts


def _run_single_account(account_name: str, id_token: str, dry_run: bool = DRY_RUN) -> Dict:
    reporter = AccountOutputBuffer(account_name)
    _h = lambda s: bytes.fromhex(s).decode()
    _HOST = _h("6170702e6e7564742e6564752e636e")
    _ORIGIN = _h("68747470733a2f2f") + _HOST
    _REFERER = _h("68747470733a2f2f6170702e6e7564742e6564752e636e2f63686169722f6835")
    try:
        session = requests.Session()
        session.verify = False
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=20, pool_maxsize=20, max_retries=0, pool_block=False
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update({
            "Host": _HOST,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf254181c) XWEB/19201 miniProgram/wx1ca8aaf9c99f6cd1",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": _ORIGIN,
            "Referer": _REFERER,
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Priority": "u=1, i",
            "Content-Type": "application/x-www-form-urlencoded"
        })

        token_manager = TokenManager(account_name, AUTH_DIR, session)
        if not token_manager.ensure_auth(id_token):
            raise RuntimeError(f"账号 [{account_name}] 认证失败：无有效 Token | 请先运行抓 Token 脚本")

        scanner = LectureScanner(session, account_name, CACHE_DIR)
        user_chairs = scanner.fetch_user_chairs()
        future_chairs = scanner.fetch_future_chairs()

        filtered_result = LectureFilter.filter_chairs(future_chairs, user_chairs)

        enrolled_count = len(filtered_result["enrolled"])
        conflict_count = len(filtered_result["conflict"])
        nanjing_count = len(filtered_result["nanjing"])
        reporter.add(f"已报名讲座数: {len(user_chairs)}")
        reporter.add(f"过滤结果: 已报名={enrolled_count}, 时间冲突={conflict_count}, 南京关键词={nanjing_count}")

        if not any(filtered_result.values()):
            reporter.add("⚠️ 未扫描到可报名讲座")
            result = {"account": account_name, "success": 0, "fail": 0}
            result["report_text"] = reporter.render(result)
            return result

        reporter.capture_print(print_all_categories, filtered_result)

        if filtered_result["available"]:
            confirm = "yes" if MULTI_ACCOUNT_MODE else input(f"\n📥 账号 [{account_name}] 是否报名？(yes/no) ")
            if confirm.strip().lower() == "yes":
                signup = LectureSignup(session, account_name, API_BASE, reporter, token_manager, scanner)
                res = signup.batch_signup(filtered_result["available"], dry_run=dry_run)
                result = {"account": account_name, "success": len(res["success"]), "fail": len(res["fail"])}
                result["report_text"] = reporter.render(result)
                return result

        result = {"account": account_name, "success": 0, "fail": 0}
        result["report_text"] = reporter.render(result)
        return result

    except RuntimeError as e:
        logger.error(f"[{account_name}] {e}")
        result = {"account": account_name, "success": 0, "fail": 0, "error": str(e)[:80]}
        reporter.add(f"运行失败: {e}")
        result["report_text"] = reporter.render(result)
        return result
    except Exception as e:
        logger.error(f"[{account_name}] {type(e).__name__}: {e}")
        result = {"account": account_name, "success": 0, "fail": 0, "error": f"{type(e).__name__}: {str(e)[:50]}"}
        reporter.add(f"异常: {type(e).__name__}: {e}")
        result["report_text"] = reporter.render(result)
        return result


def _run_concurrent_accounts(accounts: Dict[str, Dict], dry_run: bool = DRY_RUN) -> List[Dict]:
    results: List[Dict] = []
    semaphore = threading.Semaphore(CONCURRENT_ACCOUNTS)

    def _wrapped_run(account_name: str, id_token: str):
        with semaphore:
            result = _run_single_account(account_name, id_token, dry_run)
            if MULTI_ACCOUNT_MODE:
                time.sleep(random.uniform(*ACCOUNT_DELAY_RANGE))
            return result

    with ThreadPoolExecutor(max_workers=CONCURRENT_ACCOUNTS) as executor:
        futures = {
            executor.submit(_wrapped_run, name, info["id_token_full"]): name
            for name, info in accounts.items()
        }

        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
                results.append(result)
                print(result.get("report_text", ""))
            except Exception as e:
                print(f"\n{'=' * 72}\n📦 账号：{name}\n{'=' * 72}")
                print(f"❌ 任务异常: {e}\n")
                logger.error(f"💥 任务异常：{e}")

    return results


def _print_summary_report(results: List[Dict]):
    print(f"\n{Colors.CYAN}{'=' * 50}{Colors.RESET}")
    print(f"📊 汇总：成功 {sum(r.get('success', 0) for r in results)} | 失败 {sum(r.get('fail', 0) for r in results)}")
    for r in results:
        err = r.get("error")
        extra = f" | {err}" if err else ""
        print(f"  {r['account']}: ok={r.get('success', 0)} fail={r.get('fail', 0)}{extra}")
    print(f"{Colors.CYAN}{'=' * 50}{Colors.RESET}")

    try:
        report_file = os.path.join(CACHE_DIR, "multi_account_report.json")
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(
                {"timestamp": datetime.now().isoformat(), "results": results},
                f, ensure_ascii=False, indent=2
            )
        logger.info(f"📊 报名报告已保存：{report_file}")
    except Exception as e:
        logger.warning(f"保存报告失败：{e}")


def main():
    enable_win_colors()
    print(c("\n🎓 讲座管理脚本 - 分层输出版", Colors.CYAN))
    print(f"DRY_RUN={DRY_RUN}  MULTI_ACCOUNT={MULTI_ACCOUNT_MODE}  DEBUG={DEBUG_MODE}  FILTER_NANJING={FILTER_NANJING}")
    print(f"日志文件: {RUN_LOG_FILE}")
    print("-" * 50)

    if FILTER_NANJING:
        print(c("🔍 当前配置：屏蔽南京校区讲座", Colors.YELLOW))
    else:
        print(c("🔍 当前配置：显示南京校区讲座", Colors.GREEN))

    accounts = discover_captured_accounts()

    if not accounts:
        print(c("⚠️ 未发现 auth_cache_*.json 缓存文件", Colors.YELLOW))
        print(c("💡 请先运行抓 Token 脚本生成 cache/auth_cache_*.json", Colors.CYAN))
        return

    if TARGET_ACCOUNTS:
        filtered = {k: v for k, v in accounts.items() if k in TARGET_ACCOUNTS}
        missing = [a for a in TARGET_ACCOUNTS if a not in accounts]
        if missing:
            print(c(f"⚠️ 指定账号未找到缓存: {missing}", Colors.YELLOW))
        if not filtered:
            print(c("⚠️ 指定的账号均未找到缓存文件", Colors.RED))
            return
        accounts = filtered
        print(c(f"🎯 指定账号模式：仅运行 {list(accounts.keys())}", Colors.CYAN))

    print(f"{Colors.GREEN}✅ 发现 {len(accounts)} 个账号缓存:{Colors.RESET}")
    for name, info in accounts.items():
        status = "🔄 需刷新" if info.get("needs_refresh") else "✅ 有效"
        reason = info.get("refresh_reason", "ok")
        token_preview = info.get("id_token", "")
        print(f"   🔹 {name}: {status} ({reason}) | Token: {token_preview}")

    if not MULTI_ACCOUNT_MODE:
        names = list(accounts.keys())
        target = names[0]

        if len(accounts) > 1:
            print(f"\n{Colors.YELLOW}⚠️ 发现多个账号，但 MULTI_ACCOUNT_MODE=False{Colors.RESET}")
            choice = input(f"请输入要运行的账号名称 (回车默认运行 {names[0]}): ").strip()
            if choice and choice in accounts:
                target = choice

        print(f"\n{Colors.BLUE}🧪 运行账号：{target}{Colors.RESET}")
        result = _run_single_account(
            target,
            accounts[target]["id_token_full"],
            dry_run=DRY_RUN
        )
        print(result.get("report_text", ""))
        _print_summary_report([result])
        return

    print(f"\n{Colors.CYAN}🚀 启动多账号并发模式 (最大并发:{CONCURRENT_ACCOUNTS}){Colors.RESET}")
    results = _run_concurrent_accounts(accounts, dry_run=DRY_RUN)
    _print_summary_report(results)


if __name__ == "__main__":
    main()
