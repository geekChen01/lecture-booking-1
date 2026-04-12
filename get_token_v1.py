# -*- coding: utf-8 -*-
import atexit
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple

import requests

from config1 import (
    BASE_DIR, LOG_DIR, CACHE_DIR, AUTH_DIR,
    SUMMARY_FILE,
    MITMDUMP_PATH, PROXY_ADDR, PROXY_PORT,
    CHECK_INTERVAL, CACHE_EXPIRE_HOURS,
    TARGET_KEYWORD, DEBUG_MODE,
)
from auth import ProxyManager


def setup_main_logger() -> logging.Logger:
    logger = logging.getLogger("auto_mitmdump_main")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )

    logger.addHandler(console_handler)
    logger.propagate = False
    return logger


def setup_addon_logger() -> logging.Logger:
    logger = logging.getLogger("auto_mitmdump_addon")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)
    stream_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )

    logger.addHandler(stream_handler)
    logger.propagate = False
    return logger


logger = setup_main_logger()


def sanitize_account_name(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name)


def safe_json_load(path: str, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"读取 JSON 失败: {path} | {e}")
    return default


def force_kill_all_mitmdump() -> None:
    try:
        subprocess.run(
            "taskkill /F /IM mitmdump.exe >nul 2>&1",
            shell=True,
            check=False
        )
    except Exception as e:
        logger.debug(f"兜底清理 mitmdump 失败：{e}")


class TokenGrabber:
    def __init__(self):
        self.addon_logger = setup_addon_logger()
        self.event_file = os.path.join(CACHE_DIR, "token_capture_event.txt")

    def _notify_capture(self, account: str, cache_file: str, token_preview: str):
        try:
            with open(self.event_file, "w", encoding="utf-8") as f:
                f.write(f"{account}|{cache_file}|{token_preview}")
        except Exception as e:
            self.addon_logger.warning(f"写入事件文件失败: {e}")

    def request(self, flow):
        if DEBUG_MODE and TARGET_KEYWORD in flow.request.pretty_url:
            self.addon_logger.info(
                f"[HIT-REQ] {flow.request.method} {flow.request.pretty_url}"
            )

    def response(self, flow):
        if TARGET_KEYWORD not in flow.request.pretty_url:
            return

        self.addon_logger.info(
            f"[HIT-RESP] {flow.request.method} {flow.request.pretty_url}"
        )

        if flow.response.status_code != 200:
            self.addon_logger.warning(f"非 200 响应：{flow.response.status_code}")
            return

        try:
            data = flow.response.json()
        except json.JSONDecodeError as e:
            self.addon_logger.error(f"JSON 解析失败：{e}")
            self.addon_logger.error(f"响应前 300 字符：{flow.response.text[:300]}")
            return
        except Exception as e:
            self.addon_logger.error(f"解析响应失败：{e}", exc_info=True)
            return

        if DEBUG_MODE:
            try:
                self.addon_logger.debug(
                    "原始响应 (前 500 字符): %s",
                    json.dumps(data, ensure_ascii=False)[:500]
                )
            except Exception:
                pass

        if data.get("code") != "00000":
            self.addon_logger.warning(
                f"业务失败: code={data.get('code')}, msg={data.get('msg')}"
            )
            return

        resp_data = data.get("data", {})
        login_account = resp_data.get("loginAccount")
        id_token = resp_data.get("id_token", "")
        user_id = resp_data.get("userId")

        if not login_account:
            self.addon_logger.error("响应中无 loginAccount，无法区分账号")
            return

        if not id_token or len(id_token) < 50:
            self.addon_logger.error(
                f"id_token 格式异常，长度={len(id_token) if id_token else 0}"
            )
            return

        safe_name = sanitize_account_name(login_account)
        cache_file = os.path.join(AUTH_DIR, f"auth_cache_{safe_name}.json")

        old_cache = {}
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    old_cache = json.load(f)
            except Exception as e:
                self.addon_logger.warning(f"读取旧 cache 失败：{cache_file} | {e}")

        old_token = old_cache.get("id_token")

        if not old_cache:
            action = "新增"
        elif old_token == id_token:
            action = "重复捕获"
        else:
            action = "更新"

        cache = {
            "loginAccount": login_account,
            "id_token": id_token,
            "userId": user_id,
            "captured_at": datetime.now().isoformat(),
            "expire_at": (
                datetime.now() + timedelta(hours=CACHE_EXPIRE_HOURS)
            ).isoformat(),
            "raw_response": resp_data if DEBUG_MODE else None,
        }

        try:
            os.makedirs(AUTH_DIR, exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.addon_logger.error(f"写入 cache 失败：{cache_file} | {e}", exc_info=True)
            return

        self._update_summary(login_account, cache_file)

        token_preview = id_token[:20]
        self.addon_logger.info(
            f"{action} Token: 账号={login_account} | "
            f"文件={cache_file} | Token前20位={token_preview}..."
        )

        self._notify_capture(login_account, cache_file, token_preview)

    def _update_summary(self, account: str, cache_file: str):
        summary = {}
        if os.path.exists(SUMMARY_FILE):
            try:
                with open(SUMMARY_FILE, "r", encoding="utf-8") as f:
                    summary = json.load(f)
            except Exception as e:
                self.addon_logger.warning(f"读取汇总失败：{e}")

        summary[account] = {
            "cache_file": cache_file,
            "captured_at": datetime.now().isoformat(),
            "status": "active",
        }

        try:
            with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.addon_logger.warning(f"更新汇总失败：{e}")


addons = [TokenGrabber()]


class MitmdumpManager:
    def __init__(self, log_file: str = None):
        self.process: Optional[subprocess.Popen] = None
        self.log_fp = None
        self.proxy_manager = ProxyManager()
        self.original_proxy_state = self.proxy_manager.is_enabled()
        self.log_file = log_file or MITMDUMP_STDOUT_LOG

    def _wait_port_release(self, timeout: int = 8) -> bool:
        for _ in range(timeout):
            if self._get_listening_process() is None:
                return True
            time.sleep(1)
        return False

    def _get_listening_process(self) -> Optional[Tuple[str, str]]:
        try:
            output = subprocess.check_output(
                f'netstat -ano | findstr :{PROXY_PORT} | findstr LISTENING',
                shell=True,
                text=True
            ).strip()
            if not output:
                return None

            parts = output.split()
            if len(parts) < 5:
                return None

            pid = parts[-1]
            proc_output = subprocess.check_output(
                f'tasklist /FI "PID eq {pid}" /NH',
                shell=True,
                text=True
            ).strip()

            if proc_output:
                proc_parts = proc_output.split()
                if len(proc_parts) >= 1:
                    return pid, proc_parts[0]
        except Exception:
            return None
        return None

    def _port_ready(self) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                return s.connect_ex((PROXY_ADDR, PROXY_PORT)) == 0
        except Exception:
            return False

    def _dump_last_log(self, path: str, lines: int = 50):
        try:
            if not os.path.exists(path):
                return
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                tail = f.readlines()[-lines:]
            if tail:
                logger.error("mitmdump 日志最后 %s 行:\n%s", lines, "".join(tail))
        except Exception as e:
            logger.error(f"读取 mitmdump 日志失败: {e}")

    def start(self) -> bool:
        try:
            logger.info("正在清理所有 mitmdump 进程...")
            force_kill_all_mitmdump()

            logger.info("等待端口释放...")
            if self._wait_port_release(timeout=8):
                logger.info("端口已释放")
            else:
                logger.warning("端口可能仍被占用，继续尝试启动")

            if not os.path.exists(MITMDUMP_PATH):
                logger.error(f"mitmdump.exe 不存在: {MITMDUMP_PATH}")
                return False

            cmd = [
                MITMDUMP_PATH,
                "-s", os.path.abspath(__file__),
                "--listen-host", PROXY_ADDR,
                "--listen-port", str(PROXY_PORT),
                "--no-http2",
                "--set", "flow_detail=3"
            ]

            logger.info(f"启动 mitmdump: {' '.join(cmd)}")

            self.log_fp = open(self.log_file, "a", encoding="utf-8", buffering=1)
            CREATE_NO_WINDOW = 0x08000000
            DETACHED_PROCESS = 0x00000008

            self.process = subprocess.Popen(
                cmd,
                stdout=self.log_fp,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
                cwd=BASE_DIR
            )

            logger.info(f"mitmdump 进程已创建 (PID: {self.process.pid})")

            for attempt in range(8):
                time.sleep(1)

                if self.process.poll() is not None:
                    logger.error(f"mitmdump 提前退出，退出码: {self.process.returncode}")
                    self._dump_last_log(self.log_file)
                    return False

                info = self._get_listening_process()
                if info and info[1].lower() == "mitmdump.exe" and self._port_ready():
                    logger.info(f"端口 {PROXY_PORT} 已被 mitmdump.exe (PID {info[0]}) 监听")
                    break
            else:
                logger.error("端口未启动或被其他程序占用")
                self._dump_last_log(self.log_file)
                return False

            logger.info("执行代理健康检查...")
            proxies = {
                "http": f"http://{PROXY_ADDR}:{PROXY_PORT}",
                "https": f"http://{PROXY_ADDR}:{PROXY_PORT}",
            }

            for idx in range(1, 4):
                try:
                    resp = requests.get(
                        "https://mitm.it",
                        proxies=proxies,
                        timeout=8,
                        verify=False
                    )
                    if resp.status_code == 200 and "mitmproxy" in resp.text.lower():
                        logger.info(f"✅ 代理健康检查通过 (第 {idx} 次)")
                        return True
                    logger.warning(
                        f"健康检查未通过 (第 {idx} 次): status={resp.status_code}"
                    )
                except Exception as e:
                    logger.warning(f"健康检查失败 (第 {idx} 次): {e}")
                time.sleep(1)

            logger.error("健康检查最终失败")
            self._dump_last_log(self.log_file)
            return False

        except Exception as e:
            logger.error(f"启动异常: {e}", exc_info=True)
            return False

    def stop(self):
        try:
            if self.process:
                logger.info("正在停止 mitmdump 进程...")
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    logger.warning("terminate 未退出，执行 kill")
                    self.process.kill()
                    try:
                        self.process.wait(timeout=2)
                    except Exception:
                        pass
                self.process = None

            force_kill_all_mitmdump()

            if self._wait_port_release(timeout=5):
                logger.info("mitmdump 已彻底清理，端口已释放")
            else:
                logger.warning("mitmdump 已尝试清理，但端口可能未完全释放")

        except Exception as e:
            logger.error(f"停止失败: {e}", exc_info=True)
        finally:
            if self.log_fp:
                try:
                    self.log_fp.close()
                except Exception:
                    pass
                self.log_fp = None

    def __enter__(self):
        if not self.start():
            raise RuntimeError("启动 mitmdump 失败")

        if not self.original_proxy_state:
            if not self.proxy_manager.enable():
                self.stop()
                raise RuntimeError("开启系统代理失败")
        else:
            logger.warning("系统代理原本已开启，脚本退出时将保持原状态")

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        logger.info("正在清理资源...")
        if not self.original_proxy_state:
            self.proxy_manager.disable()
        else:
            logger.info("系统代理保持原状态")
        self.stop()

        if exc_type:
            logger.error(f"异常退出: {exc_type.__name__}: {exc_val}")
        else:
            logger.info("正常退出")
        return False


def cleanup_on_exit():
    try:
        force_kill_all_mitmdump()
    except Exception:
        pass


def _signal_handler(signum, frame):
    logger.warning(f"收到退出信号: {signum}")
    raise KeyboardInterrupt


def main():
    atexit.register(cleanup_on_exit)

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _signal_handler)
            except Exception:
                pass

    mitmdump_log_file = os.path.join(
        LOG_DIR,
        f"mitmdump_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )

    print("=" * 60)
    print("自动化 mitmdump 启动脚本")
    print("=" * 60)
    print(f"mitmdump日志    : {mitmdump_log_file}")
    print(f"监听端口        : {PROXY_PORT}")
    print(f"mitmdump 路径   : {MITMDUMP_PATH}")
    print(f"缓存目录        : {CACHE_DIR}")
    print("=" * 60)
    print()

    logger.info("脚本启动")

    try:
        with MitmdumpManager(log_file=mitmdump_log_file) as manager:
            start_time = time.time()
            status_count = 0
            event_file = os.path.join(CACHE_DIR, "token_capture_event.txt")
            last_event_time = 0

            logger.info("服务已就绪，现在可以打开微信小程序并触发登录接口")
            logger.info("Token 捕获日志请查看: %s", mitmdump_log_file)

            while True:
                time.sleep(1)

                if manager.process and manager.process.poll() is not None:
                    logger.error("mitmdump 进程意外退出")
                    break

                if os.path.exists(event_file):
                    try:
                        mtime = os.path.getmtime(event_file)
                        if mtime > last_event_time:
                            last_event_time = mtime
                            with open(event_file, "r", encoding="utf-8") as f:
                                content = f.read().strip()
                            if content:
                                parts = content.split("|")
                                if len(parts) == 3:
                                    account, cache_file, token_preview = parts
                                    logger.info(
                                        f"✅ 成功捕获 Token: 账号={account} | "
                                        f"文件={cache_file} | Token前20位={token_preview}..."
                                    )
                    except Exception as e:
                        logger.warning(f"读取事件文件失败: {e}")

                elapsed = int(time.time() - start_time)
                if elapsed > 0 and elapsed % CHECK_INTERVAL == 0:
                    status_count += 1
                    
                    cache_files = [
                        f for f in os.listdir(AUTH_DIR)
                        if f.startswith("auth_cache_") and f.endswith(".json")
                    ]
                    
                    mins, secs = divmod(elapsed, 60)
                    logger.info(
                        f"运行中... ({mins}分{secs}秒) | "
                        f"缓存账号数:{len(cache_files)} | "
                        f"进程 PID:{manager.process.pid if manager.process else 'N/A'} | "
                        f"状态检查#{status_count}"
                    )
                    start_time = time.time()

    except KeyboardInterrupt:
        logger.info("收到停止信号，准备退出")
    except Exception as e:
        logger.error(f"未处理异常: {type(e).__name__}: {e}", exc_info=True)
        return 1
    finally:
        cleanup_on_exit()

    logger.info("脚本结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
