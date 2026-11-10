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
import glob
import re
import threading
import time
from datetime import datetime
from typing import Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import (
    AUTH_DIR, DEBUG_MODE,
    DRY_RUN, MULTI_ACCOUNT_MODE, INCLUDE_BLOCKED_CAMPUS, TARGET_ACCOUNTS,
    CONCURRENT_ACCOUNTS, CONCURRENT_SIGNUP_THREADS,
    API_BASE, APP_HOST, APP_ORIGIN, APP_REFERER,
    RUN_CACHE_DIR, RESULTS_DIR,
    SERVERCHAN_ENABLED, SERVERCHAN_KEY,
    HEARTBEAT_INTERVAL, HEARTBEAT_MAX_LATENCY, PREWARM_FINAL_LEAD, PREWARM_FIRE_OFFSET,
)

from display import Colors, c, enable_win_colors, print_all_categories, AccountOutputBuffer, output_lock

from auth import TokenManager
from lecture import LectureScanner, LectureFilter, LectureSignup, build_conflict_groups

PREWARM_MODE = "--prewarm" in sys.argv  # 整点前完成扫描+预热+保活，到点即发报名 POST

# 运行日志按时间戳保留（不再覆盖，供整点实战复盘），并清理 7 天前的旧日志
RUN_LOG_FILE = os.path.join(RUN_CACHE_DIR, f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
_now = time.time()
for old_log in glob.glob(os.path.join(RUN_CACHE_DIR, "run_*.log")):
    try:
        if _now - os.path.getmtime(old_log) > 7 * 86400:
            os.remove(old_log)
    except OSError:
        pass

log_level = logging.DEBUG if DEBUG_MODE else logging.INFO
logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(RUN_LOG_FILE, encoding="utf-8", mode="a")]
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
            captured_at = cache.get("captured_at")

            accounts[login_account] = {
                "cache_file": cache_path,
                "id_token": (id_token or "")[:20] + "...",
                "id_token_full": id_token,
                "userId": cache.get("userId"),
                "captured_at": captured_at,
            }
            logger.debug(f"[{login_account}] 发现缓存 captured_at={captured_at}")

        except Exception as e:
            logger.warning(f"⚠️ 读取缓存失败 {fname}: {e}")
            accounts[login_account] = {
                "cache_file": cache_path,
                "captured_at": None,
            }

    return accounts


def _build_session() -> requests.Session:
    session = requests.Session()
    session.verify = False
    session.trust_env = False  # 无视系统/环境代理，防止抓 Token 的代理残留把报名流量劫持去 mitmdump
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=20, pool_maxsize=20, max_retries=0, pool_block=False
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "Host": APP_HOST,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf254181c) XWEB/19201 miniProgram/wx1ca8aaf9c99f6cd1",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": APP_ORIGIN,
        "Referer": APP_REFERER,
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Priority": "u=1, i",
        "Content-Type": "application/x-www-form-urlencoded"
    })
    return session


class _SharedFutureChairs:
    """pageChair.do 讲座列表与账号无关：全局只拉一次，首个拿到锁的账号线程拉取，其余复用。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = None

    def get(self, scanner: LectureScanner):
        with self._lock:
            if self._data is None:
                data = scanner.fetch_future_chairs()
                if any(data.values()):
                    self._data = data  # 空结果不缓存：拥堵下扫描失败时留给下一个账号重试
                return data
            return self._data


def _parse_apply_time(s: str) -> float:
    """applyBeginTime 字符串（"%Y-%m-%d %H:%M"）→ epoch 秒。"""
    return datetime.strptime(s, "%Y-%m-%d %H:%M").timestamp()


def _prewarm_wait_and_fire(signup: "LectureSignup", account_name: str, warm_n: int, fire_t: float):
    """预热模式：预热→心跳保活→最终验活→到点(开放+偏移)精确开火。"""
    signup._warm_connections(warm_n)
    logger.info(
        f"[{account_name}] 预热就绪，目标开火时刻 {datetime.fromtimestamp(fire_t).strftime('%H:%M:%S')}"
        f"+{PREWARM_FIRE_OFFSET}s（心跳间隔 {HEARTBEAT_INTERVAL}s）"
    )
    while True:
        remaining = fire_t - time.time()
        if remaining <= PREWARM_FINAL_LEAD:
            break
        time.sleep(min(HEARTBEAT_INTERVAL, remaining - PREWARM_FINAL_LEAD))
        lats = signup.heartbeat(warm_n)
        bad = sum(1 for l in lats if l is None or l > HEARTBEAT_MAX_LATENCY)
        if bad:
            logger.warning(f"[{account_name}] 心跳：{bad}/{len(lats)} 条病径/失效，立即重建")
            signup._warm_connections(bad)
    # 最终验活：开火前 0.5s 确认全部连接活着
    lats = signup.heartbeat(warm_n)
    stat = " ".join((f"{l * 1000:.0f}ms" if l is not None else "FAIL") for l in lats)
    logger.info(f"[{account_name}] 最终验活: {stat}")
    while time.time() < fire_t + PREWARM_FIRE_OFFSET:
        time.sleep(0.005)
    logger.info(f"[{account_name}] 🚀 开火 {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")


def _run_single_account(account_name: str, id_token: str, dry_run: bool = DRY_RUN,
                        shared_chairs: _SharedFutureChairs = None, prewarm: bool = False) -> Dict:
    reporter = AccountOutputBuffer(account_name)
    try:
        t_start = time.time()
        session = _build_session()
        token_manager = TokenManager(account_name, AUTH_DIR, session)
        if not token_manager.ensure_auth(id_token):
            raise RuntimeError(f"token-timeout \n 账号 [{account_name}] 认证失败：无有效 Token | 请先运行抓 Token 脚本")
        logger.info(f"[{account_name}] 认证完成 耗时{time.time() - t_start:.2f}s")

        scanner = LectureScanner(session, account_name, token_manager)

        # 已报名列表与讲座列表互不依赖，并行拉取；多账号时讲座列表走全局共享
        t_scan = time.time()
        with ThreadPoolExecutor(max_workers=2) as ex:
            user_fut = ex.submit(scanner.fetch_user_chairs)
            if shared_chairs:
                list_fut = ex.submit(shared_chairs.get, scanner)
            else:
                list_fut = ex.submit(scanner.fetch_future_chairs)
            user_chairs = user_fut.result()
            future_chairs = list_fut.result()
        logger.info(
            f"[{account_name}] 扫描完成 耗时{time.time() - t_scan:.2f}s | "
            f"已报名={len(user_chairs)} 可报名={len(future_chairs['available'])} "
            f"已满={len(future_chairs['full'])} 未开始={len(future_chairs['not_started'])}"
        )

        filtered_result = LectureFilter.filter_chairs(future_chairs, user_chairs)

        reporter.add(f"已报名讲座数: {len(user_chairs)}")
        reporter.add(
            f"过滤结果: 已报名={len(filtered_result['enrolled'])}, "
            f"时间冲突={len(filtered_result['conflict'])}, "
            f"屏蔽校区={len(filtered_result['nanjing']) + len(filtered_result['hefei'])}"
            f"（南京{len(filtered_result['nanjing'])}/合肥{len(filtered_result['hefei'])}）"
        )

        # 预热模式：即将开放（not_started）的讲座也是目标——整点前它们还显示"未到时间"，
        # 只取 applyBeginTime 最早那一分钟的（更晚开放的留给下一次运行），且剔除与已报名冲突的
        targets = filtered_result["available"]
        fire_t = None
        if prewarm and filtered_result["not_started"]:
            opening = [
                ch for ch in filtered_result["not_started"]
                if not LectureFilter.check_time_conflict(ch["chairBeginTime"], ch["chairEndTime"], user_chairs)
            ]
            if opening:
                earliest = min(ch["applyBeginTime"] for ch in opening)
                fire_t = _parse_apply_time(earliest)
                targets = targets + [ch for ch in opening if ch["applyBeginTime"] == earliest]
                reporter.add(f"⏰ 预热模式：等待 {earliest} 开放即抢（{len(targets)} 个目标）")

        result = {"account": account_name, "success": 0, "fail": 0}
        if not targets and not any(filtered_result.values()):
            reporter.add("⚠️ 未扫描到可报名讲座")
        else:
            reporter.capture_print(print_all_categories, filtered_result)
            if targets:
                logger.info(f"[{account_name}] 开火：{len(targets)} 个讲座开始报名")
                signup = LectureSignup(session, account_name, API_BASE, reporter, token_manager, scanner)
                if prewarm and fire_t is not None:
                    from config import PREWARM_MAX_WAIT
                    wait_s = fire_t - time.time()
                    if wait_s > PREWARM_MAX_WAIT:
                        logger.info(
                            f"[{account_name}] 开放时刻还有 {wait_s/60:.0f} 分钟（>{PREWARM_MAX_WAIT//60} 分钟上限），"
                            f"放弃卡点等待，按普通模式直接开火"
                        )
                        reporter.add(f"ℹ️ 距开放还有 {wait_s/60:.0f} 分钟，跳过卡点等待（普通模式）")
                        fire_t = None  # 降级
                    else:
                        warm_n = min(CONCURRENT_SIGNUP_THREADS, len(build_conflict_groups(targets))) + 1
                        _prewarm_wait_and_fire(signup, account_name, warm_n, fire_t)
                        res = signup.batch_signup(targets, dry_run=dry_run, skip_warmup=True)
                if fire_t is None:  # 普通模式或预热降级：直接开火
                    res = signup.batch_signup(targets, dry_run=dry_run)
                result = {
                    "account": account_name,
                    "success": len(res["success"]),
                    "fail": len(res["fail"]),
                    "fail_reasons": res.get("fail_reasons", []),
                    "success_titles": [item["title"] for item in res["success"]],
                }

        result["report_text"] = reporter.render(result)
        result["scanner"] = scanner  # 全部账号报完后统一刷新+导出，不占抢票关键路径
        return result

    except RuntimeError as e:
        logger.error(f"[{account_name}] {e}")
        result = {"account": account_name, "success": 0, "fail": 0, "error": str(e)[:80]}
        reporter.add(c(f"❌ 运行失败: {str(e).splitlines()[-1]}", Colors.RED))
        result["report_text"] = reporter.render(result)
        return result
    except Exception as e:
        logger.error(f"[{account_name}] {type(e).__name__}: {e}")
        result = {"account": account_name, "success": 0, "fail": 0, "error": f"{type(e).__name__}: {str(e)[:50]}"}
        reporter.add(f"异常: {type(e).__name__}: {e}")
        result["report_text"] = reporter.render(result)
        return result


def _export_enrolled_report(account_name: str, user_chairs: List[Dict]):
    """导出当前已报名讲座到 results/{账号}.json。"""
    report = {
        "account": account_name,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(user_chairs),
        "lectures": user_chairs,
    }
    try:
        out_path = os.path.join(RESULTS_DIR, f"{account_name}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"[{account_name}] 已报名报告已导出: {out_path}")
    except Exception as e:
        logger.warning(f"[{account_name}] 导出已报名报告失败: {e}")


def _refresh_and_export(results: List[Dict]):
    """所有账号报名结束后统一刷新已报名列表并导出报告，不占抢票关键路径。"""
    for r in results:
        scanner = r.pop("scanner", None)
        if scanner is None:
            continue
        try:
            user_chairs = scanner.fetch_user_chairs()
            _export_enrolled_report(r["account"], user_chairs)
        except Exception as e:
            logger.warning(f"[{r['account']}] 刷新/导出失败: {e}")


def _run_concurrent_accounts(accounts: Dict[str, Dict], dry_run: bool = DRY_RUN, prewarm: bool = False) -> List[Dict]:
    results: List[Dict] = []
    shared_chairs = _SharedFutureChairs()

    def _wrapped_run(account_name: str, id_token: str):
        return _run_single_account(account_name, id_token, dry_run, shared_chairs, prewarm)

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
                with output_lock:  # 防止与工作线程的 capture_print 重定向互踩
                    print(result.get("report_text", ""))
            except Exception as e:
                print(f"\n{'=' * 72}\n📦 账号：{name}\n{'=' * 72}")
                print(f"❌ 任务异常: {e}\n")
                logger.error(f"💥 任务异常：{e}")

    _refresh_and_export(results)
    return results


def _print_summary_report(results: List[Dict]):
    print(f"\n{Colors.CYAN}{'=' * 50}{Colors.RESET}")
    print(f"📊 汇总：成功 {sum(r.get('success', 0) for r in results)} | 失败 {sum(r.get('fail', 0) for r in results)}")
    for r in results:
        err = r.get("error")
        extra = f" | {err}" if err else ""
        print(f"  {r['account']}: ok={r.get('success', 0)} fail={r.get('fail', 0)}{extra}")

    all_fail_reasons = []
    for r in results:
        fail_reasons = r.get("fail_reasons", [])
        for fr in fail_reasons:
            all_fail_reasons.append(f"[{r['account']}] {fr['title']}: {fr['reason']}")

    if all_fail_reasons:
        print(f"\n{Colors.YELLOW}--- 失败详情 ---{Colors.RESET}")
        for reason in all_fail_reasons:
            print(f"  ❌ {reason}")

    print(f"{Colors.CYAN}{'=' * 50}{Colors.RESET}")


def _push_serverchan(results: List[Dict]):
    """报名完成后通过 Server酱 推送结果到微信（无报名动作且无错误时不推送）。"""
    if not SERVERCHAN_ENABLED:
        return

    key = os.environ.get("SERVERCHAN_KEY") or SERVERCHAN_KEY
    if not key:
        print(c("⚠️ SERVERCHAN_ENABLED=True 但未配置 SERVERCHAN_KEY，跳过推送", Colors.YELLOW))
        return

    total_success = sum(r.get("success", 0) for r in results)
    total_fail = sum(r.get("fail", 0) for r in results)
    has_error = any(r.get("error") for r in results)

    if total_success == 0 and total_fail == 0 and not has_error:
        logger.debug("无报名结果且无错误，跳过推送")
        return

    dry_tag = " [模拟]" if DRY_RUN else ""
    title = f"讲座报名{dry_tag} 成功{total_success} | 失败{total_fail}"

    lines = [f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{dry_tag}", ""]
    for r in results:
        lines.append(f"### {r['account']}")
        if r.get("error"):
            lines.append(f"- ❌ 运行失败: {r['error']}")
        for t in r.get("success_titles", []):
            lines.append(f"- ✅ {t}")
        for fr in r.get("fail_reasons", []):
            lines.append(f"- ❌ {fr['title']}: {fr['reason']}")
        if not (r.get("error") or r.get("success_titles") or r.get("fail_reasons")):
            lines.append("- ℹ️ 无可报名讲座")
        lines.append("")

    try:
        resp = requests.post(
            f"https://sctapi.ftqq.com/{key}.send",
            data={"title": title, "desp": "\n".join(lines)},
            timeout=10
        )
        if resp.json().get("code") == 0:
            print(c("📲 Server酱通知已发送", Colors.GREEN))
        else:
            print(c(f"⚠️ Server酱推送失败: {resp.text[:80]}", Colors.YELLOW))
            logger.warning(f"Server酱推送失败: {resp.text[:200]}")
    except Exception as e:
        print(c(f"⚠️ Server酱推送异常: {e}", Colors.YELLOW))
        logger.warning(f"Server酱推送异常: {e}")


def _cancel_chair(account: str, chair_id: str) -> int:
    """验收清理工具：python main.py --cancel 账号 chairId"""
    enable_win_colors()
    accounts = discover_captured_accounts()
    info = accounts.get(account)
    if not info:
        print(c(f"未找到账号 {account} 的缓存", Colors.RED))
        return 2

    session = _build_session()
    token_manager = TokenManager(account, AUTH_DIR, session)
    if not token_manager.ensure_auth(info["id_token_full"]):
        print(c("认证失败，请先重抓 Token", Colors.RED))
        return 2

    scanner = LectureScanner(session, account, token_manager)
    signup = LectureSignup(session, account, API_BASE, None, token_manager, scanner)
    ok = signup.cancel_single(chair_id)
    print(c(f"✅ 已取消 {chair_id}" if ok else f"❌ 取消失败 {chair_id}", Colors.GREEN if ok else Colors.RED))
    return 0 if ok else 1


def main():
    enable_win_colors()
    print(c("\n🎓 讲座管理脚本 - 分层输出版", Colors.CYAN))
    print(f"DRY_RUN={DRY_RUN}  MULTI_ACCOUNT={MULTI_ACCOUNT_MODE}  DEBUG={DEBUG_MODE}  PREWARM={PREWARM_MODE}")
    print(f"日志文件: {RUN_LOG_FILE}")
    print("-" * 50)

    if INCLUDE_BLOCKED_CAMPUS:
        print(c("⚠️ 测试模式：南京/合肥校区讲座不屏蔽，可用于练手", Colors.YELLOW))
    else:
        print(c("🔍 当前配置：南京/合肥校区讲座自动屏蔽", Colors.GREEN))

    accounts = discover_captured_accounts()

    if not accounts:
        print(c("⚠️ 未发现 auth_cache_*.json 缓存文件", Colors.YELLOW))
        print(c("💡 请先运行抓 Token 脚本生成 cache/auth_cache_*.json", Colors.CYAN))
        sys.exit(2)

    if TARGET_ACCOUNTS:
        filtered = {k: v for k, v in accounts.items() if k in TARGET_ACCOUNTS}
        missing = [a for a in TARGET_ACCOUNTS if a not in accounts]
        if missing:
            print(c(f"指定账号未找到缓存: {missing}", Colors.YELLOW))
        if not filtered:
            print(c("指定的账号均未找到缓存文件", Colors.RED))
            sys.exit(2)
        accounts = filtered
        print(c(f"🎯 指定账号模式：仅运行 {list(accounts.keys())}", Colors.CYAN))

    print(f"{Colors.GREEN}✅ 发现 {len(accounts)} 个账号缓存:{Colors.RESET}")
    for name, info in accounts.items():
        captured_at = info.get("captured_at") or "unknown"
        token_preview = info.get("id_token", "")
        print(f"   🔹 {name}: captured_at={captured_at} | Token: {token_preview}")

    if not MULTI_ACCOUNT_MODE:
        names = list(accounts.keys())
        target = names[0]

        if len(accounts) > 1:
            print(f"\n{Colors.YELLOW} 发现多个账号，但 MULTI_ACCOUNT_MODE=False{Colors.RESET}")
            choice = input(f"请输入要运行的账号名称 (回车默认运行 {names[0]}): ").strip()
            if choice and choice in accounts:
                target = choice

        print(f"\n{Colors.BLUE} 运行账号：{target}{Colors.RESET}")
        result = _run_single_account(
            target,
            accounts[target]["id_token_full"],
            dry_run=DRY_RUN,
            prewarm=PREWARM_MODE
        )
        with output_lock:
            print(result.get("report_text", ""))
        _refresh_and_export([result])
        _print_summary_report([result])
        _push_serverchan([result])
        return

    print(f"\n{Colors.CYAN}🚀 启动多账号并发模式 (最大并发:{CONCURRENT_ACCOUNTS}){Colors.RESET}")
    results = _run_concurrent_accounts(accounts, dry_run=DRY_RUN, prewarm=PREWARM_MODE)
    _print_summary_report(results)
    _push_serverchan(results)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--cancel":
        sys.exit(_cancel_chair(sys.argv[2], sys.argv[3]))
    main()
