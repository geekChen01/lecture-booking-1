import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests

from config import (
    FETCH_DELAY, BOOK_DELAY, MAX_PAGES,
    REQUEST_TIMEOUT, SIGNUP_TIMEOUT, MAX_RETRIES, SIGNUP_MAX_RETRIES, RETRY_DELAY,
    CONCURRENT_SIGNUP_THREADS, API_BASE, AUTO_REFRESH,
    INCLUDE_BLOCKED_CAMPUS, HEARTBEAT_TIMEOUT,
)
from auth import TokenManager

logger = logging.getLogger(__name__)


class AuthExpiredError(RuntimeError):
    """chair-token 与 id_token 双双失效：自动刷新救不回来，必须重跑 launcher.ps1 重抓。
    继承 RuntimeError，直接复用 main 里现有的异常处理与错误展示路径。"""


def _ms_str(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


class LectureScanner:
    def __init__(self, session: requests.Session, account_name: str, token_manager: TokenManager = None):
        self.session = session
        self.account_name = account_name
        self.token_manager = token_manager

    def _refresh_auth(self) -> bool:
        """扫描请求返回 401/403/A0230 时，用缓存 id_token 刷新一次 chair-token。"""
        if not (self.token_manager and AUTO_REFRESH):
            return False
        id_tok = self.token_manager.load().get("id_token")
        return bool(id_tok) and self.token_manager.refresh_token(id_tok)

    def _raise_auth_dead(self, code: str):
        """token 全过期（刷新失败或刷出来的仍被拒）：响亮报死，不再装成空列表。"""
        raise AuthExpiredError(
            f"token-timeout | token 全过期（code={code}，自动刷新失败）| 请运行 launcher.ps1 重抓 Token"
        )

    def fetch_future_chairs(self) -> Dict[str, List[Dict]]:
        now_ts = int(time.time() * 1000)

        available, full, not_started = [], [], []
        page = 1
        attempt = 0
        auth_retried = False

        while page <= MAX_PAGES:
            if page > 1:
                time.sleep(random.uniform(*FETCH_DELAY))
            try:
                resp = self.session.get(
                    f"{API_BASE}/pageChair.do",
                    params={"size": 100, "current": page, "lookCanApply": 0},
                    timeout=REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                resp.encoding = "utf-8"
                data = resp.json()

                code = str(data.get("code", ""))
                code = str(data.get("code", ""))
                if code in ("401", "403", "A0230"):
                    if not auth_retried and self._refresh_auth():
                        auth_retried = True
                        continue
                    self._raise_auth_dead(code)

                records = data.get("data", {}).get("records", [])
                if not records:
                    break

                hit_past = False
                for idx, item in enumerate(records):
                    chair_start = item.get("chairBeginTime", 0)
                    apply_start = item.get("applyBeginTime", 0)
                    if chair_start < now_ts:
                        hit_past = True  # 列表按时间倒序，此后全是已过期讲座
                        break

                    if page == 1 and idx == 0:
                        # 留证据：确认地点/结束时间字段名（调度按地点与时长排序依赖它们）
                        logger.debug(f"[{self.account_name}] pageChair 首条字段: {sorted(item.keys())}")

                    chair = {
                        "chairId": item["chairId"],
                        "title": item["chairName"],
                        "time": _ms_str(chair_start),
                        "teacher": item.get("teacherName", ""),
                        "applyBeginTime": _ms_str(apply_start),
                        "leave": item.get("leaveNum", 0),
                        # 原始时间戳与地点：冲突分组 / 一号院优先 / 时长短优先 的排序依据
                        "chairBeginTime": chair_start,
                        "chairEndTime": item.get("chairEndTime") or chair_start + 90 * 60 * 1000,
                        "address": item.get("chairSite") or item.get("chairAddress") or item.get("address") or "",
                    }

                    if apply_start > now_ts:
                        not_started.append(chair)
                    elif chair["leave"] > 0:
                        available.append(chair)
                    else:
                        full.append(chair)

                if hit_past:
                    break  # 过期讲座之后不再翻下一页

                attempt = 0
                if page >= data.get("data", {}).get("pages", 1):
                    break
                page += 1

            except AuthExpiredError:
                raise  # token 全过期不是网络故障：不做页级重试，直接上报账号级错误
            except Exception as e:
                attempt += 1
                logger.error(f"[{self.account_name}] 第{page}页请求失败(第{attempt}次)：{e}")
                if attempt >= MAX_RETRIES:
                    break
                time.sleep(RETRY_DELAY)

        available.sort(key=lambda x: x["applyBeginTime"])
        not_started.sort(key=lambda x: x["applyBeginTime"])
        full.sort(key=lambda x: x["applyBeginTime"])

        return {"available": available, "full": full, "not_started": not_started}

    def fetch_user_chairs(self) -> List[Dict]:
        user_chairs = []
        page = 1
        total_pages = 1
        attempt = 0
        auth_retried = False

        while page <= total_pages:
            if page > 1:
                time.sleep(random.uniform(*FETCH_DELAY))
            try:
                resp = self.session.get(
                    f"{API_BASE}/pageUserChair.do",
                    params={"size": 100, "current": page, "type": 1},
                    timeout=REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                resp.encoding = "utf-8"
                data = resp.json()

                code = str(data.get("code", ""))
                if code != "00000":
                    if code in ("401", "403", "A0230"):
                        if not auth_retried and self._refresh_auth():
                            auth_retried = True
                            continue
                        self._raise_auth_dead(code)
                    logger.warning(f"[{self.account_name}] 获取已报名讲座失败：code={code}")
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
                attempt = 0
                if page >= total_pages:
                    break
                page += 1

            except AuthExpiredError:
                raise  # token 全过期不是网络故障：不做页级重试，直接上报账号级错误
            except Exception as e:
                attempt += 1
                logger.error(f"[{self.account_name}] 第{page}页请求失败(第{attempt}次)：{e}")
                if attempt >= MAX_RETRIES:
                    break
                time.sleep(RETRY_DELAY)

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
        }

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
                    "chairBeginTime": chair["chairBeginTime"],
                    "chairEndTime": chair["chairEndTime"],
                    "address": chair.get("address", ""),
                    "reason": "",
                }

                if chair_id in enrolled_ids:
                    chair_info["reason"] = "已报名"
                    result["enrolled"].append(chair_info)
                    continue

                # 南京/合肥校区：一律屏蔽（无论已满/未开始/可报名）；仅测试脚本时放开
                if not INCLUDE_BLOCKED_CAMPUS:
                    if chair["title"].startswith("南京"):
                        chair_info["reason"] = "南京校区屏蔽"
                        result["nanjing"].append(chair_info)
                        continue
                    if chair["title"].startswith("合肥"):
                        chair_info["reason"] = "合肥校区屏蔽"
                        result["hefei"].append(chair_info)
                        continue

                if category == "full":
                    chair_info["reason"] = "名额已满"
                    result["full"].append(chair_info)
                    continue

                if category == "not_started":
                    chair_info["reason"] = "未到报名时间"
                    result["not_started"].append(chair_info)
                    continue

                chair_start = chair["chairBeginTime"]
                chair_end = chair["chairEndTime"]

                if LectureFilter.check_time_conflict(chair_start, chair_end, enrolled_chairs):
                    chair_info["reason"] = "时间冲突"
                    result["conflict"].append(chair_info)
                    continue

                result["available"].append(chair_info)

        for key in result:
            result[key].sort(key=lambda x: x["applyBeginTime"])

        return result


_CAMPUS_TEXT_RE = re.compile(r"([一二三1-3])\s*号院")
_ROOM_CODE_RE = re.compile(r"(?<![0-9])([13])[0-9]{2}\s*[-—－]")


def _campus_rank(chair: Dict) -> int:
    """0 = 一号院（优先），1 = 其他（三号院/未知）。
    依据地址或标题中的文字（一号院/1号院）或房间号（1xx- 为一号院，3xx- 为三号院）。"""
    text = f"{chair.get('address', '')}{chair.get('title', '')}"
    m = _CAMPUS_TEXT_RE.search(text)
    if m and m.group(1) in ("一", "1"):
        return 0
    m = _ROOM_CODE_RE.search(text)
    if m and m.group(1) == "1":
        return 0
    return 1


def build_conflict_groups(chairs: List[Dict]) -> List[List[Dict]]:
    """按时间重叠把候选讲座切成冲突组（连通分量）：跨组并行抢，组内串行兜底。
    组内排序：一号院优先 → 时长短优先（稳定排序，同名次保持报名时间先后）。"""
    ordered = sorted(chairs, key=lambda c: c["chairBeginTime"])
    groups: List[List[Dict]] = []
    current: List[Dict] = []
    max_end = 0
    for chair in ordered:
        if current and chair["chairBeginTime"] >= max_end:
            groups.append(current)
            current = []
            max_end = 0
        current.append(chair)
        max_end = max(max_end, chair["chairEndTime"])
    if current:
        groups.append(current)

    for group in groups:
        group.sort(key=lambda c: (_campus_rank(c), c["chairEndTime"] - c["chairBeginTime"]))
    return groups


class LectureSignup:
    def __init__(self, session: requests.Session, account_name: str, base_url: str, reporter, token_manager: TokenManager, scanner: LectureScanner):
        self.session = session
        self.account_name = account_name
        self.base_url = base_url
        self.reporter = reporter
        self.token_manager = token_manager
        self.scanner = scanner

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
        max_retries: int = MAX_RETRIES,
        timeout: Tuple[float, float] = REQUEST_TIMEOUT,
    ) -> Optional[Dict]:
        thread_id = threading.current_thread().name
        last_exception = None

        for attempt in range(max_retries):
            t0 = time.time()
            try:
                # 无全局锁：报名请求真并发；拥堵场景靠短超时+快速重试兜底
                if method.upper() == "POST":
                    resp = self.session.post(url, data=data, timeout=timeout)
                else:
                    resp = self.session.get(url, timeout=timeout)

                resp.encoding = "utf-8"
                logger.debug(
                    f"[{self.account_name}][{thread_id}] {url.rsplit('/', 1)[-1]}"
                    f"{('/' + str(data.get('chairId'))) if data and data.get('chairId') else ''}"
                    f" 第{attempt + 1}次尝试 成功 耗时{(time.time() - t0) * 1000:.0f}ms"
                )
                return resp.json()

            except Exception as e:
                # 超时/连接错误/JSON 解析失败（拥堵期典型故障）一律快速重试
                last_exception = e
                logger.warning(
                    f"[{self.account_name}][{thread_id}] 请求异常 (第{attempt + 1}次): "
                    f"{type(e).__name__}: {str(e)[:50]}"
                )
                if attempt < max_retries - 1:
                    time.sleep(RETRY_DELAY)

        logger.error(f"[{self.account_name}][{thread_id}] 请求失败（共尝试 {max_retries} 次）: {last_exception}")
        return None

    def apply_single(self, chair_id: str, _refresh_attempt: int = 0) -> Tuple[bool, str]:
        try:
            time.sleep(random.uniform(*BOOK_DELAY))

            r = self._make_request_with_retry(
                "POST",
                f"{self.base_url}/chairApply.do",
                data={"chairId": chair_id},
                timeout=SIGNUP_TIMEOUT,
                max_retries=SIGNUP_MAX_RETRIES,
            )

            if r is None:
                logger.warning(f"[{self.account_name}] POST 报名失败，尝试 GET 确认...")
                verified = self._verify_signup(chair_id)
                return verified, "请求失败" if not verified else ""

            code, msg = str(r.get("code", "")), r.get("msg", "")

            if code in ("401", "403", "A0230") and AUTO_REFRESH and _refresh_attempt < 2:
                stale = self.session.cookies.get("chair-personnel-token")
                id_tok = self.token_manager.load().get("id_token")
                if id_tok and self.token_manager.refresh_token(id_tok, stale_token=stale):
                    return self.apply_single(chair_id, _refresh_attempt + 1)

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
            user_chairs = self.scanner.fetch_user_chairs()
            return chair_id in [c["chairId"] for c in user_chairs]
        except Exception as e:
            logger.debug(f"[{self.account_name}] 验证报名状态失败：{e}")
            return False

    def _ping(self) -> Optional[float]:
        """迷你 GET：测往返延迟（秒），失败返回 None。"""
        try:
            t0 = time.time()
            self.session.get(
                f"{API_BASE}/pageChair.do",
                params={"size": 1, "current": 1},
                timeout=HEARTBEAT_TIMEOUT,
            )
            return time.time() - t0
        except Exception:
            return None

    def _warm_connections(self, n: int) -> int:
        """并发 n 个迷你 GET 建立 TLS 连接并逐一验证存活；返回确认存活数（可能 < n）。
        旧版吞掉异常即报"预热完成"是假验证（10-18 实战：4/5 账号预热超时却显示完成）。"""
        with ThreadPoolExecutor(max_workers=n) as ex:
            lats = list(ex.map(lambda _: self._ping(), range(n)))
        ok = sum(1 for l in lats if l is not None)
        if ok < n:  # 有连接没建成：立即补建一轮
            with ThreadPoolExecutor(max_workers=n - ok) as ex:
                lats += list(ex.map(lambda _: self._ping(), range(n - ok)))
            ok = sum(1 for l in lats if l is not None)
        good = sorted(l for l in lats if l is not None)
        best = f"{good[0] * 1000:.0f}ms" if good else "无"
        logger.info(f"[{self.account_name}] 连接池预热：{n} 条中 {ok} 条已验证存活（最快 {best}）")
        self._report(f"🔥 预热 {ok}/{n} 条连接已验证")
        return ok

    def heartbeat(self, n: int = 1) -> List[Optional[float]]:
        """保活心跳：对池内连接并发迷你 GET，返回各条往返延迟（失败为 None）。
        防服务器 keep-alive 闲置断连（典型 15~75s），同时实时测路径健康度。"""
        with ThreadPoolExecutor(max_workers=n) as ex:
            return list(ex.map(lambda _: self._ping(), range(n)))

    def batch_signup(self, chairs: List[Dict], dry_run: bool = True, skip_warmup: bool = False) -> Dict:
        res = {"success": [], "fail": [], "fail_reasons": []}
        groups = build_conflict_groups(chairs)

        if dry_run:
            for gi, group in enumerate(groups):
                label = chr(ord("A") + gi)
                first = group[0]
                backup = f"（组内备选 {len(group) - 1} 个）" if len(group) > 1 else ""
                self._report(f"[通道{label}] 优先抢: {first['title'][:30]} {backup}")
                self._report_status(True, "[模拟]")
                res["success"].append(first)
            return res

        return self._scheduled_signup(groups, skip_warmup=skip_warmup)

    def _scheduled_signup(self, groups: List[List[Dict]], skip_warmup: bool = False) -> Dict:
        """跨组（通道）并行；组内按 一号院→时长短 优先，前一目标失败（含重试）才发下一备选。"""
        res = {"success": [], "fail": [], "fail_reasons": []}
        completed_lock = threading.Lock()
        start_all = time.time()

        total = sum(len(g) for g in groups)
        self._report(f"⚡ 分组抢票：{len(groups)} 个通道并行，组内失败转备选（一号院优先/时长短优先）")
        self._report(f"讲座总数: {total}")
        if not skip_warmup:  # 预热模式已在开火前完成预热+验活，这里不再重复
            self._warm_connections(min(CONCURRENT_SIGNUP_THREADS, len(groups)) + 1)  # +1 给超时重发兜底

        def run_group(gi: int, group: List[Dict]):
            label = chr(ord("A") + gi)
            for rank, chair in enumerate(group, 1):
                title = chair["title"][:30]
                tag = "" if rank == 1 else "(备选) "
                start_time = time.time()
                success, reason = self.apply_single(chair["chairId"])
                elapsed = time.time() - start_time

                logger.info(
                    f"[{self.account_name}] [通道{label}] {tag}{chair['chairId']} "
                    f"{'成功' if success else '失败'} 耗时{elapsed:.2f}s"
                )
                if success:
                    self._report(f"[通道{label}] {tag}{title} | 成功 ({elapsed:.1f}s)")
                    with completed_lock:
                        res["success"].append(chair)
                    return

                self._report(
                    f"[通道{label}] {tag}{title} | 失败 ({elapsed:.1f}s)"
                    + (f" {reason}" if reason else "")
                    + (" → 转下一备选" if rank < len(group) else "")
                )
                with completed_lock:
                    res["fail"].append(chair)
                    res["fail_reasons"].append({"title": title, "reason": reason})

        with ThreadPoolExecutor(
            max_workers=min(CONCURRENT_SIGNUP_THREADS, len(groups)),
            thread_name_prefix="Group"
        ) as executor:
            futures = [executor.submit(run_group, gi, group) for gi, group in enumerate(groups)]
            for future in as_completed(futures):
                future.result()

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
