import ctypes
import json
import logging
import os
import threading
import winreg
from datetime import datetime
from typing import Dict

import requests

from config import AUTO_REFRESH, PERSONNEL_LOGIN_URL, API_BASE, PROXY_ADDR, PROXY_PORT, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)


class TokenManager:
    def __init__(self, account_name: str, auth_dir: str, session: requests.Session):
        self.account_name = account_name
        self.cache_file = os.path.join(auth_dir, f"auth_cache_{account_name}.json")
        self.session = session
        self._refresh_lock = threading.Lock()

    def load(self) -> Dict:
        if not os.path.exists(self.cache_file):
            return {}
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                cache = json.load(f)
            return cache
        except Exception:
            pass
        return {}

    def save(self, chair_token: str, id_token: str = ""):
        # 保留旧缓存中的额外字段（userId, raw_response 等）
        old_cache = {}
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    old_cache = json.load(f)
            except Exception:
                pass

        cache = {
            "chair_token": chair_token,
            "id_token": id_token,
            "loginAccount": self.account_name,
            "captured_at": datetime.now().isoformat(),
        }
        # 保留旧缓存中的扩展字段
        for key in ("userId", "raw_response"):
            if key in old_cache:
                cache[key] = old_cache[key]

        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

    def validate_token(self, token: str) -> bool:
        try:
            self.session.cookies.set("chair-personnel-token", token)
            resp = self.session.get(
                f"{API_BASE}/pageChair.do",
                params={"size": 1, "current": 1},
                timeout=REQUEST_TIMEOUT
            )
            return resp.status_code == 200 and str(resp.json().get("code")) == "00000"
        except Exception:
            return False

    def refresh_token(self, id_token: str, stale_token: str = None) -> bool:
        """单飞刷新：并发调用时第一个线程真正刷新，其余线程直接复用刷新结果。"""
        with self._refresh_lock:
            if stale_token is not None:
                cached = self.load().get("chair_token")
                if cached and cached != stale_token:
                    # 别的线程已刷新出更新的 token，直接复用
                    self.session.cookies.set("chair-personnel-token", cached)
                    logger.info(f"[{self.account_name}] 复用其他线程刷新的 chair-token")
                    return True

            logger.info(f"[{self.account_name}] 用 id_token 刷新 chair-token...")
            try:
                resp = self.session.post(PERSONNEL_LOGIN_URL, data={"accessToken": id_token}, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                new_token = resp.cookies.get("chair-personnel-token") or resp.json().get("data", {}).get("token")
                if new_token:
                    self.session.cookies.set("chair-personnel-token", new_token)
                    self.save(new_token, id_token)
                    return True
            except Exception as e:
                logger.error(f"[{self.account_name}] 刷新失败：{e}")
            return False

    def try_fallback_chair_token(self, fallback: str) -> bool:
        if not fallback or len(fallback) <= 20:
            return False
        if self.validate_token(fallback):
            self.save(fallback, fallback)
            logger.info(f"[{self.account_name}] 使用 fallback chair-token")
            return True
        return False

    def ensure_auth(self, fallback: str) -> bool:
        """乐观认证：有缓存 chair_token 直接使用（省一次预检往返）；
        token 已过期时由扫描/报名路径的 401/403 处理触发刷新。"""
        cache = self.load()

        chair_token = cache.get("chair_token")
        if chair_token:
            self.session.cookies.set("chair-personnel-token", chair_token)
            logger.debug(f"[{self.account_name}] 使用缓存 chair-token（乐观）")
            return True

        id_tok = cache.get("id_token") or fallback
        if AUTO_REFRESH and id_tok and len(id_tok) > 20:
            if self.refresh_token(id_tok):
                logger.info(f"[{self.account_name}] 用缓存 id_token 刷新成功")
                return True

        if self.try_fallback_chair_token(fallback):
            return True

        logger.error(f"[{self.account_name}] 认证失败：无有效 Token")
        return False


class ProxyManager:
    @staticmethod
    def is_enabled() -> bool:
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            )
            val, _ = winreg.QueryValueEx(key, "ProxyEnable")
            winreg.CloseKey(key)
            return val == 1
        except Exception as e:
            logger.debug(f"检查代理状态失败: {e}")
            return False

    @staticmethod
    def enable(proxy: str = f"{PROXY_ADDR}:{PROXY_PORT}") -> bool:
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                key_path,
                0,
                winreg.KEY_SET_VALUE
            )
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, proxy)
            winreg.CloseKey(key)

            ctypes.windll.wininet.InternetSetOptionW(0, 39, 0, 0)
            ctypes.windll.wininet.InternetSetOptionW(0, 37, 0, 0)

            logger.info(f"系统代理已开启: {proxy}")
            return True
        except Exception as e:
            logger.error(f"开启系统代理失败: {e}")
            return False

    @staticmethod
    def disable() -> bool:
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                key_path,
                0,
                winreg.KEY_SET_VALUE
            )
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
            winreg.CloseKey(key)

            ctypes.windll.wininet.InternetSetOptionW(0, 39, 0, 0)
            ctypes.windll.wininet.InternetSetOptionW(0, 37, 0, 0)

            logger.info("系统代理已关闭")
            return True
        except Exception as e:
            logger.error(f"关闭系统代理失败: {e}")
            return False
