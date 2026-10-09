import json
import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests
import urllib3

from config import (
    FETCH_DELAY, BOOK_DELAY, MAX_PAGES,
    REQUEST_TIMEOUT, MAX_RETRIES, RETRY_DELAY,
    CONCURRENT_SIGNUP_THREADS, API_BASE, AUTO_REFRESH,
)
from auth import TokenManager

logger = logging.getLogger(__name__)


def _ms_str(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


class LectureScanner:
    def __init__(self, session: requests.Session, account_name: str):
        self.session = session
        self.account_name = account_name

    def fetch_future_chairs(self) -> Dict[str, List[Dict]]:
        now_ts = int(time.time() * 1000)

        available, full, not_started = [], [], []
        page = 1

        while page <= MAX_PAGES:
            time.sleep(random.uniform(*FETCH_DELAY))
            try:
                resp = self.session.get(
                    f"{API_BASE}/pageChair.do",
                    params={"size": 30, "current": page, "lookCanApply": 0},
                    timeout=10
                )
                resp.raise_for_status()
                resp.encoding = "utf-8"
                data = resp.json()
                records = data.get("data", {}).get("records", [])
                if not records:
                    break

                for item in records:
                    chair_start = item.get("chairBeginTime", 0)
                    apply_start = item.get("applyBeginTime", 0)
                    if chair_start < now_ts:
                        break

                    chair = {
                        "chairId": item["chairId"],
                        "title": item["chairName"],
                        "time": _ms_str(chair_start),
                        "teacher": item.get("teacherName", ""),
                        "applyBeginTime": _ms_str(apply_start),
                        "leave": item.get("leaveNum", 0)
                    }

                    if apply_start > now_ts:
                        not_started.append(chair)
                    elif chair["leave"] > 0:
                        available.append(chair)
                    else:
                        full.append(chair)

                if page >= data.get("data", {}).get("pages", 1):
                    break
                page += 1

            except Exception as e:
                logger.error(f"[{self.account_name}] 第{page}页请求失败：{e}")
                break

        available.sort(key=lambda x: x["applyBeginTime"])
        not_started.sort(key=lambda x: x["applyBeginTime"])
        full.sort(key=lambda x: x["applyBeginTime"])

        return {"available": available, "full": full, "not_started": not_started}

    def fetch_user_chairs(self) -> List[Dict]:
        user_chairs = []
        page = 1
        total_pages = 1

        while page <= total_pages:
            time.sleep(random.uniform(*FETCH_DELAY))
            try:
                resp = self.session.get(
                    f"{API_BASE}/pageUserChair.do",
                    params={"size": 10, "current": page, "type": 1},
                    timeout=10
                )
                resp.raise_for_status()
                resp.encoding = "utf-8"
                data = resp.json()

                if str(data.get("code")) != "00000":
                    logger.warning(f"[{self.account_name}] 获取已报名讲座失败：code={data.get('code')}")
                    break

                records = data.get("data", {}).get("records", [])
                if not records:
                    break

                for item in records:
                    begin_ms = item.get("chairBeginTime", 0)
                    end_ms = item.get("chairEndTime", 0)
                    chair_info = {
                        "title": item.get("chairName", ""),
                        "time": _ms_str(begin_ms) if begin_ms else "",
                        "endTime": _ms_str(end_ms) if end_ms else "",
                        "teacher": item.get("teacherName", ""),
                        "chairId": item.get("chairId", ""),
                        # 原始时间戳（程序内部用于冲突检测）
                        "chairBeginTime": begin_ms,
                        "chairEndTime": end_ms,
                    }
                    # 只保留有值的可选字段
                    addr = item.get("chairAddress", "")
                    if addr:
                        chair_info["address"] = addr
                    content = item.get("chairContent", "")
                    if content:
                        chair_info["content"] = content
                    user_chairs.append(chair_info)

                total_pages = data.get("data", {}).get("pages", 1)
                if page >= total_pages:
                    break
                page += 1

            except requests.exceptions.RequestException as e:
                logger.error(f"[{self.account_name}] 第{page}页请求失败：{e}")
                break
            except json.JSONDecodeError as e:
                logger.error(f"[{self.account_name}] 响应解析失败：{e}")
                break
            except Exception as e:
                logger.error(f"[{self.account_name}] 获取已报名讲座异常：{e}")
                break

        if user_chairs:
            logger.info(f"✓ 账号 [{self.account_name}] 获取到 {len(user_chairs)} 个已报名讲座")
        else:
            logger.info(f"ℹ️ 账号 [{self.account_name}] 未查询到已报名讲座")

        return user_chairs


class LectureFilter:
    @staticmethod
    def check_time_conflict(chair_start: int, chair_end: int, enrolled_list: List[Dict]) -> bool:
        for enrolled in enrolled_list:
            enrolled_start = enrolled.get("chairBeginTime", 0)
            enrolled_end = enrolled.get("chairEndTime", 0)
            if enrolled_start < chair_end and chair_start < enrolled_end:
                return True
        return False

    @staticmethod
    def filter_chairs(future_chairs: Dict[str, List[Dict]], enrolled_chairs: List[Dict]) -> Dict[str, List[Dict]]:
        result = {
            "available": [],
            "enrolled": [],
            "conflict": [],
            "nanjing": [],
            "hefei": [],
            "full": [],
            "not_started": [],
            "ending_soon": [],
        }

        now_ts = int(time.time() * 1000)
        enrolled_ids = {chair["chairId"] for chair in enrolled_chairs}

        for category, chairs in future_chairs.items():
            for chair in chairs:
                chair_id = chair["chairId"]
                chair_info = {
                    "chairId": chair_id,
                    "title": chair["title"],
                    "time": chair["time"],
                    "teacher": chair["teacher"],
                    "applyBeginTime": chair["applyBeginTime"],
                    "leave": chair["leave"],
                    "reason": "",
                }

                if chair_id in enrolled_ids:
                    chair_info["reason"] = "已报名"
                    result["enrolled"].append(chair_info)
                    continue

                if category == "full":
                    chair_info["reason"] = "名额已满"
                    result["full"].append(chair_info)
                    continue

                if category == "not_started":
                    chair_info["reason"] = "未到报名时间"
                    result["not_started"].append(chair_info)
                    continue

                chair_start = int(datetime.strptime(chair["time"], "%Y-%m-%d %H:%M").timestamp() * 1000)
                chair_end = chair_start + 90 * 60 * 1000

                if now_ts > chair_end - 60 * 60 * 1000:
                    chair_info["reason"] = "即将结束"
                    result["ending_soon"].append(chair_info)
                    continue

                if chair["title"].startswith("南京"):
                    chair_info["reason"] = "含南京关键词"
                    result["nanjing"].append(chair_info)
                    continue

                if chair["title"].startswith("合肥") or "合肥" in chair["title"]:
                    chair_info["reason"] = "含合肥关键词"
                    result["hefei"].append(chair_info)
                    continue

                if LectureFilter.check_time_conflict(chair_start, chair_end, enrolled_chairs):
                    chair_info["reason"] = "时间冲突"
                    result["conflict"].append(chair_info)
                    continue

                result["available"].append(chair_info)

        for key in result:
            result[key].sort(key=lambda x: x["applyBeginTime"])

        return result


class LectureSignup:
    def __init__(self, session: requests.Session, account_name: str, base_url: str, reporter, token_manager: TokenManager, scanner: LectureScanner):
        self.session = session
        self.account_name = account_name
        self.base_url = base_url
        self.reporter = reporter
        self.token_manager = token_manager
        self.scanner = scanner
        self._request_lock = threading.Lock()

    def _report(self, text: str):
        if self.reporter:
            self.reporter.add(text)

    def _report_status(self, success: bool, msg: str):
        if self.reporter:
            self.reporter.add_status(success, msg)

    def _make_request_with_retry(
        self,
        method: str,
        url: str,
        data: Dict = None,
        max_retries: int = MAX_RETRIES
    ) -> Optional[Dict]:
        thread_id = threading.current_thread().name
        last_exception = None

        for attempt in range(max_retries):
            try:
                with self._request_lock:
                    if method.upper() == "POST":
                        resp = self.session.post(url, data=data, timeout=REQUEST_TIMEOUT)
                    else:
                        resp = self.session.get(url, timeout=REQUEST_TIMEOUT)

                resp.encoding = "utf-8"
                return resp.json()

            except requests.exceptions.Timeout as e:
                last_exception = e
                logger.warning(f"[{self.account_name}][{thread_id}] 请求超时 (第{attempt + 1}次): {str(e)[:50]}")
                if attempt < max_retries - 1:
                    time.sleep(RETRY_DELAY)
                continue

            except requests.exceptions.ConnectionError as e:
                last_exception = e
                logger.error(f"[{self.account_name}][{thread_id}] 连接错误 (第{attempt + 1}次): {str(e)[:50]}")
                if attempt < max_retries - 1:
                    time.sleep(RETRY_DELAY * 2)
                continue

            except Exception as e:
                last_exception = e
                logger.debug(f"[{self.account_name}][{thread_id}] 请求异常 (第{attempt + 1}次): {e}")
                break

        logger.error(f"[{self.account_name}][{thread_id}] 请求失败（已重试 {max_retries} 次）: {last_exception}")
        return None

    def apply_single(self, chair_id: str) -> Tuple[bool, str]:
        try:
            time.sleep(random.uniform(*BOOK_DELAY))

            r = self._make_request_with_retry(
                "POST",
                f"{self.base_url}/chairApply.do",
                data={"chairId": chair_id}
            )

            if r is None:
                logger.warning(f"[{self.account_name}] POST 报名失败，尝试 GET 确认...")
                verified = self._verify_signup(chair_id)
                return verified, "请求失败" if not verified else ""

            code, msg = str(r.get("code", "")), r.get("msg", "")

            if code in ("401", "403") and AUTO_REFRESH:
                cache = self.token_manager.load()
                id_tok = cache.get("id_token")
                if id_tok and self.token_manager.refresh_token(id_tok):
                    return self.apply_single(chair_id)

            success = code in ("00000", "200") or any(k in msg for k in ["重复", "已报名", "已存在"])

            if success:
                return True, ""

            if ("超时" not in msg and "失败" not in msg):
                logger.info(f"[{self.account_name}] POST 结果不明确，尝试 GET 确认...")
                verified = self._verify_signup(chair_id)
                return verified, msg if not verified else ""

            return False, msg

        except Exception as e:
            logger.debug(f"[{self.account_name}] 报名异常：{e}")
            return False, str(e)[:50]

    def _verify_signup(self, chair_id: str) -> bool:
        try:
            time.sleep(random.uniform(0.3, 0.6))
            user_chairs = self.scanner.fetch_user_chairs()
            enrolled_ids = [c["chairId"] for c in user_chairs]
            return chair_id in enrolled_ids
        except Exception as e:
            logger.debug(f"[{self.account_name}] 验证报名状态失败：{e}")
            return False

    def batch_signup(self, chairs: List[Dict], dry_run: bool = True, concurrent: bool = True) -> Dict:
        res = {"success": [], "fail": [], "fail_reasons": []}

        if dry_run:
            for item in chairs:
                self._report(f"[{self.account_name}] {item['title'][:30]}...")
                self._report_status(True, "[模拟]")
                res["success"].append(item)
            return res

        if concurrent and len(chairs) > 1:
            return self._batch_signup_concurrent(chairs)

        for item in chairs:
            title = item["title"][:30]
            start_time = time.time()

            success, reason = self.apply_single(item["chairId"])
            elapsed = time.time() - start_time

            if success:
                self._report_status(True, f"[{self.account_name}] {title}... 成功 ({elapsed:.1f}s)")
                res["success"].append(item)
            else:
                self._report_status(False, f"[{self.account_name}] {title}... 失败 ({elapsed:.1f}s)")
                res["fail"].append(item)
                res["fail_reasons"].append({"title": title, "reason": reason})

        return res

    def _batch_signup_concurrent(self, chairs: List[Dict]) -> Dict:
        res = {"success": [], "fail": [], "fail_reasons": []}
        semaphore = threading.Semaphore(CONCURRENT_SIGNUP_THREADS)

        total = len(chairs)
        completed = 0
        completed_lock = threading.Lock()
        start_all = time.time()

        self._report(f"⚡ 并发抢票模式启动 ({CONCURRENT_SIGNUP_THREADS} 线程)")
        self._report(f"讲座总数: {total}")

        def signup_task(item: Dict, index: int) -> Tuple[bool, float, str, str]:
            nonlocal completed
            thread_name = f"Worker-{index + 1}"

            with semaphore:
                title = item["title"][:30]
                start_time = time.time()

                try:
                    success, reason = self.apply_single(item["chairId"])
                    elapsed = time.time() - start_time

                    with completed_lock:
                        completed += 1
                        progress = f"[{completed}/{total}]"

                    msg = f"[{thread_name}] {title} | {'成功' if success else '失败'} ({elapsed:.1f}s) {progress}"
                    return success, elapsed, msg, reason

                except Exception as e:
                    elapsed = time.time() - start_time
                    with completed_lock:
                        completed += 1
                        progress = f"[{completed}/{total}]"

                    msg = f"[{thread_name}] {title} | 异常 ({elapsed:.1f}s) [{str(e)[:20]}] {progress}"
                    return False, elapsed, msg, str(e)[:50]

        with ThreadPoolExecutor(
            max_workers=CONCURRENT_SIGNUP_THREADS,
            thread_name_prefix="Signup"
        ) as executor:
            futures = {
                executor.submit(signup_task, item, idx): item
                for idx, item in enumerate(chairs)
            }

            for future in as_completed(futures):
                item = futures[future]
                try:
                    success, elapsed, msg, reason = future.result(timeout=30)
                    self._report(msg)
                    if success:
                        res["success"].append(item)
                    else:
                        res["fail"].append(item)
                        res["fail_reasons"].append({"title": item["title"][:30], "reason": reason})
                except Exception as e:
                    self._report_status(False, f"超时/异常: {str(e)[:25]}")
                    res["fail"].append(item)
                    res["fail_reasons"].append({"title": item["title"][:30], "reason": str(e)[:50]})

        total_elapsed = time.time() - start_all
        self._report(
            f"📊 抢票完成 | 总耗时: {total_elapsed:.1f}s | "
            f"成功: {len(res['success'])} | 失败: {len(res['fail'])}"
        )
        logger.info(f"✅ [{self.account_name}] 抢票完成：成功 {len(res['success'])} | 失败 {len(res['fail'])}")

        return res

    def cancel_single(self, chair_id: str) -> bool:
        try:
            resp = self.session.post(f"{self.base_url}/chairElect.do", data={"chairId": chair_id}, timeout=10)
            resp.encoding = "utf-8"
            r = resp.json()
            return str(r.get("code")) == "00000" and r.get("data") is True
        except Exception:
            return False

    def batch_cancel(self, chairs: List[Dict], indices: Optional[List[int]] = None):
        targets = chairs if not indices else [chairs[i - 1] for i in indices if 0 < i <= len(chairs)]
        for item in targets:
            ok = self.cancel_single(item["chairId"])
            self._report_status(ok, f"[{self.account_name}] 取消：{item['title'][:25]}...")
