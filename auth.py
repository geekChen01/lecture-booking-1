import ctypes
import json
import logging
import os
import time
import winreg
from datetime import datetime
from typing import Dict

import requests

from config import AUTO_REFRESH, PERSONNEL_LOGIN_URL, API_BASE, PROXY_ADDR, PROXY_PORT

logger = logging.getLogger(__name__)


class TokenManager:
    def __init__(self, account_name: str, auth_dir: str, session: requests.Session):
        self.account_name = account_name
        self.cache_file = os.path.join(auth_dir, f"auth_cache_{account_name}.json")
        self.session = session

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
                timeout=5
            )
            return resp.status_code == 200 and str(resp.json().get("code")) == "00000"
        except Exception:
            return False

    def refresh_token(self, id_token: str) -> bool:
        logger.info(f"[{self.account_name}] 用 id_token 刷新 chair-token...")
        try:
            resp = self.session.post(PERSONNEL_LOGIN_URL, data={"accessToken": id_token}, timeout=10)
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
        cache = self.load()

        if cache.get("chair_token") and self.validate_token(cache["chair_token"]):
            logger.debug(f"[{self.account_name}] 使用有效 chair-token")
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
