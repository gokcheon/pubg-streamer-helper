from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import itertools
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path

import cv2
import mss
import numpy as np
import requests

# fix_a26: 버전 단일 진실 + 자동 업데이트 (GitHub Releases)
import updater
from version import APP_FIX, APP_LABEL, APP_VERSION


WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
HOTKEY_ID = 1
VK_F12 = 0x7B
WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104

STEAM_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
STEAM_HOTKEY_MAP = {
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
    "printscreen": 0x2C,
    "prtsc": 0x2C,
    "prtscr": 0x2C,
}


@dataclass
class RoiResult:
    player_index: int
    kill: int
    damage: int


@dataclass
class AuthResult:
    ok: bool
    message: str


def get_runtime_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_resource_base_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return get_runtime_base_dir()


def check_environment() -> list[dict]:
    """실행 환경 사전 검사. 문제 항목 리스트 반환."""
    issues = []
    exe_path = str(Path(sys.executable).resolve() if getattr(sys, "frozen", False)
                   else Path(__file__).resolve())

    # 1. OneDrive 경로 감지
    onedrive_keywords = ["OneDrive", "onedrive"]
    if any(kw in exe_path for kw in onedrive_keywords):
        issues.append({
            "level": "error",
            "title": "OneDrive 경로 감지됨",
            "msg": (
                "현재 실행 경로가 OneDrive 폴더 안입니다.\n\n"
                "OneDrive가 파일을 클라우드에만 올려두면\n"
                "필요한 런타임 파일을 찾지 못해 오류가 발생합니다.\n\n"
                "권장 경로로 폴더를 옮긴 후 다시 실행하세요:\n"
                "  C:\\pubg_helper\n"
                "  D:\\pubg_helper\n\n"
                "또는 OneDrive 설정에서 해당 폴더를\n"
                "\"항상 이 장치에 유지\"로 변경하세요."
            ),
        })

    # 2. WebView2 런타임 확인 (레지스트리)
    if os.name == "nt":
        webview2_ok = False
        try:
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for subkey in (
                    r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
                    r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
                ):
                    try:
                        with winreg.OpenKey(hive, subkey):
                            webview2_ok = True
                            break
                    except OSError:
                        continue
                if webview2_ok:
                    break
        except Exception:
            pass
        if not webview2_ok:
            issues.append({
                "level": "error",
                "title": "WebView2 런타임 미설치",
                "msg": (
                    "Microsoft Edge WebView2 런타임이 설치되어 있지 않습니다.\n\n"
                    "아래 링크에서 설치 후 앱을 다시 실행하세요:\n"
                    "https://go.microsoft.com/fwlink/p/?LinkId=2124703\n\n"
                    "(Evergreen Bootstrapper 다운로드 후 실행)"
                ),
            })

    # 3. 로그 폴더 쓰기 권한 확인
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        test_file = LOGS_DIR / ".write_test"
        test_file.write_text("test", encoding="utf-8")
        test_file.unlink()
    except Exception:
        issues.append({
            "level": "warning",
            "title": "로그 폴더 쓰기 권한 없음",
            "msg": (
                "logs 폴더에 파일을 쓸 수 없습니다.\n\n"
                "앱은 실행되지만 로그가 저장되지 않습니다.\n"
                "Program Files 등 관리자 전용 폴더에 설치한 경우\n"
                "다른 경로(C:\\pubg_helper 등)로 옮겨서 실행하세요."
            ),
        })

    # 4. 한글/특수문자 경로 사전 검출 (fix_a15.2)
    # Python의 cv2/subprocess/PyInstaller 등이 ASCII 외 경로에서 드물게 이슈를 일으키므로,
    # 사용자에게 미리 안내하여 "왜 안 되는지" 모르는 상황 방지
    try:
        exe_path.encode("ascii")
    except UnicodeEncodeError:
        # 한글 또는 다른 비ASCII 문자가 경로에 있음
        issues.append({
            "level": "warning",
            "title": "한글/특수문자 경로 감지",
            "msg": (
                "현재 실행 경로에 한글이나 특수문자가 포함되어 있습니다.\n"
                f"  경로: {exe_path}\n\n"
                "대부분의 기능은 정상 동작하지만,\n"
                "일부 환경에서 문제가 발생할 수 있습니다.\n\n"
                "문제 발생 시 영문 경로로 옮기세요:\n"
                "  C:\\pubg_helper\n"
                "  D:\\pubg_helper"
            ),
        })

    return issues


def _make_env_warning_html(issues: list[dict]) -> str:
    """환경 경고 HTML 생성"""
    items_html = ""
    for issue in issues:
        color = "#ef4444" if issue["level"] == "error" else "#fbbf24"
        icon = "🚫" if issue["level"] == "error" else "⚠️"
        msg_escaped = issue["msg"].replace("\n", "<br>")
        items_html += f"""
        <div class='issue'>
          <div class='ititle'>{icon} {issue['title']}</div>
          <div class='imsg'>{msg_escaped}</div>
        </div>"""

    return f"""<!DOCTYPE html><html><head><meta charset='UTF-8'>
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:#141820;color:#e8f4ff;font-family:'Segoe UI',sans-serif;
     display:flex;flex-direction:column;height:100vh;user-select:none;overflow:hidden;}}
.tb{{height:40px;min-height:40px;display:flex;align-items:center;justify-content:space-between;
    padding:0 16px;-webkit-app-region:drag;background:#0d1117;}}
.tt{{font-size:13px;font-weight:700;color:#e8f4ff;}}
.wb{{width:26px;height:26px;border-radius:6px;border:none;background:transparent;
    color:#4a7a9b;font-size:12px;cursor:pointer;-webkit-app-region:no-drag;}}
.wb:hover{{background:#2a1515;color:#e05252;}}
.body{{flex:1;overflow-y:auto;padding:14px 16px;display:flex;flex-direction:column;gap:12px;}}
.headline{{font-size:13px;font-weight:700;color:#fbbf24;}}
.issue{{background:#1e2530;border-radius:8px;padding:12px 14px;border-left:3px solid #ef4444;}}
.ititle{{font-size:12px;font-weight:700;margin-bottom:6px;color:#f87171;}}
.imsg{{font-size:11px;color:#94a3b8;line-height:1.6;}}
.foot{{padding:10px 16px;background:#0d1117;display:flex;justify-content:flex-end;gap:8px;min-height:48px;}}
.btn{{padding:6px 18px;border-radius:6px;border:none;font-size:12px;cursor:pointer;font-family:inherit;}}
.btn-close{{background:#1e2530;color:#94a3b8;}}
.btn-close:hover{{background:#2a3441;}}
.btn-ok{{background:#1d4ed8;color:#fff;}}
.btn-ok:hover{{background:#2563eb;}}
</style></head><body>
<div class='tb'><div class='tt'>PUBG 스트리머 헬퍼 — 환경 경고</div>
<button class='wb' onclick="window.pywebview.api.close_window()">✕</button></div>
<div class='body'>
  <div class='headline'>실행 환경에서 문제가 감지되었습니다</div>
  {items_html}
</div>
<div class='foot'>
  <button class='btn btn-close' onclick="window.pywebview.api.close_window()">닫기</button>
  <button class='btn btn-ok' onclick="window.pywebview.api.dismiss_env_warning()">무시하고 계속</button>
</div>
</body></html>"""


RUNTIME_BASE_DIR = get_runtime_base_dir()
RESOURCE_BASE_DIR = get_resource_base_dir()
CONFIG_PATH = RUNTIME_BASE_DIR / "config.json"
OVERLAY_PATH = RUNTIME_BASE_DIR / "overlay.txt"
OUTBOX_PATH = RUNTIME_BASE_DIR / "chat_outbox.txt"
TOKENS_PATH = RUNTIME_BASE_DIR / "chat_tokens.json"
LOGS_DIR = RUNTIME_BASE_DIR / "logs"
# CAPTURES_DIR 제거: screenshot 폴더로 통일
SCREENSHOTS_DIR = RUNTIME_BASE_DIR / "screenshot"
DEBUG_DIR = RUNTIME_BASE_DIR / "debug"
BUILD_LOGS_DIR = RUNTIME_BASE_DIR / "build_logs"
FATAL_STARTUP_LOG = LOGS_DIR / "fatal_startup.log"
CONFIG_BACKUP_PATH = RUNTIME_BASE_DIR / "config.backup.json"

# fix_a25: 기록 영속 저장. config와 동일 정책 (앱 재시작 시 유지)
HISTORY_PATH = RUNTIME_BASE_DIR / "history.json"

def write_fatal_startup_log(exc: Exception) -> None:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with FATAL_STARTUP_LOG.open("a", encoding="utf-8") as fp:
            fp.write(f"[{timestamp}] {type(exc).__name__}: {exc}\n")
    except Exception:
        pass




RUNTIME_BASE_DIR = get_runtime_base_dir()
RESOURCE_BASE_DIR = get_resource_base_dir()
CONFIG_PATH = RUNTIME_BASE_DIR / "config.json"
OVERLAY_PATH = RUNTIME_BASE_DIR / "overlay.txt"
OUTBOX_PATH = RUNTIME_BASE_DIR / "chat_outbox.txt"
TOKENS_PATH = RUNTIME_BASE_DIR / "chat_tokens.json"
LOGS_DIR = RUNTIME_BASE_DIR / "logs"
# CAPTURES_DIR 제거: screenshot 폴더로 통일
SCREENSHOTS_DIR = RUNTIME_BASE_DIR / "screenshot"
DEBUG_DIR = RUNTIME_BASE_DIR / "debug"
BUILD_LOGS_DIR = RUNTIME_BASE_DIR / "build_logs"
FATAL_STARTUP_LOG = LOGS_DIR / "fatal_startup.log"
CONFIG_BACKUP_PATH = RUNTIME_BASE_DIR / "config.backup.json"

# fix_a25: 기록 영속 저장. config와 동일 정책 (앱 재시작 시 유지)
HISTORY_PATH = RUNTIME_BASE_DIR / "history.json"

def write_fatal_startup_log(exc: Exception) -> None:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with FATAL_STARTUP_LOG.open("a", encoding="utf-8") as fp:
            fp.write(f"[{timestamp}] {type(exc).__name__}: {exc}\n")
    except Exception:
        pass


class LevelRangeFilter(logging.Filter):
    def __init__(self, low: int, high: int) -> None:
        super().__init__()
        self.low = low
        self.high = high

    def filter(self, record: logging.LogRecord) -> bool:
        return self.low <= record.levelno <= self.high


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.wintypes.DWORD),
        ("scanCode", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class HotkeyManager:
    def __init__(self, callback, event_logger: logging.Logger, error_logger: logging.Logger, console_logger: logging.Logger,
                 send_callback=None) -> None:
        # fix_a19: callback = F8 (OCR), send_callback = F9 (송출)
        self.callback = callback
        self.send_callback = send_callback
        self.event_logger = event_logger
        self.error_logger = error_logger
        self.console_logger = console_logger
        self.is_running = False
        self.thread: threading.Thread | None = None
        self.thread_id: int = 0
        # fix_a15.3: 핫키 등록 실패 상태 노출 (UI에서 사용자에게 알리기 위함)
        self.registration_failed = False
        self.registration_error: str = ""

    def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        # 새 시작 시 이전 실패 상태 리셋
        self.registration_failed = False
        self.registration_error = ""
        self.thread = threading.Thread(target=self._run, name="HotkeyThread", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        import ctypes
        user32 = ctypes.windll.user32
        VK_F8 = 0x77
        VK_F9 = 0x78
        MOD_NOREPEAT = 0x4000
        HOTKEY_ID_F8 = 1
        HOTKEY_ID_F9 = 2  # fix_a19
        WM_HOTKEY = 0x0312
        WM_QUIT = 0x0012

        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        # F8 등록 (필수 - 실패 시 전체 실패)
        if not user32.RegisterHotKey(None, HOTKEY_ID_F8, MOD_NOREPEAT, VK_F8):
            err = ctypes.windll.kernel32.GetLastError()
            # fix_a15.3: 사용자가 알 수 있도록 상태 노출
            self.registration_failed = True
            self.registration_error = f"F8 핫키 등록 실패 (다른 프로그램이 F8을 점유 중일 수 있음, code={err})"
            self.event_logger.error(self.registration_error)
            self.error_logger.error(self.registration_error)
            self.is_running = False
            return

        self.event_logger.info("F8 전역 핫키 등록 완료")
        self.console_logger.info("F8 전역 핫키 등록 완료")

        # fix_a19: F9 등록 (선택 - 실패해도 F8은 살림)
        # F9 등록 실패는 송출 핫키만 비활성화될 뿐 F8 OCR은 정상 동작
        f9_registered = False
        if self.send_callback is not None:
            if user32.RegisterHotKey(None, HOTKEY_ID_F9, MOD_NOREPEAT, VK_F9):
                f9_registered = True
                self.event_logger.info("F9 전역 핫키 등록 완료 (송출)")
                self.console_logger.info("F9 전역 핫키 등록 완료 (송출)")
            else:
                err = ctypes.windll.kernel32.GetLastError()
                self.event_logger.warning(
                    "F9 핫키 등록 실패 (다른 프로그램 점유 추정, code=%d) - F8 OCR은 정상 동작",
                    err
                )

        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID_F8:
                # fix_a15.1: callback 예외가 메시지 루프를 죽이지 않도록 격리
                # 이게 없으면 callback 한 번 깨졌을 때 F8이 영구 무력화됨
                try:
                    self.callback()
                except Exception:
                    self.error_logger.exception("F8 핫키 콜백 처리 중 예외 발생 (메시지 루프는 계속)")
            elif msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID_F9:
                # fix_a19: F9 송출 콜백 (예외 격리)
                try:
                    if self.send_callback is not None:
                        self.send_callback()
                except Exception:
                    self.error_logger.exception("F9 핫키 콜백 처리 중 예외 발생 (메시지 루프는 계속)")
            elif msg.message == WM_QUIT:
                break

        user32.UnregisterHotKey(None, HOTKEY_ID_F8)
        if f9_registered:
            user32.UnregisterHotKey(None, HOTKEY_ID_F9)
        self.is_running = False

    def stop(self) -> None:
        if not self.is_running:
            return
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)  # WM_QUIT
        self.is_running = False
        self.event_logger.info("전역 핫키 감시 종료")
        self.console_logger.info("전역 핫키 감시 종료")


class HistoryManager:
    """fix_a25: OCR 기록 영속화 + 세션 락.
    - history.json에 디스크 저장. 앱 재시작해도 최근 20건 유지.
    - 최대 20개. 초과 시 가장 오래된 항목 폐기.
    - F8 OCR 시점에 add(), F9/auto_send 시점에 mark_sent_*(),
      카드 더블클릭 수정 시 update_latest().
    - 세션 락 (_session_dirty): add() 한 번이라도 호출돼야 mark_sent_*/update_latest 허용.
      → 재시작 직후 이전 세션 마지막 항목을 의도치 않게 수정·송출하는 것 차단.
    스레드 안전 (락 사용).
    """

    MAX_ITEMS = 20

    def __init__(self, error_logger: logging.Logger | None = None) -> None:
        self._lock = threading.Lock()
        self._error_logger = error_logger
        # fix_a25: 영속 저장된 기록 로드
        loaded = load_history()
        self._items: list[dict] = loaded[: self.MAX_ITEMS]  # 최신이 [0]
        # next_id는 기존 항목 max(id)+1로 복원
        if self._items:
            try:
                self._next_id = max(int(it.get("id", 0)) for it in self._items) + 1
            except Exception:
                self._next_id = 1
        else:
            self._next_id = 1
        # fix_a25: 세션 락 - 이번 세션에서 add()가 한 번이라도 호출됐는지.
        # False인 동안은 mark_sent_*/update_latest 모두 차단 (호출부에서 check_session_dirty로 검증).
        self._session_dirty = False

    def is_session_dirty(self) -> bool:
        """이번 세션에서 add()가 호출됐는가. False면 카드 편집/수동 송출 차단해야 함."""
        with self._lock:
            return self._session_dirty

    def _save_silent(self) -> None:
        """저장 실패해도 raise 안 함 (errors.log에만 기록)."""
        try:
            save_history(self._items)
        except Exception:
            if self._error_logger is not None:
                try:
                    self._error_logger.exception("history.json 저장 실패")
                except Exception:
                    pass

    def add(self, mode: str, players: list[dict], chicken_bonus: int,
            kill_bonus: int, multiplier: int, total_damage: int,
            total_kill: int, final: int, final_kill: int, is_chicken: bool,
            read_target: str, message: str = "") -> int:
        """F8 OCR 결과를 새 항목으로 추가. 항목 ID 반환.

        players: [{"kill": int, "damage": int}, ...] 모드별 1~4명
        message: 송출용으로 조립된 채팅 메시지 (재전송 시 사용)
        final: 배수/치킨 적용된 최종 딜량 (final_damage)
        final_kill: 배수/치킨 적용된 최종 킬수
        """
        with self._lock:
            item_id = self._next_id
            self._next_id += 1
            now = time.time()
            entry = {
                "id": item_id,
                "timestamp": now,
                "time_str": datetime.fromtimestamp(now).strftime("%H:%M"),
                "mode": mode,
                "players": list(players),  # shallow copy
                "chicken_bonus": int(chicken_bonus),
                "kill_bonus": int(kill_bonus),
                "multiplier": int(multiplier),
                "total_damage": int(total_damage),
                "total_kill": int(total_kill),
                "final": int(final),
                "final_kill": int(final_kill),
                "is_chicken": bool(is_chicken),
                "read_target": read_target,
                "message": message,
                "sent": False,  # F9 또는 auto_send 시 True로
            }
            self._items.insert(0, entry)
            # 최대 개수 초과 시 가장 오래된 것 제거
            if len(self._items) > self.MAX_ITEMS:
                self._items = self._items[: self.MAX_ITEMS]
            # fix_a25: 세션 락 해제 + 영속 저장
            self._session_dirty = True
            self._save_silent()
            return item_id

    def mark_sent_latest(self) -> bool:
        """가장 최근 항목을 송출됨으로 표시. 항목이 없으면 False.
        fix_a25: 세션 락 가드는 호출부 (manual_send/auto_send) 책임.
        여기서는 락 검증 없이 단순 처리 (재전송 시에도 mark_sent_by_id가 자유로워야 하므로).
        """
        with self._lock:
            if not self._items:
                return False
            self._items[0]["sent"] = True
            self._save_silent()
            return True

    def mark_sent_by_id(self, item_id: int) -> bool:
        """특정 항목을 송출됨으로 표시 (재전송 후 호출)."""
        with self._lock:
            for item in self._items:
                if item["id"] == item_id:
                    item["sent"] = True
                    self._save_silent()
                    return True
            return False

    def update_latest(self, players: list[dict], chicken_bonus: int,
                      kill_bonus: int, multiplier: int,
                      total_damage: int, total_kill: int, final: int,
                      final_kill: int, message: str = "") -> bool:
        """카드 더블클릭 수정 → 가장 최근 항목 값 갱신.
        곡천님 정책: 수정된 항목에 별도 마크 안 함, 그냥 값만 갱신.
        fix_a25: 세션 락 가드는 호출부 (update_last_result) 책임.
        """
        with self._lock:
            if not self._items:
                return False
            item = self._items[0]
            item["players"] = list(players)
            item["chicken_bonus"] = int(chicken_bonus)
            item["kill_bonus"] = int(kill_bonus)
            item["multiplier"] = int(multiplier)
            item["total_damage"] = int(total_damage)
            item["total_kill"] = int(total_kill)
            item["final"] = int(final)
            item["final_kill"] = int(final_kill)
            if message:
                item["message"] = message
            self._save_silent()
            return True

    def get_by_id(self, item_id: int) -> dict | None:
        """재전송용. 특정 항목 깊은 복사 반환."""
        with self._lock:
            for item in self._items:
                if item["id"] == item_id:
                    return dict(item)  # shallow copy 충분 (외부에서 수정 안 함)
            return None

    def list_all(self) -> list[dict]:
        """UI용. 전체 항목 복사 반환 (최신 → 오래된 순)."""
        with self._lock:
            return [dict(item) for item in self._items]


class ChzzkChatClient:
    def __init__(self, config_provider, event_logger: logging.Logger, error_logger: logging.Logger, console_logger: logging.Logger) -> None:
        self.config_provider = config_provider
        self.event_logger = event_logger
        self.error_logger = error_logger
        self.console_logger = console_logger
        self.session = requests.Session()
        self._tokens: dict = {}
        self._lock = threading.Lock()
        # fix_a9: 토큰 갱신 직렬화용 별도 락 (네트워크 대기 동안 _lock 점유 방지)
        self._refresh_lock = threading.Lock()
        self.load_tokens()
        # fix_a19: 백그라운드 사전 갱신 - 사용자가 토큰 신경 쓰지 않도록
        # 만료 5분 전부터 미리 갱신 시도. 송출 시점에 갱신 대기로 인한 지연 방지.
        self._refresh_failed_count = 0  # 연속 갱신 실패 카운터 (백오프용)
        self._bg_refresh_stop = threading.Event()
        self._bg_refresh_thread: threading.Thread | None = None
        self._start_background_refresh()

    def _start_background_refresh(self) -> None:
        """fix_a19: 토큰 만료 5분 전 자동 갱신 워커 시작.
        - 60초마다 토큰 상태 점검
        - 만료까지 5분 미만이면 갱신 시도
        - 실패하면 지수 백오프 (60s → 120s → 240s, 최대 10분)
        - has_valid_access_token이 false면(토큰 없음) 그냥 대기
        """
        def _worker() -> None:
            base_interval = 60  # 평상시 점검 주기 (초)
            while not self._bg_refresh_stop.wait(base_interval):
                try:
                    # 토큰 자체가 없으면 (사용자 미연결) 그냥 대기
                    with self._lock:
                        access_token = self._tokens.get("accessToken")
                        expires_at = float(self._tokens.get("expires_at", 0))
                        has_refresh = bool(self._tokens.get("refreshToken"))
                    if not access_token or not has_refresh:
                        continue
                    # 만료까지 남은 시간 (초)
                    remain = expires_at - time.time()
                    # 5분(300초) 이상 남았으면 아직 갱신 불필요
                    if remain > 300:
                        # 갱신 실패 카운터 리셋 (정상 상태)
                        self._refresh_failed_count = 0
                        continue
                    # 5분 미만 → 백그라운드 갱신 시도
                    # 단, 연속 실패 시 지수 백오프 (망가진 인터넷에서 1분마다 시도하는 거 방지)
                    if self._refresh_failed_count > 0:
                        # 60s, 120s, 240s, 480s, 600s 캡
                        backoff = min(60 * (2 ** self._refresh_failed_count), 600)
                        if remain > -backoff:  # 만료된 지 backoff 시간 안 지났으면 대기
                            continue
                    try:
                        with self._refresh_lock:
                            # double-check: 락 대기 중 다른 곳에서 이미 갱신했을 수도
                            with self._lock:
                                if float(self._tokens.get("expires_at", 0)) - time.time() > 300:
                                    self._refresh_failed_count = 0
                                    continue
                            self.refresh_access_token()
                            self._refresh_failed_count = 0
                            self.event_logger.info("[bg-refresh] 토큰 사전 갱신 성공")
                    except Exception as exc:
                        self._refresh_failed_count += 1
                        self.event_logger.warning(
                            "[bg-refresh] 토큰 사전 갱신 실패 (%d회): %s",
                            self._refresh_failed_count, exc
                        )
                        # 연속 5회 실패 = refreshToken 자체가 만료됐을 가능성 높음
                        # 더 이상 자동 갱신 시도 안 하고 사용자 재연결 기다림
                        if self._refresh_failed_count >= 5:
                            self.event_logger.error(
                                "[bg-refresh] 5회 연속 실패 - 자동 갱신 중단. 사용자 재연결 필요"
                            )
                            self._refresh_failed_count = 0  # 다음 토큰 발급 후 재시작 가능
                            # 한참 대기 (다음 사용자 연결까지)
                            self._bg_refresh_stop.wait(600)
                except Exception:
                    # 워커 자체가 죽지 않도록 모든 예외 흡수
                    self.error_logger.exception("[bg-refresh] 워커 오류")

        self._bg_refresh_thread = threading.Thread(
            target=_worker, name="ChzzkTokenRefresh", daemon=True
        )
        self._bg_refresh_thread.start()
        self.event_logger.info("[bg-refresh] 백그라운드 토큰 갱신 워커 시작")

    def stop_background_refresh(self) -> None:
        """fix_a19: 앱 종료 시 워커 정리"""
        try:
            self._bg_refresh_stop.set()
        except Exception:
            pass

    def get_chat_config(self) -> dict:
        return self.config_provider().get("chat", {})

    def get_token_path(self) -> Path:
        chat_cfg = self.get_chat_config()
        token_file = chat_cfg.get("token_file", "chat_tokens.json")
        token_path = Path(token_file)
        if token_path.is_absolute():
            return token_path
        return RUNTIME_BASE_DIR / token_path

    def load_tokens(self) -> None:
        token_path = self.get_token_path()
        if token_path.exists():
            try:
                with token_path.open("r", encoding="utf-8") as fp:
                    self._tokens = json.load(fp)
            except Exception:
                self._tokens = {}
                self.error_logger.exception("토큰 파일 로드 실패: %s", token_path)
                self.console_logger.exception("토큰 파일 로드 실패: %s", token_path)
        else:
            self._tokens = {}

    def save_tokens(self, tokens: dict) -> None:
        token_path = self.get_token_path()
        token_path.parent.mkdir(parents=True, exist_ok=True)
        with token_path.open("w", encoding="utf-8") as fp:
            json.dump(tokens, fp, ensure_ascii=False, indent=2)
        self._tokens = tokens
        self.event_logger.info("치지직 토큰 저장 완료: %s", token_path)
        self.console_logger.info("치지직 토큰 저장 완료: %s", token_path)

    def clear_tokens(self) -> None:
        token_path = self.get_token_path()
        if token_path.exists():
            token_path.unlink(missing_ok=True)
        self._tokens = {}
        self.event_logger.info("치지직 토큰 삭제")
        self.console_logger.info("치지직 토큰 삭제")

    def get_status_text(self) -> str:
        chat_cfg = self.get_chat_config()
        if not chat_cfg.get("client_id") or not chat_cfg.get("client_secret"):
            return "치지직 API 설정 필요"
        if self.has_valid_access_token():
            expires_at = self._tokens.get("expires_at", 0)
            remain_sec = max(0, int(expires_at - time.time()))
            remain_min = remain_sec // 60
            return f"연결됨 (토큰 {remain_min}분 남음)"
        if self._tokens.get("refreshToken"):
            return "토큰 있음 (갱신 필요)"
        return "미연결"

    def has_valid_access_token(self) -> bool:
        access_token = self._tokens.get("accessToken")
        expires_at = float(self._tokens.get("expires_at", 0))
        return bool(access_token and expires_at > time.time() + 60)

    def ensure_access_token(self) -> str:
        # fix_a9: 락 분리로 네트워크 대기 시간 동안 토큰 dict 읽기 차단되지 않도록 개선
        # 빠른 경로: 토큰이 유효하면 즉시 반환
        with self._lock:
            if self.has_valid_access_token():
                return str(self._tokens["accessToken"])
            has_refresh = bool(self._tokens.get("refreshToken"))

        if not has_refresh:
            raise RuntimeError("치지직 Access Token이 없습니다. 먼저 연결을 완료하세요.")

        # 갱신 경로: 네트워크 호출은 _refresh_lock 안에서 직렬화
        # _lock은 점유하지 않아 다른 스레드의 has_valid_access_token() 등 빠른 동작은 차단되지 않음
        with self._refresh_lock:
            # double-check: 락 대기 중 다른 스레드가 이미 갱신했을 수 있음
            with self._lock:
                if self.has_valid_access_token():
                    return str(self._tokens["accessToken"])
            # 실제 갱신 (네트워크 호출, 최대 20초)
            self.refresh_access_token()

        with self._lock:
            if self.has_valid_access_token():
                return str(self._tokens["accessToken"])
            raise RuntimeError("치지직 Access Token이 없습니다. 먼저 연결을 완료하세요.")

    def exchange_code_for_tokens(self, code: str, state: str) -> None:
        chat_cfg = self.get_chat_config()
        payload = {
            "grantType": "authorization_code",
            "clientId": chat_cfg.get("client_id", "").strip(),
            "clientSecret": chat_cfg.get("client_secret", "").strip(),
            "code": code,
            "state": state,
        }
        api_base = chat_cfg.get("api_base_url", "https://openapi.chzzk.naver.com").rstrip("/")
        response = self.session.post(f"{api_base}/auth/v1/token", json=payload, timeout=20)
        response.raise_for_status()
        data = response.json()
        content = data.get("content") or data
        if not content.get("accessToken"):
            raise RuntimeError(f"토큰 발급 응답이 비정상입니다: {data}")
        content["issued_at"] = int(time.time())
        content["expires_at"] = int(time.time()) + int(content.get("expiresIn", 0))
        self.save_tokens(content)

    def refresh_access_token(self) -> None:
        chat_cfg = self.get_chat_config()
        refresh_token = self._tokens.get("refreshToken")
        if not refresh_token:
            raise RuntimeError("Refresh Token이 없습니다. 다시 연결하세요.")
        payload = {
            "grantType": "refresh_token",
            "refreshToken": refresh_token,
            "clientId": chat_cfg.get("client_id", "").strip(),
            "clientSecret": chat_cfg.get("client_secret", "").strip(),
        }
        api_base = chat_cfg.get("api_base_url", "https://openapi.chzzk.naver.com").rstrip("/")
        response = self.session.post(f"{api_base}/auth/v1/token", json=payload, timeout=20)
        response.raise_for_status()
        data = response.json()
        content = data.get("content") or data
        if not content.get("accessToken"):
            raise RuntimeError(f"토큰 갱신 응답이 비정상입니다: {data}")
        content["issued_at"] = int(time.time())
        content["expires_at"] = int(time.time()) + int(content.get("expiresIn", 0))
        self.save_tokens(content)
        self.event_logger.info("치지직 Access Token 갱신 완료")
        self.console_logger.info("치지직 Access Token 갱신 완료")

    def send_message(self, message: str) -> str:
        message = (message or "").strip()
        if not message:
            raise RuntimeError("보낼 메시지가 비어 있습니다.")
        if len(message) > 100:
            message = message[:100]

        # fix_a19: 자동 복구 - 401 시 토큰 강제 갱신 후 1회 재시도, 네트워크 에러는 backoff 재시도
        return self._send_message_with_retry(message)

    def _send_message_with_retry(self, message: str) -> str:
        """fix_a19: 송출 자동 복구 래퍼.
        - 401: ensure_access_token 마진(60초) 너머의 토큰 만료 케이스. 강제 갱신 후 1회 재시도.
        - 네트워크/5xx: 일시 장애. 짧은 backoff로 최대 2회 재시도 (총 3회 시도).
        - 4xx (401 외): 클라이언트 오류. 즉시 raise (재시도 무의미).
        """
        chat_cfg = self.get_chat_config()
        api_base = chat_cfg.get("api_base_url", "https://openapi.chzzk.naver.com").rstrip("/")
        url = f"{api_base}/open/v1/chats/send"

        max_retries_network = 2  # 네트워크 에러 시 추가 재시도 횟수 (총 3회)
        retried_after_401 = False  # 401 재시도는 1회만

        for attempt in range(max_retries_network + 1):
            try:
                access_token = self.ensure_access_token()
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                }
                response = self.session.post(
                    url,
                    headers=headers,
                    json={"message": message},
                    timeout=20,
                )

                # 401: 토큰이 막 만료된 경우. 강제 갱신 후 1회만 재시도.
                if response.status_code == 401 and not retried_after_401:
                    self.event_logger.warning(
                        "치지직 채팅 전송 401 - 토큰 만료 추정, 강제 갱신 후 재시도"
                    )
                    retried_after_401 = True
                    try:
                        with self._refresh_lock:
                            self.refresh_access_token()
                    except Exception as exc:
                        # 갱신 자체가 실패하면 사용자에게 명확한 안내
                        self._on_refresh_failure(exc)
                        raise RuntimeError(
                            "치지직 토큰이 만료되었습니다. 설정에서 '치지직 연결'을 다시 진행해주세요."
                        )
                    continue  # 재시도

                # 그 외 4xx: 재시도 무의미, 즉시 에러
                if 400 <= response.status_code < 500:
                    response.raise_for_status()

                # 5xx: 서버 일시 장애 - 재시도
                if response.status_code >= 500:
                    if attempt < max_retries_network:
                        wait = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s
                        self.event_logger.warning(
                            "치지직 채팅 전송 %d - %.1f초 후 재시도 (%d/%d)",
                            response.status_code, wait, attempt + 1, max_retries_network + 1
                        )
                        time.sleep(wait)
                        continue
                    response.raise_for_status()

                # 2xx: 성공
                response.raise_for_status()
                data = response.json()
                content = data.get("content") or {}
                message_id = content.get("messageId", "")
                self.event_logger.info(
                    "치지직 채팅 전송 성공: messageId=%s message=%s%s",
                    message_id, message,
                    " (재시도 후 성공)" if (attempt > 0 or retried_after_401) else ""
                )
                self.console_logger.info("치지직 채팅 전송 성공: messageId=%s message=%s", message_id, message)
                return message_id

            except (requests.ConnectionError, requests.Timeout) as exc:
                # 네트워크 일시 장애 - 재시도
                if attempt < max_retries_network:
                    wait = 0.5 * (2 ** attempt)
                    self.event_logger.warning(
                        "치지직 채팅 전송 네트워크 오류 (%s) - %.1f초 후 재시도 (%d/%d)",
                        type(exc).__name__, wait, attempt + 1, max_retries_network + 1
                    )
                    time.sleep(wait)
                    continue
                # 마지막 시도도 실패
                self.event_logger.error("치지직 채팅 전송 최종 실패 (네트워크): %s", exc)
                raise RuntimeError(
                    f"네트워크 오류로 채팅 전송 실패: {type(exc).__name__}. 인터넷 연결 확인 후 다시 시도해주세요."
                )

        # 여기 도달은 이론적으로 없어야 함 (continue로 빠지거나 raise/return으로 끝남)
        raise RuntimeError("치지직 채팅 전송 실패 (예기치 않은 종료)")

    def _on_refresh_failure(self, exc: Exception) -> None:
        """fix_a19: refresh 실패 시 처리. UI에서 상태 알림 가능하도록 플래그 설정."""
        self.event_logger.error("치지직 토큰 갱신 실패: %s", exc)
        self.console_logger.error("치지직 토큰 갱신 실패: %s", exc)
        # 토큰 무효화 - 다음 has_valid_access_token이 false 반환하도록
        # (단, 실제 토큰은 보존하지 않음. 갱신 실패 = 사용자 재연결 필요)

    def start_interactive_login(self) -> AuthResult:
        chat_cfg = self.get_chat_config()
        client_id = chat_cfg.get("client_id", "").strip()
        client_secret = chat_cfg.get("client_secret", "").strip()
        redirect_uri = chat_cfg.get("redirect_uri", "").strip()
        auth_url = chat_cfg.get("auth_url", "https://chzzk.naver.com/account-interlock").strip()

        if not client_id or not client_secret or not redirect_uri:
            return AuthResult(False, "client_id / client_secret / redirect_uri를 먼저 입력하고 저장하세요.")

        parsed = urllib.parse.urlparse(redirect_uri)
        if parsed.scheme != "http" or not parsed.hostname or not parsed.port:
            return AuthResult(False, "redirect_uri 형식이 잘못되었습니다. 예: http://127.0.0.1:8785/chzzk/callback")

        state = f"state_{int(time.time())}"
        event = threading.Event()
        result_box: dict[str, str] = {}
        expected_path = parsed.path or "/"

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                incoming = urllib.parse.urlparse(self.path)
                if incoming.path != expected_path:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"Not Found")
                    return

                params = urllib.parse.parse_qs(incoming.query)
                result_box["code"] = params.get("code", [""])[0]
                result_box["state"] = params.get("state", [""])[0]
                result_box["error"] = params.get("error", [""])[0]

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write("<html><body><h2>치지직 연결 완료</h2><p>이 창을 닫고 앱으로 돌아가세요.</p></body></html>".encode("utf-8"))
                event.set()

            def log_message(self, format: str, *args) -> None:
                return

        # fix_a15.1: 포트 충돌 시 명시적인 안내 메시지 제공
        try:
            server = HTTPServer((parsed.hostname, parsed.port), CallbackHandler)
        except OSError as exc:
            self.error_logger.exception("OAuth 콜백 서버 시작 실패 (포트 점유 가능성)")
            return AuthResult(False, f"포트 {parsed.port} 사용 불가 (다른 프로그램이 점유 중일 수 있음). 다른 프로그램 종료 후 재시도하세요.")
        server.timeout = 1

        def server_worker() -> None:
            start_time = time.time()
            try:
                while not event.is_set() and (time.time() - start_time) < 180:
                    server.handle_request()
            finally:
                server.server_close()

        thread = threading.Thread(target=server_worker, name="ChzzkOAuthCallback", daemon=True)
        thread.start()

        params = {
            "clientId": client_id,
            "redirectUri": redirect_uri,
            "state": state,
        }
        login_url = f"{auth_url}?{urllib.parse.urlencode(params)}"
        self.event_logger.info("치지직 인증 페이지 열기: %s", redirect_uri)
        self.console_logger.info("치지직 인증 페이지 열기: %s", redirect_uri)
        webbrowser.open(login_url)

        event.wait(timeout=180)
        if not event.is_set():
            return AuthResult(False, "인증 대기 시간 초과입니다. 브라우저 로그인 후 다시 시도하세요.")
        if result_box.get("error"):
            return AuthResult(False, f"치지직 인증 오류: {result_box['error']}")
        if result_box.get("state") != state:
            return AuthResult(False, "state 검증에 실패했습니다.")
        if not result_box.get("code"):
            return AuthResult(False, "인증 코드가 도착하지 않았습니다.")

        try:
            self.exchange_code_for_tokens(result_box["code"], state)
            return AuthResult(True, "치지직 연결 완료")
        except Exception as exc:
            self.error_logger.exception("치지직 토큰 발급 실패")
            self.console_logger.exception("치지직 토큰 발급 실패")
            return AuthResult(False, f"토큰 발급 실패: {exc}")


# ---------- File/bootstrap helpers ----------

def ensure_dirs() -> None:
    for folder in [LOGS_DIR, SCREENSHOTS_DIR, DEBUG_DIR, BUILD_LOGS_DIR]:
        folder.mkdir(parents=True, exist_ok=True)


def bootstrap_runtime_files() -> None:
    ensure_dirs()
    for filename in ["config.json", "overlay.txt", "chat_outbox.txt", "digit_templates_strip.png"]:
        runtime_file = RUNTIME_BASE_DIR / filename
        resource_file = RESOURCE_BASE_DIR / filename
        if not runtime_file.exists() and resource_file.exists():
            shutil.copy2(resource_file, runtime_file)
    # fix_a26: 깃허브 공개 저장소에는 config.json(개인 인증정보가 들어갈 수 있음) 대신
    # config.default.json(빈 템플릿)만 올라간다. config.json 이 아직 없으면 템플릿에서 만든다.
    # 빈 텍스트 파일(overlay/outbox)도 없으면 새로 만든다 (저장소에서 제외되므로).
    cfg_runtime = RUNTIME_BASE_DIR / "config.json"
    if not cfg_runtime.exists():
        for tpl in (RUNTIME_BASE_DIR / "config.default.json", RESOURCE_BASE_DIR / "config.default.json"):
            if tpl.exists():
                shutil.copy2(tpl, cfg_runtime)
                break
    for filename in ["overlay.txt", "chat_outbox.txt"]:
        runtime_file = RUNTIME_BASE_DIR / filename
        if not runtime_file.exists():
            try:
                runtime_file.write_text("", encoding="utf-8")
            except Exception:
                pass


def _read_json_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def load_config() -> dict:
    last_error: Exception | None = None
    for candidate in [CONFIG_PATH, CONFIG_BACKUP_PATH, RESOURCE_BASE_DIR / "config.json",
                      RUNTIME_BASE_DIR / "config.default.json", RESOURCE_BASE_DIR / "config.default.json"]:
        if not candidate.exists():
            continue
        try:
            data = _read_json_file(candidate)
            if candidate != CONFIG_PATH:
                ensure_dirs()
                tmp_path = CONFIG_PATH.with_suffix(".json.tmp")
                with tmp_path.open("w", encoding="utf-8") as fp:
                    json.dump(data, fp, ensure_ascii=False, indent=2)
                    fp.flush()
                    os.fsync(fp.fileno())
                os.replace(tmp_path, CONFIG_PATH)
            # fix_a23: 보너스 키 마이그레이션 - 딜/킬 대칭 보장.
            # a22 이전 빌드를 쓰던 사용자의 config.json에는 kill_bonus_* 키가
            # 누락되어 있어서 체크 ON 해도 kill_bonus_count=0 → 보너스 미적용 버그.
            # 누락분만 채우고, 이미 값이 있으면 사용자 의사 존중해서 그대로 둠.
            data = _ensure_bonus_keys(data)
            return data
        except Exception as exc:
            last_error = exc
            write_fatal_startup_log(exc)
    if last_error is not None:
        raise last_error
    raise FileNotFoundError(f"config.json을 찾을 수 없습니다: {CONFIG_PATH}")


def _ensure_bonus_keys(data: dict) -> dict:
    """fix_a23: 딜/킬 보너스 키가 비대칭이던 문제 마이그레이션.

    딜 측은 처음부터 config.json에 키가 있었지만 킬 측은 없었음.
    누락된 키만 기본값으로 채워넣고 디스크에 한 번 저장. 이미 값이 있는
    키는 건드리지 않음 (사용자가 0으로 설정한 의도 존중).

    딜과 완전 대칭 정책:
      chicken_bonus_enabled=True,  chicken_bonus_damage=1000
      kill_bonus_enabled=True,     kill_bonus_count=10
    """
    defaults = {
        "chicken_bonus_enabled": True,
        "chicken_bonus_damage": 1000,
        "kill_bonus_enabled": True,
        "kill_bonus_count": 10,
    }
    changed = False
    for k, v in defaults.items():
        if k not in data:
            data[k] = v
            changed = True
    if changed:
        try:
            save_config(data)
        except Exception:
            pass  # 저장 실패해도 메모리 dict는 채워졌으니 이번 세션은 정상 동작
    return data


def save_config(config: dict) -> None:
    ensure_dirs()
    temp_path = CONFIG_PATH.with_suffix(".json.tmp")
    if CONFIG_PATH.exists():
        try:
            shutil.copy2(CONFIG_PATH, CONFIG_BACKUP_PATH)
        except Exception:
            pass
    with temp_path.open("w", encoding="utf-8") as fp:
        json.dump(config, fp, ensure_ascii=False, indent=2)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(temp_path, CONFIG_PATH)
    try:
        shutil.copy2(CONFIG_PATH, CONFIG_BACKUP_PATH)
    except Exception:
        pass


# fix_a25: 기록 영속화 (history.json 저장/로드)
# 곡천님 정책: 최근 20건 유지, 송출 여부 무관하게 모든 항목 보존.
# 재시작 시 이전 세션 마지막 항목을 의도치 않게 수정/송출하는 것은 호출부(WebApi)에서 가드.
def save_history(items: list) -> None:
    """기록을 history.json에 저장 (atomic write).
    실패해도 앱 동작은 막지 않음 - 호출부에서 silent하게 호출."""
    try:
        ensure_dirs()
        temp_path = HISTORY_PATH.with_suffix(".json.tmp")
        with temp_path.open("w", encoding="utf-8") as fp:
            # version 키로 향후 마이그레이션 여지. next_id는 로드 시 max+1로 복원.
            json.dump(
                {"version": 1, "items": list(items)},
                fp, ensure_ascii=False, indent=2,
            )
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(temp_path, HISTORY_PATH)
    except Exception:
        # 호출부에서 errors.log로 잡음. 여기선 raise해서 알림.
        raise


def load_history() -> list:
    """history.json 로드. 파일 없거나 깨졌으면 빈 리스트 (앱 시작 막지 않음).
    필수 키 (id, players) 누락된 항목은 자동 제외.
    """
    if not HISTORY_PATH.exists():
        return []
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict):
            return []
        items = data.get("items", [])
        if not isinstance(items, list):
            return []
        valid_items = []
        for it in items:
            if not isinstance(it, dict):
                continue
            if "id" not in it or "players" not in it:
                continue
            valid_items.append(it)
        return valid_items
    except Exception:
        # 깨진 파일은 무시하고 빈 리스트 (앱 시작 정상 진행)
        return []


def resolve_path(relative_or_absolute: str) -> Path:
    path = Path(relative_or_absolute)
    if path.is_absolute():
        return path
    return RUNTIME_BASE_DIR / path


def setup_logging(config: dict) -> tuple[logging.Logger, logging.Logger, logging.Logger]:
    logging_cfg = config.get("logging", {})
    event_log_path = resolve_path(logging_cfg.get("event_log", "logs/events.log"))
    error_log_path = resolve_path(logging_cfg.get("error_log", "logs/errors.log"))
    max_bytes = int(logging_cfg.get("max_bytes", 1_048_576))
    backup_count = int(logging_cfg.get("backup_count", 5))

    event_logger = logging.getLogger("pubg_helper.events")
    error_logger = logging.getLogger("pubg_helper.errors")
    console_logger = logging.getLogger("pubg_helper.console")

    for logger in [event_logger, error_logger, console_logger]:
        logger.handlers.clear()
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    event_file = RotatingFileHandler(event_log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    event_file.setFormatter(formatter)
    event_file.addFilter(LevelRangeFilter(logging.DEBUG, logging.INFO))
    event_logger.addHandler(event_file)

    error_file = RotatingFileHandler(error_log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    error_file.setFormatter(formatter)
    error_file.setLevel(logging.WARNING)
    error_logger.addHandler(error_file)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.setLevel(logging.DEBUG)
    console_logger.addHandler(console)

    return event_logger, error_logger, console_logger


# ---------- OCR helpers ----------
def clamp_roi(x1: int, y1: int, x2: int, y2: int, width: int, height: int) -> tuple[int, int, int, int]:
    x1 = max(0, min(x1, width - 1))
    y1 = max(0, min(y1, height - 1))
    x2 = max(1, min(x2, width))
    y2 = max(1, min(y2, height))
    return x1, y1, x2, y2


def relative_roi_to_absolute(roi: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = roi
    return (
        int(x1 * width),
        int(y1 * height),
        int(x2 * width),
        int(y2 * height),
    )


def save_image_unicode_safe(path: Path, image: np.ndarray) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower() or ".png"
    ok, encoded = cv2.imencode(suffix, image)
    if not ok:
        raise RuntimeError(f"이미지 인코딩 실패: {path}")
    encoded.tofile(str(path))


def load_image_unicode_safe(path: Path, flags: int = cv2.IMREAD_GRAYSCALE) -> np.ndarray | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, flags)
    except Exception:
        return None




class DigitTemplateMatcher:
    def __init__(self, config_provider, event_logger: logging.Logger, error_logger: logging.Logger, console_logger: logging.Logger) -> None:
        self.config_provider = config_provider
        self.event_logger = event_logger
        self.error_logger = error_logger
        self.console_logger = console_logger
        self.templates: dict[str, np.ndarray] = {}
        self.last_strip_path: str | None = None
        self.disabled_until_restart = False
        self.last_reject_reason: str | None = None
        # fix_a8: 모드별 템플릿 캐시 (solo는 다른 폰트로 학습된 strip 사용)
        self._templates_cache_by_path: dict[str, dict[str, np.ndarray]] = {}
        self._current_loaded_mode: str | None = None

    def get_cfg(self) -> dict:
        return self.config_provider().get("recognizer", {})

    def get_template_path(self, mode: str | None = None) -> Path:
        cfg = self.get_cfg()
        # fix_a8: solo 모드는 별도 strip 사용 (다른 폰트 크기 대응)
        # fix_a12: duo 모드도 별도 strip 사용 (베이지/흰색 글자, 9px 폭)
        # fix_a13: squad3는 임시로 duo strip 재사용 (글자 크기 비슷, 정확도 미보장)
        if mode == "solo":
            name = cfg.get("template_strip_solo", "digit_templates_strip_solo.png")
        elif mode in ("duo", "squad3"):
            name = cfg.get("template_strip_duo", "digit_templates_strip_duo.png")
        else:
            name = cfg.get("template_strip", "digit_templates_strip.png")
        candidate = Path(name)
        if candidate.is_absolute():
            return candidate
        candidates = [
            RUNTIME_BASE_DIR / name,
            RESOURCE_BASE_DIR / name,
            RUNTIME_BASE_DIR / "_internal" / name,
            RESOURCE_BASE_DIR / "_internal" / name,
        ]
        for path in candidates:
            if path.exists():
                return path
        return candidates[0]

    def _target_width_for_digit(self, digit: str) -> int:
        return 9 if digit == "1" else 11

    def _normalize_template_digit(self, digit_img: np.ndarray, digit: str) -> np.ndarray:
        target_w = self._target_width_for_digit(digit)
        target_h = int(self.get_cfg().get("digit_cell_height", 20))
        ys, xs = np.where(digit_img > 0)
        if len(xs) == 0 or len(ys) == 0:
            return np.zeros((target_h, target_w), dtype=np.uint8)
        digit_img = digit_img[ys.min():ys.max()+1, xs.min():xs.max()+1]
        resized = cv2.resize(digit_img, (target_w, target_h), interpolation=cv2.INTER_NEAREST)
        return (resized > 0).astype(np.uint8)

    def ensure_loaded(self, mode: str | None = None) -> None:
        if self.disabled_until_restart:
            raise RuntimeError("템플릿 엔진이 비활성화되었습니다. 앱을 다시 시작하세요.")
        path = self.get_template_path(mode)
        resolved = str(path.resolve()) if path.exists() else str(path)
        # fix_a8: 동일한 strip 경로가 이미 self.templates에 로드되어 있으면 재사용
        if self.templates and self.last_strip_path == resolved:
            return
        # 캐시에서 찾기 (이전에 로드한 적 있는 strip)
        if resolved in self._templates_cache_by_path:
            self.templates = self._templates_cache_by_path[resolved]
            self.last_strip_path = resolved
            self._current_loaded_mode = mode
            return
        if not path.exists():
            searched = [
                RUNTIME_BASE_DIR / path.name,
                RESOURCE_BASE_DIR / path.name,
                RUNTIME_BASE_DIR / "_internal" / path.name,
                RESOURCE_BASE_DIR / "_internal" / path.name,
            ]
            raise FileNotFoundError(f"템플릿 스트립이 없습니다: {path} | searched={', '.join(str(p) for p in searched)}")

        strip = load_image_unicode_safe(path, cv2.IMREAD_GRAYSCALE)
        if strip is None:
            raise RuntimeError(f"템플릿 스트립 로드 실패: {path}")


        cfg = self.get_cfg()
        cell_w = int(cfg.get("digit_cell_width", 11))
        cell_h = int(cfg.get("digit_cell_height", 20))
        spacing = int(cfg.get("digit_spacing_max", 2))
        _, binary = cv2.threshold(strip, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if float(np.count_nonzero(binary)) / float(binary.size) > 0.55:
            binary = cv2.bitwise_not(binary)
        new_templates: dict[str, np.ndarray] = {}
        x = 0
        for digit in range(10):
            d = str(digit)
            cell = binary[:, x:x+cell_w]
            new_templates[d] = self._normalize_template_digit(cell, d)
            x += cell_w
            if digit < 9:
                x += spacing
        self.templates = new_templates
        self._templates_cache_by_path[resolved] = new_templates
        self.last_strip_path = resolved
        self._current_loaded_mode = mode

    def _to_binary_variants(self, image_bgr: np.ndarray, mode: str = "squad4") -> dict[str, np.ndarray]:
        # fix_a12: 듀오 모드는 P1(본인)이 베이지/연노란색, P2(팀원)이 흰색.
        # 박스 우측에 캐릭터 옷/신발 등이 침범할 수 있어 Otsu 단순 임계로는
        # 글자만 분리하기 어려운 케이스가 있음 (예: 흰 신발).
        # 두 색을 모두 글자로 인식하는 마스크를 생성.
        # fix_a13: squad3 도 같은 색상 마스크 사용. 다만 박스 테두리(베이지에 가까운 색)가
        # 마스크에 잡히므로 박스 가로/세로 테두리 제거 후처리 필요.
        if mode in ("duo", "squad3"):
            b = image_bgr[:,:,0].astype(np.int32)
            g = image_bgr[:,:,1].astype(np.int32)
            r = image_bgr[:,:,2].astype(np.int32)
            # squad3는 글자 밝기가 낮아 (max gray ~210) 임계값 낮춤
            if mode == "squad3":
                # 베이지 (R 우세)
                beige = (r > 180) & (g > 150) & (b > 110) & (r > b + 15)
                # 흰색
                white = (r > 180) & (g > 180) & (b > 180) & (np.abs(r - b) < 20)
            else:
                # 듀오: 기존 임계값
                beige = (r > 200) & (g > 170) & (b > 130) & (r > b + 20)
                white = (r > 200) & (g > 200) & (b > 200) & (np.abs(r - b) < 15)
            mask = (beige | white).astype(np.uint8) * 255

            # squad3: 박스 가로/세로 테두리 제거
            # 박스 좌/우/하단 테두리는 row/column 활성도가 매우 높음 (전체 폭/높이의 70%+)
            # 글자는 부분 활성 (50% 미만)이라 임계값으로 분리 가능
            if mode == "squad3":
                H_, W_ = mask.shape
                # row 활성도 70% 이상인 행 = 박스 가로 테두리 → 제거
                row_density = (mask > 0).sum(axis=1) / max(W_, 1)
                border_rows = row_density >= 0.70
                mask[border_rows, :] = 0
                # column 활성도 70% 이상인 열 = 박스 세로 테두리 → 제거
                col_density = (mask > 0).sum(axis=0) / max(H_, 1)
                border_cols = col_density >= 0.70
                mask[:, border_cols] = 0

            # 신발/옷 산발 노이즈 제거: column별 활성 픽셀이 2 미만인 col은 제거
            # 글자는 컬럼당 수직 5+ 픽셀 (높이 19 글자), 노이즈 점들은 1px만 활성
            col_sum = (mask > 0).sum(axis=0)
            noise_cols = col_sum < 2
            mask[:, noise_cols] = 0

            # 추가 노이즈 제거: 첫 글자 그룹과 본체에서 떨어진 우측 노이즈 컷
            # 흰 신발이 박스 우측에 침범할 때 나타나는 패턴
            # 본체(첫 활성 그룹)에서 큰 갭(8+ px)으로 떨어진 우측 영역은 글자가 아님
            col_sum = (mask > 0).sum(axis=0)
            active_cols = np.where(col_sum > 0)[0]
            if len(active_cols) > 0:
                # 우측에서부터 큰 갭 찾기
                gaps = np.diff(active_cols)
                large_gap_indices = np.where(gaps >= 8)[0]
                if len(large_gap_indices) > 0:
                    # 마지막 큰 갭 이후의 영역 (우측 끝쪽)을 검사
                    # 그 영역의 활성 column 수가 첫 글자 그룹보다 훨씬 적으면 노이즈
                    last_gap_idx = large_gap_indices[-1]
                    after_gap_start = active_cols[last_gap_idx + 1]
                    after_gap_active = (col_sum[after_gap_start:] > 0).sum()
                    before_gap_active = (col_sum[:after_gap_start] > 0).sum()
                    # 우측 영역의 활성 column이 본체보다 훨씬 적으면 노이즈로 간주
                    if after_gap_active <= 8 and before_gap_active >= 8:
                        mask[:, after_gap_start:] = 0
            return {"tmpl_otsu": mask}

        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        white_ratio = float(np.count_nonzero(otsu)) / float(otsu.size)
        if white_ratio > 0.55:
            otsu = cv2.bitwise_not(otsu)
        return {"tmpl_otsu": otsu}

    def _score_digit(self, candidate: np.ndarray, template: np.ndarray, shifts: list[int]) -> float:
        best = 0.0
        for shift_x in shifts:
            h, w = candidate.shape
            shifted = np.zeros_like(candidate)
            if shift_x >= 0:
                if shift_x < w:
                    shifted[:, shift_x:] = candidate[:, :w-shift_x]
            else:
                sx = abs(shift_x)
                if sx < w:
                    shifted[:, :w-sx] = candidate[:, sx:]
            inter = np.logical_and(shifted > 0, template > 0).sum()
            union = np.logical_or(shifted > 0, template > 0).sum()
            score = float(inter) / float(union) if union else 0.0
            if score > best:
                best = score
        return best

    def _prepare_band(self, binary: np.ndarray, mode: str = "squad4") -> np.ndarray:
        target_h = int(self.get_cfg().get("digit_cell_height", 20))
        target_w = 62  # squad4 기준 band width 고정

        # fix_a8: solo는 글자 폭이 좁아 (14px) 세로선 제거 임계값에 걸리는 문제
        # solo 모드에서는 박스 테두리가 없어 세로선 제거 자체가 불필요
        # fix_a12: duo 모드도 ROI를 박스 안쪽 글자 영역만 잡으므로 박스 테두리 제거 불필요
        # fix_a13: squad3는 _to_binary_variants 단계에서 이미 박스 테두리 제거됨
        if mode in ("solo", "duo", "squad3"):
            cleaned = binary.copy()
        else:
            # 세로선 제거: 전체 행의 85% 이상에 픽셀이 있는 열 → 테두리선으로 간주하고 제거
            col_density = (binary > 0).sum(axis=0) / max(binary.shape[0], 1)
            vert_mask = col_density >= 0.85
            cleaned = binary.copy()
            cleaned[:, vert_mask] = 0

            # 가로줄 제거: 전체 열의 60% 이상에 픽셀이 있는 행 → 상단/하단 테두리선 제거
            row_density = (cleaned > 0).sum(axis=1) / max(cleaned.shape[1], 1)
            horiz_mask = row_density >= 0.60
            cleaned[horiz_mask, :] = 0

        row_sum = (cleaned > 0).sum(axis=1)
        fg_rows = np.where(row_sum > 0)[0]
        if len(fg_rows) == 0:
            # 세로선 제거 후 내용 없으면 원본 사용
            row_sum = (binary > 0).sum(axis=1)
            fg_rows = np.where(row_sum > 0)[0]
            src = binary
        else:
            src = cleaned

        # fix_a8: 글자 높이가 target_h를 초과하면 다운스케일 (솔로 폰트 대응)
        # 글자가 잘려나가지 않도록 글자 영역 전체를 target_h에 맞춤
        if len(fg_rows) > 0:
            glyph_h = int(fg_rows.max() - fg_rows.min() + 1)
            if glyph_h > target_h:
                # 글자 영역만 추출 후 비율 유지하며 다운스케일
                glyph_top = int(fg_rows.min())
                glyph_bot = int(fg_rows.max()) + 1
                # 좌우는 glyph 픽셀이 있는 열만
                col_sum_glyph = (src[glyph_top:glyph_bot] > 0).sum(axis=0)
                fg_cols = np.where(col_sum_glyph > 0)[0]
                if len(fg_cols) > 0:
                    glyph_left = int(fg_cols.min())
                    glyph_right = int(fg_cols.max()) + 1
                    glyph = src[glyph_top:glyph_bot, glyph_left:glyph_right]
                    # 비율 유지하며 height를 target_h에 맞춤
                    # INTER_AREA: 다운스케일 시 영역 평균으로 픽셀 손실 최소화
                    scale = target_h / float(glyph_h)
                    new_w = max(1, int(round(glyph.shape[1] * scale)))
                    resized_glyph = cv2.resize(glyph, (new_w, target_h), interpolation=cv2.INTER_AREA)
                    _, resized_glyph = cv2.threshold(resized_glyph, 127, 255, cv2.THRESH_BINARY)
                    # target_w 폭의 새 band 생성, 원본 좌측 위치 유지 (start_x 호환)
                    band = np.zeros((target_h, target_w), dtype=np.uint8)
                    # 좌측 시작점도 비례 축소 적용
                    new_left = int(round(glyph_left * scale))
                    place_w = min(new_w, max(0, target_w - new_left))
                    if place_w > 0 and new_left < target_w:
                        band[:, new_left:new_left+place_w] = resized_glyph[:, :place_w]
                    return band

        if len(fg_rows) == 0:
            if src.shape[0] >= target_h:
                start = max(0, (src.shape[0] - target_h) // 2)
                crop = src[start:start+target_h, :]
            else:
                crop = src
        else:
            top = int(fg_rows.min()) - 1
            top = max(0, min(top, max(0, src.shape[0] - target_h)))
            crop = src[top:top+target_h, :]

        # height 패딩
        if crop.shape[0] < target_h:
            pad = np.zeros((target_h, crop.shape[1]), dtype=np.uint8)
            pad[:crop.shape[0], :] = crop
            crop = pad

        # width를 target_w로 맞춤
        if crop.shape[1] < target_w:
            band = np.zeros((target_h, target_w), dtype=np.uint8)
            band[:, :crop.shape[1]] = crop
        elif crop.shape[1] > target_w:
            band = crop[:, :target_w]
        else:
            band = crop
        return band

    def _digit_gap(self, prev_digit: str, next_digit: str) -> int:
        return 1 if (prev_digit == "1" or next_digit == "7") else 2

    def _count_holes(self, candidate: np.ndarray) -> int:
        if candidate.size == 0:
            return 0
        fg = (candidate > 0).astype(np.uint8)
        h, w = fg.shape
        padded = np.pad(1 - fg, 1, mode="constant", constant_values=1)
        visited = np.zeros_like(padded, dtype=np.uint8)
        stack = [(0, 0)]
        visited[0, 0] = 1
        while stack:
            y, x = stack.pop()
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < padded.shape[0] and 0 <= nx < padded.shape[1] and not visited[ny, nx] and padded[ny, nx] == 1:
                    visited[ny, nx] = 1
                    stack.append((ny, nx))
        holes = 0
        for y in range(1, h + 1):
            for x in range(1, w + 1):
                if padded[y, x] == 1 and not visited[y, x]:
                    holes += 1
                    visited[y, x] = 1
                    stack = [(y, x)]
                    while stack:
                        cy, cx = stack.pop()
                        for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                            if 0 <= ny < padded.shape[0] and 0 <= nx < padded.shape[1] and padded[ny, nx] == 1 and not visited[ny, nx]:
                                visited[ny, nx] = 1
                                stack.append((ny, nx))
        return holes

    def _shape_bonus(self, candidate: np.ndarray, digit: str, pos: int | None = None, seq_len: int | None = None) -> float:
        ys, xs = np.where(candidate > 0)
        total_fg = int((candidate > 0).sum())
        if total_fg == 0 or len(xs) == 0 or len(ys) == 0:
            return 0.0
        bbox_w = int(xs.max() - xs.min() + 1)
        holes = self._count_holes(candidate)
        h, w = candidate.shape
        third = max(1, w // 3)
        half = max(1, h // 2)
        left_density = float(candidate[:, :third].sum()) / float(max(total_fg, 1))
        center_start = max(0, (w // 2) - 1)
        center_end = min(w, center_start + min(3, w))
        center_density = float(candidate[:, center_start:center_end].sum()) / float(max(total_fg, 1))
        top_density = float(candidate[:half, :].sum()) / float(max(total_fg, 1))
        bottom_density = float(candidate[half:, :].sum()) / float(max(total_fg, 1))
        lower_left_density = float(candidate[half:, :third].sum()) / float(max(total_fg, 1))
        top_band = candidate[:half, :]
        top_holes = self._count_holes(top_band) if top_band.size else 0
        lower_left_block = int((candidate[11:18, :5] > 0).sum()) if candidate.shape[0] >= 18 and candidate.shape[1] >= 5 else 0

        is_first = pos == 0
        is_last = seq_len is not None and pos is not None and pos == seq_len - 1

        bonus = 0.0

        # 첫 자리 전용: 1 / 3 / 7 구분만 강하게 본다.
        if is_first:
            if digit == "1":
                if bbox_w <= 5:
                    bonus += 0.20
                elif bbox_w <= 6:
                    bonus += 0.14
                elif bbox_w >= 8:
                    bonus -= 0.20
                if center_density >= 0.55:
                    bonus += 0.10
                elif center_density <= 0.42:
                    bonus -= 0.08
                if holes > 0:
                    bonus -= 0.05
            elif digit == "3":
                if bbox_w <= 6:
                    bonus -= 0.18
                elif bbox_w >= 8:
                    bonus += 0.02
                if center_density >= 0.54:
                    bonus -= 0.05
                if holes > 0:
                    bonus -= 0.05
            elif digit == "7":
                if center_density <= 0.46:
                    bonus += 0.08
                if center_density >= 0.55:
                    bonus -= 0.12
                if top_density >= 0.62:
                    bonus += 0.05
                if bbox_w <= 5:
                    bonus -= 0.12
                elif bbox_w <= 6:
                    bonus -= 0.08
                # 7은 하단 왼쪽에 픽셀이 없어야 함 (1과 구분)
                if lower_left_density >= 0.15:
                    bonus -= 0.08

        # 마지막 자리 전용: 5 / 9 구분만 강하게 본다.
        if is_last:
            if digit == "5":
                if top_holes == 0:
                    bonus += 0.10
                else:
                    bonus -= 0.08
                if lower_left_density >= 0.18:
                    bonus += 0.05
                if holes >= 1:
                    bonus -= 0.04
            elif digit == "9":
                if top_holes >= 1 and holes == 1:
                    bonus += 0.10
                elif holes >= 2:
                    bonus -= 0.08
                else:
                    bonus -= 0.08
                if lower_left_density <= 0.14:
                    bonus += 0.05
                if bottom_density >= top_density:
                    bonus -= 0.05

            # fix_a12: 마지막 자리 7 / 1 구분 강화
            # 7은 가운데 영역(row 8-12, col 3-7)에 비스듬한 획이 지나가서 픽셀이 많음 (20px)
            # 1은 가운데 세로획만 있어서 적음 (10px)
            # mid_box 픽셀 수가 결정적 차이.
            if candidate.shape[0] >= 13 and candidate.shape[1] >= 8:
                mid_box_last = int((candidate[8:13, 3:8] > 0).sum())
                if digit == "7":
                    if mid_box_last >= 12:
                        bonus += 0.08   # 가운데 영역에 7의 비스듬한 획이 있음
                    elif mid_box_last <= 6:
                        bonus -= 0.05   # 가운데가 너무 비면 7이 아닐 가능성
                elif digit == "1":
                    if mid_box_last >= 14:
                        bonus -= 0.08   # 가운데가 많이 차면 1이 아닌 7
            # fix45: fix41→42와 같은 방식으로 candidate 자체를 직접 측정한다.
            # 마지막 자리 0/9 구분은 "하단 좌측 블록" 픽셀량이 가장 안정적이었다.
            # normalize template 실측 (rows 11:18, cols 0:5):
            #   0: 29px, 6: 29px, 9: 21px
            # 즉 9는 하단 좌측이 비고, 0/6은 하단 좌측이 더 차 있다.
            # 빈도/확률 보정 없이 candidate 구조만 보고 페널티를 준다.
            if digit == "0" and lower_left_block <= 23:
                bonus -= 0.12
            elif digit == "9" and lower_left_block >= 25:
                bonus -= 0.12

        # 중립 보정은 아주 약하게만 남긴다.
        if digit == "8":
            if holes >= 2:
                bonus += 0.05   # holes=2가 8의 핵심 신호 (9는 holes=1)
            elif holes == 0:
                bonus -= 0.04

        # 9 전역 보정: holes==1이 9의 핵심 신호 (8=holes2, 0=holes1이지만 모양 다름)
        if digit == "9":
            if holes == 1:
                bonus += 0.04
            elif holes >= 2:
                bonus -= 0.05   # holes=2면 8일 가능성

        # 8 / 9 전역 구분 (자리 무관)
        # 8: 하단 왼쪽(y11~18, x0~5)이 꽉 참 (양쪽 세로획 + 하단 원)
        # 9: 하단 왼쪽이 비교적 비어있음 (오른쪽 세로획만 내려옴)
        # 실측: 8=28px, 9=22px
        # 추가: 중단 오른쪽(y6~8, x>=7) - 8은 중단이 닫혀있어서 많음, 9도 비슷하지만
        # 9는 하단(y12~17) 중앙이 빔 (오른쪽 세로획만) → holes 차이로도 구분
        if digit in ("8", "9"):
            lower_left_89 = int((candidate[11:18, :5] > 0).sum()) if candidate.shape[0] >= 18 and candidate.shape[1] >= 5 else 0
            mid_right_89 = int((candidate[6:9, 7:] > 0).sum()) if candidate.shape[0] >= 9 and candidate.shape[1] >= 8 else 0
            if digit == "8" and lower_left_89 <= 18:
                bonus -= 0.12   # 하단 왼쪽 비어있음 → 8이 아니라 9
            elif digit == "9" and lower_left_89 >= 27:
                bonus -= 0.12   # 하단 왼쪽 꽉 참 → 9이 아니라 8
            # 9는 holes=1, 8은 holes=2가 핵심이지만 캡처 노이즈로 holes가 같아질 때
            # mid_right로 추가 보정: 9는 중단 오른쪽이 채워짐(>=7), 8도 채워지지만
            # 8에서 holes<=1이면 9일 가능성이 높음
            if digit == "8" and holes <= 1 and mid_right_89 >= 7:
                bonus -= 0.10   # holes 부족 + 중단 오른쪽 참 → 9일 가능성

        # 6 / 9 전역 구분 (자리 무관)
        # 실측: y=6~8, x>=7 구간 픽셀 수
        #   6: 3px (중단 오른쪽이 열림 - 왼쪽 세로획만 있음)
        #   9: 12px (중단 오른쪽이 꽉 참 - 상단 원이 닫혀있음)
        if digit in ("6", "9"):
            mid_right = int((candidate[6:9, 7:] > 0).sum()) if candidate.shape[0] >= 9 and candidate.shape[1] >= 8 else 0
            if digit == "6" and mid_right >= 8:
                bonus -= 0.15   # 중단 오른쪽이 많으면 6이 아니라 9
            elif digit == "9" and mid_right <= 5:
                bonus -= 0.15   # 중단 오른쪽이 없으면 9가 아니라 6

        # 6 / 8 전역 구분 (자리 무관)
        # 6: 중단(y=6~8) 오른쪽이 열려있음 → mid_right 적음(3px)
        # 8: 중단(y=6~8) 오른쪽이 닫혀있음 → mid_right 많음(10px)
        if digit in ("6", "8"):
            mid_right_68 = int((candidate[6:9, 7:] > 0).sum()) if candidate.shape[0] >= 9 and candidate.shape[1] >= 8 else 0
            if digit == "6" and mid_right_68 >= 7:
                bonus -= 0.15   # 중단 오른쪽 꽉 참 → 6이 아니라 8
            elif digit == "8" and mid_right_68 <= 4:
                bonus -= 0.15   # 중단 오른쪽 비어있음 → 8이 아니라 6

        # 5 / 6 전역 구분 (자리 무관)
        # 5: holes=0, 상단 가로획 꽉 참 / 6: holes=1, 상단 곡선
        if digit == "5":
            if holes >= 1:
                bonus -= 0.10   # holes 있으면 5가 아니라 6
        if digit == "6":
            if holes == 0:
                bonus -= 0.10   # holes 없으면 6이 아니라 5

        # fix_a12: 0 / 6 / 8 / 9 구분 강화 - 가운데 영역 픽셀 수 검사
        # fix_a14: 영역 cols 3-7 → cols 4-8로 좁힘
        # 실측 (정규화된 11x20 template, rows 8-13 / cols 4-8 = 5x4 = 20px max):
        #   solo/squad4 strip:
        #     0: 0px  (도넛, 가운데 비어있음)
        #     6: 11px (가운데 짧은 가로)
        #     8: 12px (위/아래 도넛 가운데에서 합류)
        #     9: 14px (가운데 가로획)
        #   duo strip:
        #     0: 0px,  6: 5px,  8: 12px,  9: 16px
        # 한자릿수 0이 8로 잘못 인식되던 케이스의 핵심 시그널.
        if candidate.shape[0] >= 13 and candidate.shape[1] >= 8:
            mid_box_pixels = int((candidate[8:13, 4:8] > 0).sum())
            if digit == "0":
                # 0 후보: mid_box <= 2이어야 정상. 클수록 0 아닐 가능성
                if mid_box_pixels <= 2:
                    bonus += 0.10
                elif mid_box_pixels >= 7:
                    bonus -= 0.18
            elif digit == "8":
                # 8 후보: mid_box >= 7이어야 정상
                if mid_box_pixels >= 7:
                    bonus += 0.05
                elif mid_box_pixels <= 2:
                    bonus -= 0.20   # 가운데 비었으면 8이 아닌 0
                # fix_a14: 우측 상단 strip (rows 6-8, cols 7-10) - 8은 차있음(7-10), 6은 비어있음(2-3)
                if candidate.shape[0] >= 9 and candidate.shape[1] >= 11:
                    right_top = int((candidate[6:9, 7:11] > 0).sum())
                    if right_top <= 4:
                        bonus -= 0.10   # 우측 상단 비어있으면 8이 아닌 6
            elif digit == "6":
                # 6 후보: mid_box 4-9 범위
                if mid_box_pixels <= 2:
                    bonus -= 0.18   # 가운데 비었으면 6이 아닌 0
                elif mid_box_pixels >= 12:
                    bonus -= 0.08   # 가운데 너무 차면 6이 아닌 8
                # fix_a14: 우측 상단 strip (rows 6-8, cols 7-10) - 6은 비어있어야 정상 (2-3px)
                if candidate.shape[0] >= 9 and candidate.shape[1] >= 11:
                    right_top = int((candidate[6:9, 7:11] > 0).sum())
                    if right_top <= 4:
                        bonus += 0.05   # 우측 상단 비어있으면 6 맞음
                    elif right_top >= 7:
                        bonus -= 0.10   # 우측 상단 차있으면 6이 아닌 8
            elif digit == "9":
                # 9: 위 도넛 + 가운데 가로획. mid_box: solo=9, squad4=14, duo=16, 3=12
                # fix_a14: 임계값 10 → 14 (3의 mid_box 12와 9의 mid_box 14+ 분리)
                # 진짜 9에만 보너스, 3이 9로 잘못 가는 것 방지
                if mid_box_pixels >= 14:
                    bonus += 0.04
                elif mid_box_pixels <= 3:
                    bonus -= 0.15   # 가운데 비었으면 9가 아닌 0
                # 추가 신호: 좌측 strip (rows 7-13, cols 0-2) - 9는 도넛 좌측 + 가운데 가로 → 8~9px
                # 3은 좌측 가로획만 있어 0px (실측: squad4 3=0/9=8, duo 3=0/9=9)
                if candidate.shape[0] >= 14 and candidate.shape[1] >= 3:
                    left_strip = int((candidate[7:14, 0:3] > 0).sum())
                    if left_strip >= 5:
                        bonus += 0.06   # 좌측 strip 차있으면 9 (3과 분리)
                    elif left_strip <= 1:
                        bonus -= 0.10   # 좌측 strip 비어있으면 9가 아닌 3
            elif digit == "3":
                # fix_a14: 3 추가 신호 - 좌측 strip이 비어있어야 정상 3
                if candidate.shape[0] >= 14 and candidate.shape[1] >= 3:
                    left_strip = int((candidate[7:14, 0:3] > 0).sum())
                    if left_strip <= 1:
                        bonus += 0.05   # 좌측 strip 비어있으면 3 맞음
                    elif left_strip >= 5:
                        bonus -= 0.10   # 좌측 strip 차있으면 3이 아닌 9

        # fix_a15: 2 vs 7 구분 (자리 무관)
        # 2: 하단 가로 받침 명확 (rows 17-19 흰픽셀 ~10px씩)
        # 7: 하단 가로 받침 없음, 좌하단으로 가는 사선 (rows 17-19 흰픽셀 ~3-4px)
        # 실측 (squad4 11x20 template):
        #   2: r17=11, r18=11, r19=11, sum=33
        #   7: r17=3,  r18=3,  r19=4,  sum=10
        # 12번 P1딜 case (172→177 오인식): 마지막 자리 r17=4, r18=10, r19=10, sum=24 → 2의 패턴
        if digit in ("2", "7"):
            if candidate.shape[0] >= 20 and candidate.shape[1] >= 8:
                bottom_3rows = int((candidate[17:20, :] > 0).sum())
                if digit == "2":
                    if bottom_3rows >= 18:
                        bonus += 0.06   # 하단 받침 꽉 참 → 2 맞음
                    elif bottom_3rows <= 12:
                        bonus -= 0.10   # 하단 받침 빈약 → 2가 아닌 7
                elif digit == "7":
                    if bottom_3rows >= 18:
                        bonus -= 0.10   # 하단 받침 꽉 참 → 7이 아닌 2
                    elif bottom_3rows <= 12:
                        bonus += 0.06   # 하단 받침 빈약 → 7 맞음

        return bonus

    def _candidate_lengths(self) -> list[int]:
        return [1, 2, 3, 4]

    def _mid_active_width(self, raw: np.ndarray) -> int:
        """raw crop(bbox crop 전)의 중단부(y=5~15) 수직 투영 활성 열 수."""
        h = raw.shape[0]
        y_start = min(5, h)
        y_end   = min(15, h)
        if y_start >= y_end:
            return 0
        mid = raw[y_start:y_end, :]
        return int((mid > 0).sum(axis=0).astype(bool).sum())

    def _score_digit_at(self, band: np.ndarray, start_x: int, digit: str, shifts: list[int], pos: int | None = None, seq_len: int | None = None) -> tuple[float, tuple[int, int]] | None:
        width = self._target_width_for_digit(digit)
        end_x = start_x + width
        if start_x < 0 or end_x > band.shape[1]:
            return None
        raw = band[:, start_x:end_x]
        ys, xs = np.where(raw > 0)
        if len(xs) == 0 or len(ys) == 0:
            candidate = np.zeros((20, width), dtype=np.uint8)
        else:
            cropped = raw[ys.min():ys.max()+1, xs.min():xs.max()+1]
            resized = cv2.resize(cropped, (width, 20), interpolation=cv2.INTER_NEAREST)
            candidate = (resized > 0).astype(np.uint8)
        template = self.templates[digit]
        score = self._score_digit(candidate, template, shifts)
        score = max(0.0, min(1.0, score + self._shape_bonus(candidate, digit, pos=pos, seq_len=seq_len)))

        # 1 / 7 전역 분류 패널티 (중단부 수직 투영 폭 기반)
        # 템플릿 실측: 1의 중단부 폭=2~3px, 7의 중단부 폭=9~11px (솔로/스쿼드4)
        # 듀오 실측: 1의 중단부 폭=2px, 7의 중단부 폭=6~7px (글자 작아서 좁음)
        # fix_a12: 1 패널티 임계 9 → 6 (듀오 7의 mid_w 6-7가 1로 잘못 잡히는 문제 해결)
        if digit in ("1", "7"):
            mid_w = self._mid_active_width(raw)
            if digit == "7" and mid_w <= 4:
                score -= 0.30   # 중단부가 좁으면 7이 아니라 1
            elif digit == "1" and mid_w >= 6:
                score -= 0.20   # 중단부가 일정 폭 이상이면 1이 아니라 7
            score = max(0.0, score)

        # 1 / 3 전역 분류 패널티 (중단부 연속 픽셀 구간 최대폭 기반)
        # 실측 (band에서 실제 1픽셀을 3 window로 잘랐을 때):
        #   1의 max_group_w=4px, 3 템플릿의 max_group_w=10px
        # body_right 방식은 1의 하단 세리프가 번져서 실패
        # 연속 구간 폭이 결정적 차이: 1은 가는 세로획(4px), 3은 넓음(10px)
        if digit == "3":
            h = raw.shape[0]
            y1_mid, y2_mid = min(4, h), min(16, h)
            mid = raw[y1_mid:y2_mid, :]
            col_active = (mid > 0).sum(axis=0) > 0
            groups = []
            in_g = False
            for c in range(len(col_active)):
                if col_active[c] and not in_g:
                    in_g = True; gs = c
                elif not col_active[c] and in_g:
                    in_g = False; groups.append(c - gs)
            if in_g:
                groups.append(len(col_active) - gs)
            max_group_w = max(groups) if groups else 0
            score_before = score
            if max_group_w <= 5:
                score -= 0.30   # 중단부 연속 구간이 좁으면 3이 아니라 1
            score = max(0.0, score)

        return score, (start_x, end_x)

    def _digit_options(self, band: np.ndarray, field_name: str, pos: int, seq_len: int, prev_end: int | None, prev_digit: str | None, shift: int) -> list[dict]:
        cfg = self.get_cfg()
        shifts = list(cfg.get("shift_candidates", [-1, 0, 1]))
        base_x = int(cfg.get("squad4_start_x", 2)) + shift
        options: list[dict] = []
        for digit in "0123456789":
            start_x = base_x if pos == 0 else int(prev_end) + self._digit_gap(str(prev_digit), digit)
            scored = self._score_digit_at(band, start_x, digit, shifts, pos=pos, seq_len=seq_len)
            if scored is None:
                continue
            score, span = scored
            options.append({
                "digit": digit,
                "score": float(score),
                "span": span,
                "end_x": span[1],
            })
        options.sort(key=lambda item: (item["score"], item["digit"] != "1", item["digit"]), reverse=True)
        top_n = 3
        min_local = 0.12
        filtered = [item for item in options if item["score"] >= min_local]
        return (filtered or options)[:top_n]

    def _finalize_candidate(self, band: np.ndarray, field_name: str, variant_name: str, shift: int, seq: str, scores: list[float], spans: list[tuple[int, int]]) -> dict:
        total_fg = int((band > 0).sum())
        first_start = spans[0][0] if spans else 0
        glyph_width = 0
        total_span_width = 0
        occupied_width = 0
        width_mismatch = 0.0
        glyph_width_mismatch = 0.0
        if total_fg > 0:
            covered = np.zeros_like(band, dtype=np.uint8)
            for sx, ex in spans:
                covered[:, sx:ex] = 1
                glyph_width += max(0, ex - sx)
            if spans:
                total_span_width = max(0, spans[-1][1] - spans[0][0])
            uncovered_fg = int(np.logical_and(band > 0, covered == 0).sum())
            uncovered_ratio = uncovered_fg / float(total_fg)
            leading_mask = np.zeros_like(band, dtype=np.uint8)
            leading_mask[:, :max(0, first_start)] = 1
            leading_fg = int(np.logical_and(band > 0, leading_mask > 0).sum())
            leading_uncovered_ratio = leading_fg / float(total_fg)
            col_sum = (band > 0).sum(axis=0)
            fg_cols = np.where(col_sum > 0)[0]
            if len(fg_cols) > 0:
                occupied_width = int(fg_cols.max() - fg_cols.min() + 1)
                width_mismatch = abs(total_span_width - occupied_width) / float(max(occupied_width, 1))
                glyph_width_mismatch = abs(glyph_width - occupied_width) / float(max(occupied_width, 1))
        else:
            uncovered_ratio = 0.0
            leading_uncovered_ratio = 0.0
        avg_score = sum(scores) / len(scores)
        min_score = min(scores)
        length_bonus = 0.0
        short_penalty = 0.0
        shift_penalty = 0.0
        width_penalty = 0.0
        if len(seq) >= 3:
            length_bonus += 0.04 * (len(seq) - 2)
        elif len(seq) == 2:
            short_penalty += 0.08
        elif len(seq) == 1:
            short_penalty += 0.30
        shift_penalty += 0.0 * abs(shift)  # penalty 제거: 올바른 shift를 방해하던 문제 수정
        width_penalty += 1.15 * width_mismatch
        width_penalty += 0.20 * glyph_width_mismatch
        length_fit_bonus = 0.0
        if len(seq) >= 2 and total_span_width > 0 and occupied_width > 0:
            length_fit_bonus = max(0.0, 1.0 - width_mismatch)
            width_penalty -= 0.06 * length_fit_bonus
            if "1" in seq:
                ones_bonus = 0.02 * seq.count("1") * length_fit_bonus
                width_penalty -= ones_bonus
            if "7" in seq:
                sevens_bonus = 0.015 * seq.count("7") * length_fit_bonus
                width_penalty -= sevens_bonus
        pattern_bonus = 0.0
        low_penalty = 0.0
        if min_score < 0.40:
            low_penalty += 0.18
        uncovered_penalty = (uncovered_ratio * 0.95) + (leading_uncovered_ratio * 1.35)
        final_score = avg_score + length_bonus + pattern_bonus - uncovered_penalty - short_penalty - low_penalty - shift_penalty - width_penalty
        return {
            "method": "template_rule",
            "variant": variant_name,
            "digits": seq,
            "value": int(seq),
            "avg_score": avg_score,
            "min_score": min_score,
            "digit_count": len(seq),
            "scores": [round(s, 4) for s in scores],
            "final_score": round(final_score, 4),
            "start_shift": shift,
            "uncovered_ratio": round(uncovered_ratio, 4),
            "leading_uncovered_ratio": round(leading_uncovered_ratio, 4),
            "covered_width": glyph_width,
            "total_span_width": total_span_width,
            "occupied_width": occupied_width,
            "width_mismatch": round(width_mismatch, 4),
            "glyph_width_mismatch": round(glyph_width_mismatch, 4),
        }

    def _score_fixed_sequence(self, band: np.ndarray, field_name: str, variant_name: str, shift: int, seq: str) -> dict | None:
        cfg = self.get_cfg()
        shifts = list(cfg.get("shift_candidates", [-1, 0, 1]))
        base_x = int(cfg.get("squad4_start_x", 2)) + shift
        scores: list[float] = []
        spans: list[tuple[int, int]] = []
        prev_end = None
        prev_digit = None
        for pos, digit in enumerate(seq):
            start_x = base_x if pos == 0 else int(prev_end) + self._digit_gap(str(prev_digit), digit)
            scored = self._score_digit_at(band, start_x, digit, shifts, pos=pos, seq_len=len(seq))
            if scored is None:
                return None
            score, span = scored
            scores.append(score)
            spans.append(span)
            prev_end = span[1]
            prev_digit = digit
        return self._finalize_candidate(band, field_name, variant_name, shift, seq, scores, spans)

    def _refine_position_limited(self, band: np.ndarray, field_name: str, variant_name: str, shift: int, result: dict) -> dict:
        if field_name != "damage":
            return result
        seq = str(result.get("digits", ""))
        if len(seq) < 2:
            return result
        candidates = [result]
        suffix = seq[1:]
        # First digit local recheck only among 1/3/7.
        for d in ("1", "3", "7"):
            if d == seq[0]:
                continue
            fixed = self._score_fixed_sequence(band, field_name, variant_name, shift, d + suffix)
            if fixed is not None:
                candidates.append(fixed)
        prefix = seq[:-1]
        # Last digit local recheck only among 5/9.
        for d in ("5", "9"):
            if d == seq[-1]:
                continue
            fixed = self._score_fixed_sequence(band, field_name, variant_name, shift, prefix + d)
            if fixed is not None:
                candidates.append(fixed)
        candidates.sort(key=lambda item: (float(item.get("final_score", 0.0)), float(item.get("min_score", 0.0)), -float(item.get("width_mismatch", 1.0))), reverse=True)
        return candidates[0]

    def _search_sequences(self, band: np.ndarray, field_name: str, variant_name: str, shift: int) -> list[dict]:
        cfg = self.get_cfg()
        beam_width = int(cfg.get("squad4_beam_width_damage", 12))
        all_results: list[dict] = []
        for length in self._candidate_lengths():
            beam = [{"seq": "", "scores": [], "spans": [], "prev_end": None, "prev_digit": None, "rank": 0.0}]
            for pos in range(length):
                next_beam = []
                for state in beam:
                    options = self._digit_options(band, field_name, pos, length, state["prev_end"], state["prev_digit"], shift)
                    for option in options:
                        seq = state["seq"] + option["digit"]
                        if pos == 0 and option["digit"] == "0" and length > 1:
                            continue  # damage 다자리 첫 자리 0은 불가 (050 같은 오독 차단, 단 1자리는 허용)
                        scores = [*state["scores"], option["score"]]
                        spans = [*state["spans"], option["span"]]
                        rank = sum(scores) / len(scores)
                        if field_name == "damage":
                            rank += 0.015 * max(0, len(seq) - 1)
                        if min(scores) < 0.33:
                            rank -= 0.12
                        next_beam.append({
                            "seq": seq,
                            "scores": scores,
                            "spans": spans,
                            "prev_end": option["end_x"],
                            "prev_digit": option["digit"],
                            "rank": rank,
                        })
                if not next_beam:
                    beam = []
                    break
                next_beam.sort(key=lambda item: (item["rank"], len(item["seq"]), item["seq"]), reverse=True)
                beam = next_beam[:beam_width]
            for state in beam:
                if state["seq"]:
                    all_results.append(self._finalize_candidate(band, field_name, variant_name, shift, state["seq"], state["scores"], state["spans"]))
        return all_results

    def _recognize_squad4_rule(self, image_bgr: np.ndarray, field_name: str, debug_dir: Path, debug_name: str, mode: str = "squad4") -> dict | None:
        self.last_reject_reason = None
        self.last_best_result = None
        variants = self._to_binary_variants(image_bgr, mode)
        cfg = self.get_cfg()
        save_debug = bool(self.config_provider().get("save_debug_images", False))
        x_shifts = list(cfg.get("squad4_start_shift_candidates", [0]))
        best_result = None
        second_result = None
        for variant_name, binary in variants.items():
            band = self._prepare_band(binary, mode)

            # 첫픽셀 자동 감지: band 실제 첫 fg 픽셀 위치에 맞는 shift 추가
            col_sum = (band > 0).sum(axis=0)
            fg_cols = [c for c in range(band.shape[1]) if col_sum[c] > 0]
            if fg_cols:
                first_fg = fg_cols[0]
                # 모드별 start_x 오버라이드 (solo/duo/squad3는 박스 위치 차이로 다른 값 사용)
                mode_cfg = self.config_provider().get("modes", {}).get(mode, {})
                start_x = int(mode_cfg.get("start_x", cfg.get("squad4_start_x", 4)))
                # first_fg가 base_x가 되도록 필요한 shift 계산
                exact_shift = first_fg - start_x
                candidate_shifts = set(x_shifts)
                # exact_shift와 exact_shift-1 추가 (1px 여유)
                # 단, 양수 shift는 숫자가 오른쪽으로 치우쳐 왼쪽이 잘리므로 허용하지 않음
                for s in [exact_shift, exact_shift - 1]:
                    if s <= 0:  # 왼쪽 방향만 허용
                        candidate_shifts.add(s)
                x_shifts = sorted(candidate_shifts, reverse=True)
            if save_debug:
                try:
                    save_image_unicode_safe(debug_dir / f"{debug_name}_{variant_name}_binary.png", binary)
                    save_image_unicode_safe(debug_dir / f"{debug_name}_{variant_name}_band.png", band)
                except Exception:
                    pass
            results = []
            for shift in x_shifts:
                seq_results = self._search_sequences(band, field_name, variant_name, shift)
                if field_name == "damage" and seq_results:
                    seq_results.sort(key=lambda item: (float(item.get("final_score", 0.0)), float(item.get("min_score", 0.0)), -float(item.get("width_mismatch", 1.0))), reverse=True)
                    refined: list[dict] = []
                    for item in seq_results[:8]:
                        refined.append(self._refine_position_limited(band, field_name, variant_name, shift, item))
                    seq_results.extend(refined)
                results.extend(seq_results)
            results.sort(key=lambda item: (item["final_score"], -item.get("width_mismatch", 1.0), -item.get("uncovered_ratio", 1.0), -item.get("leading_uncovered_ratio", 1.0), item["digit_count"], item["avg_score"]), reverse=True)
            for result in results[:2]:
                if best_result is None or (result["final_score"], result["digit_count"], result["avg_score"]) > (best_result["final_score"], best_result["digit_count"], best_result["avg_score"]):
                    second_result = best_result
                    best_result = result
                elif second_result is None or (result["final_score"], result["digit_count"], result["avg_score"]) > (second_result["final_score"], second_result["digit_count"], second_result["avg_score"]):
                    second_result = result
        if best_result is None:
            self.last_reject_reason = "candidate_empty"
            self.last_best_result = None
            return None
        self.last_best_result = dict(best_result)
        min_final = float(cfg.get("squad4_min_final_score_damage", 0.44))
        min_first = float(cfg.get("squad4_min_first_digit_score_damage", 0.36))
        max_uncovered = float(cfg.get("squad4_max_uncovered_damage", 0.22))
        max_leading = float(cfg.get("squad4_max_leading_uncovered_damage", 0.18))
        first_score = float(best_result["scores"][0]) if best_result.get("scores") else 0.0
        uncovered_ratio = float(best_result.get("uncovered_ratio", 1.0))
        leading_uncovered_ratio = float(best_result.get("leading_uncovered_ratio", 1.0))
        reason_parts = []
        if best_result["final_score"] < min_final:
            reason_parts.append(f"final<{min_final:.2f}")
        if first_score < min_first:
            reason_parts.append(f"first<{min_first:.2f}")
        if uncovered_ratio > max_uncovered:
            reason_parts.append(f"uncovered>{max_uncovered:.2f}")
        if leading_uncovered_ratio > max_leading:
            reason_parts.append(f"leading>{max_leading:.2f}")
        if second_result is not None:
            delta = float(best_result["final_score"]) - float(second_result["final_score"])
            best_result["second_final_score"] = round(float(second_result["final_score"]), 4)
            best_result["final_delta"] = round(delta, 4)
            min_delta = 0.003
            same_value = best_result.get("value") == second_result.get("value")
            if delta < min_delta and not same_value:
                second_uncovered = float(second_result.get("uncovered_ratio", 1.0))
                second_leading = float(second_result.get("leading_uncovered_ratio", 1.0))
                second_digits = int(second_result.get("digit_count", 0))
                best_digits = int(best_result.get("digit_count", 0))
                prefer_second = False
                if field_name == "damage":
                    if (second_uncovered + 0.06 < uncovered_ratio) or (second_leading + 0.05 < leading_uncovered_ratio):
                        prefer_second = True
                    elif second_digits > best_digits and (float(second_result["final_score"]) + 0.015) >= float(best_result["final_score"]):
                        prefer_second = True
                if prefer_second:
                    best_result, second_result = second_result, best_result
                    first_score = float(best_result["scores"][0]) if best_result.get("scores") else 0.0
                    uncovered_ratio = float(best_result.get("uncovered_ratio", 1.0))
                    leading_uncovered_ratio = float(best_result.get("leading_uncovered_ratio", 1.0))
                    reason_parts = []
                    if best_result["final_score"] < min_final:
                        reason_parts.append(f"final<{min_final:.2f}")
                    if first_score < min_first:
                        reason_parts.append(f"first<{min_first:.2f}")
                    if uncovered_ratio > max_uncovered:
                        reason_parts.append(f"uncovered>{max_uncovered:.2f}")
                    if leading_uncovered_ratio > max_leading:
                        reason_parts.append(f"leading>{max_leading:.2f}")
                    delta = float(best_result["final_score"]) - float(second_result["final_score"])
                    best_result["second_final_score"] = round(float(second_result["final_score"]), 4)
                    best_result["final_delta"] = round(delta, 4)
                elif delta < min_delta and not same_value:
                    reason_parts.append(f"delta<{min_delta:.3f}")
        if reason_parts:
            self.last_reject_reason = ";".join(reason_parts)
            return None
        self.last_reject_reason = "accepted"
        return best_result

    def recognize(self, image_bgr: np.ndarray, field_name: str, debug_dir: Path, debug_name: str, mode: str) -> dict | None:
        if mode not in ("solo", "duo", "squad3", "squad4"):
            return None
        # fix_a8: solo 모드는 별도 strip 로드
        self.ensure_loaded(mode)
        return self._recognize_squad4_rule(image_bgr, field_name, debug_dir, debug_name, mode)

def detect_game_mode(frame_bgr, modes_cfg: dict) -> str:
    """결과화면 이미지에서 인원 모드를 자동 감지."""
    import cv2 as _cv2
    h, w = frame_bgr.shape[:2]
    if w != 1920 or h != 1080:
        frame_bgr = _cv2.resize(frame_bgr, (1920, 1080), interpolation=_cv2.INTER_LINEAR)
    best_mode = "squad4"
    best_score = -1.0
    for mode, mcfg in modes_cfg.items():
        players = mcfg.get("players", [])
        if not players:
            continue
        total = 0.0
        for p in players:
            roi = p.get("damage")
            if not roi:
                continue
            x1=int(roi[0]*1920); y1=int(roi[1]*1080)
            x2=int(roi[2]*1920); y2=int(roi[3]*1080)
            crop = frame_bgr[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            gray = _cv2.cvtColor(crop, _cv2.COLOR_BGR2GRAY)
            total += float((gray > 150).sum()) / max(gray.size, 1)
        avg = total / len(players)
        if avg > best_score:
            best_score = avg
            best_mode = mode
    return best_mode


def score_result_screen(frame_bgr, modes_cfg: dict) -> tuple[str, float]:
    """fix_a15: 결과창 신뢰도 평가.
    detect_game_mode와 같은 측정 기준이지만, best_mode와 함께 best_score도 반환.
    호출부에서 score가 임계값(RESULT_SCREEN_MIN_SCORE) 이하면 "결과창 아님"으로 판단 가능.

    실측 기반 임계값 분포 (스쿼드 50장 + invalid 2장):
    - 정상 결과창 best_score: 0.1008 ~ 0.2780 (n=50)
    - 결과창 아님 (다음 페이지 등): 0.0490 ~ 0.0669 (n=2)
    - 권장 임계값: 0.085 (양쪽에 충분한 마진)
    """
    import cv2 as _cv2
    h, w = frame_bgr.shape[:2]
    if w != 1920 or h != 1080:
        frame_bgr = _cv2.resize(frame_bgr, (1920, 1080), interpolation=_cv2.INTER_LINEAR)
    best_mode = "squad4"
    best_score = -1.0
    for mode, mcfg in modes_cfg.items():
        players = mcfg.get("players", [])
        if not players:
            continue
        total = 0.0
        for p in players:
            roi = p.get("damage")
            if not roi:
                continue
            x1=int(roi[0]*1920); y1=int(roi[1]*1080)
            x2=int(roi[2]*1920); y2=int(roi[3]*1080)
            crop = frame_bgr[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            gray = _cv2.cvtColor(crop, _cv2.COLOR_BGR2GRAY)
            total += float((gray > 150).sum()) / max(gray.size, 1)
        avg = total / len(players)
        if avg > best_score:
            best_score = avg
            best_mode = mode
    return best_mode, max(0.0, best_score)


# fix_a15: 결과창 진입 게이트 임계값
# 이 값 이하의 best_score는 결과창이 아닌 캡처(다음 페이지 등)로 판단하여 OCR 스킵
RESULT_SCREEN_MIN_SCORE = 0.085

def scan_steam_image_files(folder: Path) -> list[Path]:
    """스팀 스크린샷 폴더의 최상위 이미지 파일만 반환 (하위폴더 제외)."""
    folder = Path(folder)
    if not folder.exists():
        return []
    files: list[Path] = []
    try:
        for p in folder.iterdir():
            if p.is_file() and p.suffix.lower() in STEAM_IMAGE_EXTENSIONS:
                files.append(p)
    except Exception:
        return []
    return files


def snapshot_steam_files(folder: Path) -> tuple[dict[Path, float], dict[str, int], str]:
    snapshot: dict[Path, float] = {}
    ext_counts: Counter[str] = Counter()
    latest_path = ""
    latest_mtime = -1.0
    for p in scan_steam_image_files(folder):
        try:
            rp = p.resolve()
            mtime = p.stat().st_mtime
        except FileNotFoundError:
            continue
        except Exception:
            continue
        snapshot[rp] = mtime
        ext_counts[p.suffix.lower()] += 1
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest_path = str(p)
    return snapshot, dict(ext_counts), latest_path


def send_virtual_key(vk_code: int) -> str:
    if os.name != "nt":
        return "non_windows"
    user32 = ctypes.windll.user32
    try:
        ULONG_PTR = ctypes.POINTER(ctypes.c_ulong)

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", ULONG_PTR)]

        class _INPUTUNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("union", _INPUTUNION)]

        KEYEVENTF_KEYUP = 0x0002
        INPUT_KEYBOARD = 1
        press = INPUT(type=INPUT_KEYBOARD, union=_INPUTUNION(ki=KEYBDINPUT(wVk=vk_code, wScan=0, dwFlags=0, time=0, dwExtraInfo=None)))
        release = INPUT(type=INPUT_KEYBOARD, union=_INPUTUNION(ki=KEYBDINPUT(wVk=vk_code, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)))
        sent = user32.SendInput(2, (INPUT * 2)(press, release), ctypes.sizeof(INPUT))
        if sent == 2:
            return "SendInput"
    except Exception:
        pass

    try:
        user32.keybd_event(vk_code, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(vk_code, 0, 2, 0)
        return "keybd_event"
    except Exception:
        return "failed"


class TrayManager:
    """fix_a17: 시스템 트레이 아이콘 관리.
    pystray를 별도 스레드에서 돌려 pywebview 메인 스레드와 분리.
    좌클릭 = 창 복원, 우클릭 메뉴 = [앱 표시, 종료].
    pystray 미설치 시 조용히 비활성화 (앱 동작 자체엔 영향 없음).
    """

    def __init__(
        self,
        icon_path: Path,
        on_show,
        on_quit,
        event_logger=None,
        error_logger=None,
    ) -> None:
        self.icon_path = icon_path
        self.on_show = on_show
        self.on_quit = on_quit
        self.event_logger = event_logger
        self.error_logger = error_logger
        self._icon = None
        self._thread: threading.Thread | None = None
        self._available = self._probe()

    @staticmethod
    def _probe() -> bool:
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401
            return True
        except Exception:
            return False

    @property
    def available(self) -> bool:
        return self._available

    def start(self) -> None:
        """트레이 아이콘 활성화. 이미 활성화돼 있거나 pystray 없으면 no-op."""
        if not self._available:
            if self.event_logger:
                self.event_logger.warning("트레이: pystray 미설치 - 트레이 비활성")
            return
        if self._icon is not None:
            return
        try:
            import pystray
            from PIL import Image

            try:
                image = Image.open(str(self.icon_path))
            except Exception as exc:
                # 아이콘 로드 실패 시 단색 fallback (사용자 눈에는 거의 안 띄지만 동작은 유지)
                if self.error_logger:
                    self.error_logger.warning("트레이 아이콘 로드 실패, fallback 사용: %s", exc)
                image = Image.new("RGB", (32, 32), color=(20, 24, 32))

            def _menu_show(icon, item):
                try:
                    self.on_show()
                except Exception:
                    if self.error_logger:
                        self.error_logger.exception("트레이 메뉴 show 실패")

            def _menu_quit(icon, item):
                try:
                    self.on_quit()
                except Exception:
                    if self.error_logger:
                        self.error_logger.exception("트레이 메뉴 quit 실패")
                finally:
                    try:
                        icon.stop()
                    except Exception:
                        pass

            def _on_left_click(icon, item):
                # 좌클릭 = default 액션 = 창 복원
                try:
                    self.on_show()
                except Exception:
                    if self.error_logger:
                        self.error_logger.exception("트레이 좌클릭 show 실패")

            menu = pystray.Menu(
                pystray.MenuItem("앱 표시", _on_left_click, default=True),
                pystray.MenuItem("종료", _menu_quit),
            )
            self._icon = pystray.Icon(
                "pubg_streamer_helper",
                image,
                "PUBG 스트리머 헬퍼",
                menu,
            )

            def _run():
                try:
                    self._icon.run()
                except Exception:
                    if self.error_logger:
                        self.error_logger.exception("트레이 run 실패")

            self._thread = threading.Thread(target=_run, name="TrayThread", daemon=True)
            self._thread.start()
            if self.event_logger:
                self.event_logger.info("트레이 아이콘 활성화")
        except Exception:
            if self.error_logger:
                self.error_logger.exception("트레이 시작 실패")
            self._icon = None

    def stop(self) -> None:
        """트레이 아이콘 제거. 비활성 상태면 no-op."""
        if self._icon is None:
            return
        try:
            self._icon.stop()
        except Exception:
            if self.error_logger:
                self.error_logger.exception("트레이 stop 실패")
        finally:
            self._icon = None
            self._thread = None


class WebApi:
    """JS ↔ Python 브릿지. pywebview가 이 클래스의 public 메서드를 JS에 노출."""

    def __init__(self) -> None:
        bootstrap_runtime_files()
        self.config = load_config()
        self.event_logger, self.error_logger, self.console_logger = setup_logging(self.config)

        # 중복 실행 방지
        # fix_a9: PID 재사용 시 영구 차단 방지 - 프로세스 이름 매칭 + 락 파일 mtime 검증
        self._lock_file = RUNTIME_BASE_DIR / ".running.lock"
        self._is_duplicate = False
        if self._lock_file.exists():
            try:
                # 1단계: 락 파일이 너무 오래됐으면 (1일 이상) 무효 처리
                # 정상 종료 시 cleanup이 락 파일 삭제하므로, 1일 이상 남아있다면 비정상 종료 잔재
                lock_age_seconds = time.time() - self._lock_file.stat().st_mtime
                if lock_age_seconds > 86400:  # 24시간
                    self._lock_file.unlink(missing_ok=True)
                else:
                    pid = int(self._lock_file.read_text().strip())
                    import psutil
                    if psutil.pid_exists(pid):
                        # 2단계: 프로세스 이름 검증 - PID 재사용 케이스 감지
                        # 본 앱의 exe명을 추출 (PyInstaller 빌드 시 sys.executable이 본인 exe)
                        # 개발 환경에서는 python.exe 인데 그 경우는 아래 검증을 관대하게 처리
                        try:
                            proc = psutil.Process(pid)
                            proc_name = proc.name().lower()
                            self_exe_name = Path(sys.executable).name.lower()
                            # 본인 프로세스 이름과 일치하면 진짜 중복 실행
                            # 또는 frozen이 아닌 개발 환경에서는 python 계열 프로세스면 중복으로 간주
                            if getattr(sys, "frozen", False):
                                # PyInstaller 빌드: exe명 직접 비교
                                self._is_duplicate = (proc_name == self_exe_name)
                            else:
                                # 개발 환경: python.exe 또는 pythonw.exe면 중복으로 간주
                                self._is_duplicate = proc_name in ("python.exe", "pythonw.exe", "python", "pythonw")
                            if not self._is_duplicate:
                                # PID는 살아있지만 다른 프로세스 - 락 파일 무효
                                self._lock_file.unlink(missing_ok=True)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            # 프로세스 정보 접근 실패 - 락 파일 무효 처리
                            self._lock_file.unlink(missing_ok=True)
                    else:
                        # PID 자체가 없음 - 락 파일 무효
                        self._lock_file.unlink(missing_ok=True)
            except Exception:
                # 락 파일 파싱 실패 등 - 안전하게 락 파일 제거
                try:
                    self._lock_file.unlink(missing_ok=True)
                except Exception:
                    pass
        if not self._is_duplicate:
            try:
                self._lock_file.write_text(str(os.getpid()))
            except Exception:
                pass

        self.last_run_time = 0.0
        self._calc_lock = threading.Lock()
        self._last_damage: int = 0
        self._last_kill: int = 0
        self._last_details: list[RoiResult] = []
        self._last_is_chicken: bool = False
        self._last_result_text: str = ""
        self._pending_auto_send_message: str = ""
        self._last_capture_mode_used: str = ""
        self._last_capture_source: str = ""
        self._last_capture_saved: str = ""
        # fix_a19: 가장 최근 OCR 결과의 history 항목 ID (수정/송출 시 동기화용)
        self._last_history_id: int = 0
        # fix_a19: 송출 상태 메시지 (poll_state로 UI에 노출)
        self._last_send_status: str = ""
        self._pending_hotkey_count = 0
        self._pending_hotkey_lock = threading.Lock()
        self._steam_watch_queue: queue.Queue[Path] = queue.Queue()
        self._steam_watch_thread: threading.Thread | None = None
        self._steam_watch_stop = threading.Event()
        self._steam_watch_snapshot: dict[Path, float] = {}
        self._steam_watch_dir: str = ""
        self._connecting = False
        self._connect_lock = threading.Lock()
        self._last_connect_message = ""
        self._monitor_var = str(self.config.get("monitor_index", 1))
        self._hotkey_enabled = bool(self.config.get("hotkey", {}).get("enabled", True))
        self._chat_enabled = bool(self.config.get("chat", {}).get("enabled", True))
        self._multiplier = int(self.config.get("chat_multiplier", 1))

        self.chat_client = ChzzkChatClient(lambda: self.config, self.event_logger, self.error_logger, self.console_logger)
        self.template_matcher = DigitTemplateMatcher(lambda: self.config, self.event_logger, self.error_logger, self.console_logger)

        self._hotkey_manager = HotkeyManager(
            self._on_hotkey, self.event_logger, self.error_logger, self.console_logger,
            send_callback=self._on_hotkey_send,  # fix_a19: F9 송출 핫키
        )
        # 화면 캡처 모드일 때만 F8 핫키 활성 (스팀 모드는 파일 감지 자동)
        if self._hotkey_enabled and self.config.get("capture_mode", "screen") != "steam":
            self._hotkey_manager.start()

        self.window = None  # pywebview window, set after creation
        self._startup_watch_ready = False
        self._startup_watch_thread: threading.Thread | None = None

        # fix_a17: 트레이 매니저 (lazy start - 사용자가 트레이 옵션 켰거나 X 눌렀을 때 시작)
        self._tray: "TrayManager | None" = None
        self._minimize_to_tray = bool(self.config.get("minimize_to_tray_enabled", False))

        # fix_a19: OCR 기록 관리자 (메모리 20개)
        # fix_a25: 영속 저장 + 세션 락 추가, error_logger 전달
        self.history = HistoryManager(error_logger=self.error_logger)

        # fix_a15.1: 무거운 첫 호출들(mss, template strip)을 백그라운드에서 미리 워밍업
        # 사용자가 윈도우 뜨자마자 클릭해도 첫 작업이 빠르게 처리되도록
        # 실패해도 무시 (lazy 로딩이 fallback)
        def _warmup() -> None:
            try:
                # mss 첫 호출 (~1초) 미리 처리
                with mss.mss() as sct:
                    _ = len(sct.monitors)
                self._monitor_list_cache_time = 0  # 캐시 리셋해서 첫 get_config가 다시 한 번 정확히 측정
            except Exception:
                pass
            try:
                # 현재 모드의 template strip 미리 로드
                mode = self.config.get("game_mode", "squad4")
                if mode != "auto":
                    self.template_matcher.ensure_loaded(mode)
            except Exception:
                pass

        threading.Thread(target=_warmup, name="WebApiWarmup", daemon=True).start()

        # fix_a15.2: 환경 진단 정보 로깅 - 다른 PC에서 문제 발생 시 events.log 한 번 보고 원격 디버그 가능하게
        try:
            self.event_logger.info("=" * 60)
            self.event_logger.info("앱 시작 (webview)")
            self.event_logger.info("=" * 60)

            # Python / OS 버전
            try:
                import platform
                self.event_logger.info(
                    "Python: %s, Platform: %s %s",
                    sys.version.split()[0],
                    platform.system(),
                    platform.release(),
                )
                if os.name == "nt":
                    self.event_logger.info("Windows 빌드: %s", platform.version())
            except Exception:
                pass

            # 라이브러리 버전 (호환성 디버그)
            try:
                lib_versions = []
                try:
                    lib_versions.append(f"mss={mss.__version__}")
                except Exception:
                    pass
                try:
                    lib_versions.append(f"cv2={cv2.__version__}")
                except Exception:
                    pass
                try:
                    import webview
                    lib_versions.append(f"webview={webview.__version__}")
                except Exception:
                    pass
                if lib_versions:
                    self.event_logger.info("라이브러리: %s", ", ".join(lib_versions))
            except Exception:
                pass

            # 실행 환경
            self.event_logger.info("실행: frozen=%s", getattr(sys, "frozen", False))
            self.event_logger.info("실행 경로: %s", str(Path(sys.executable).resolve() if getattr(sys, "frozen", False) else Path(__file__).resolve()))
            self.event_logger.info("작업 폴더: %s", str(RUNTIME_BASE_DIR))

            # 모니터 정보 (DPI 가상화 검출용 핵심)
            try:
                with mss.mss() as sct:
                    for i, mon in enumerate(sct.monitors):
                        self.event_logger.info("모니터[%d]: %s", i, mon)
            except Exception as exc:
                self.event_logger.warning("모니터 정보 조회 실패: %s", exc)

            # DPI awareness 상태 (Windows만)
            if os.name == "nt":
                try:
                    awareness = ctypes.c_int()
                    ctypes.windll.shcore.GetProcessDpiAwareness(0, ctypes.byref(awareness))
                    awareness_names = {0: "unaware", 1: "system", 2: "per_monitor"}
                    self.event_logger.info("DPI awareness: %s(%d)",
                                           awareness_names.get(awareness.value, "unknown"),
                                           awareness.value)
                except Exception:
                    pass

            # 사용자 설정 요약 (중요한 것만, 비밀 정보 제외)
            try:
                cfg = self.config
                self.event_logger.info(
                    "설정: game_mode=%s, capture_mode=%s, hotkey=%s, chat=%s",
                    cfg.get("game_mode", "?"),
                    cfg.get("capture_mode", "?"),
                    "on" if cfg.get("hotkey", {}).get("enabled", True) else "off",
                    "on" if cfg.get("chat", {}).get("enabled", True) else "off",
                )
                self.event_logger.info(
                    "설정: read_target=%s, multiplier=%s, save_screenshot=%s, save_debug=%s",
                    cfg.get("read_target", "damage"),
                    cfg.get("chat_multiplier", 1),
                    cfg.get("save_screenshot", True),
                    cfg.get("save_debug_images", False),
                )
                steam_dir = cfg.get("steam_screenshot_dir", "")
                if steam_dir:
                    self.event_logger.info("스팀 폴더: %s", steam_dir)
            except Exception:
                pass

            self.event_logger.info("=" * 60)
        except Exception:
            pass

    def _queue_calc_trigger(self, reason: str = "manual") -> None:
        with self._pending_hotkey_lock:
            self._pending_hotkey_count += 1
        self.event_logger.info("계산 트리거 추가: %s", reason)

    def _on_hotkey(self) -> None:
        self._queue_calc_trigger("hotkey")

    def _on_hotkey_send(self) -> None:
        """fix_a19: F9 송출 핫키 콜백.
        manual_send와 동일한 동작 (카드 비었으면 무시).
        예외는 HotkeyManager에서 격리되어 메시지 루프 보호됨.
        """
        # 빈 카드 가드 - 곡천님 정책: 사용자에겐 UI 상태 메시지로 알림
        # from_hotkey=True 로 호출하면 _last_send_status 박혀서 poll_state로 UI에 표시됨
        self.manual_send(from_hotkey=True)

    def _consume_queued_steam_screenshot(self) -> Path | None:
        latest: Path | None = None
        while True:
            try:
                latest = self._steam_watch_queue.get_nowait()
            except queue.Empty:
                break
        return latest

    def schedule_startup_watch(self, delay_seconds: float = 1.2) -> None:
        if self._startup_watch_ready:
            return
        self._startup_watch_ready = True

        def _worker() -> None:
            try:
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                self._sync_steam_watch_state(reason="startup_delayed")
            except Exception:
                self.error_logger.exception("지연 시작 스팀 자동 감지 초기화 실패")

        self._startup_watch_thread = threading.Thread(
            target=_worker,
            name="StartupSteamWatchDelay",
            daemon=True,
        )
        self._startup_watch_thread.start()


    def _sync_steam_watch_state(self, reason: str = "config") -> None:
        mode = self.config.get("capture_mode", "screen")
        steam_dir = self.config.get("steam_screenshot_dir", "").strip()
        if mode == "steam" and steam_dir:
            self._start_steam_watch(steam_dir, reason=reason)
        else:
            self._stop_steam_watch(reason=reason)

    def _start_steam_watch(self, steam_dir: str, reason: str = "config") -> None:
        folder = Path(steam_dir)
        if not folder.exists():
            self.event_logger.info("스팀 자동 감지 대기: 폴더 없음 (%s)", steam_dir)
            self._stop_steam_watch(reason=reason)
            return
        resolved_dir = str(folder.resolve())
        if self._steam_watch_thread and self._steam_watch_thread.is_alive() and self._steam_watch_dir == resolved_dir:
            return
        self._stop_steam_watch(reason=reason)
        self._steam_watch_snapshot, _, _ = snapshot_steam_files(folder)
        while True:
            try:
                self._steam_watch_queue.get_nowait()
            except queue.Empty:
                break
        self._steam_watch_stop.clear()
        self._steam_watch_dir = resolved_dir
        self._steam_watch_thread = threading.Thread(
            target=self._steam_watch_loop,
            args=(folder,),
            name="SteamScreenshotWatcher",
            daemon=True,
        )
        self._steam_watch_thread.start()
        self.event_logger.info("스팀 자동 감지 시작: %s (기준파일=%d, reason=%s)", resolved_dir, len(self._steam_watch_snapshot), reason)

    def _stop_steam_watch(self, reason: str = "config") -> None:
        self._steam_watch_stop.set()
        if self._steam_watch_thread and self._steam_watch_thread.is_alive():
            self._steam_watch_thread.join(timeout=1.5)
        self._steam_watch_thread = None
        self._steam_watch_dir = ""
        self._steam_watch_snapshot = {}
        while True:
            try:
                self._steam_watch_queue.get_nowait()
            except queue.Empty:
                break
        self._steam_watch_stop.clear()
        self.event_logger.info("스팀 자동 감지 중지: %s", reason)

    def _steam_watch_loop(self, folder: Path) -> None:
        try:
            current = dict(self._steam_watch_snapshot)
            while not self._steam_watch_stop.wait(0.5):
                try:
                    snapshot, _, _ = snapshot_steam_files(folder)
                except Exception:
                    self.error_logger.exception("스팀 자동 감지 스냅샷 실패")
                    continue
                new_paths: list[Path] = []
                for path, mtime in snapshot.items():
                    prev = current.get(path.resolve())
                    if prev is None or mtime > prev + 0.0001:
                        new_paths.append(path)
                current = snapshot
                self._steam_watch_snapshot = dict(snapshot)
                if not new_paths:
                    continue
                new_paths.sort(key=lambda p: p.stat().st_mtime)
                latest = new_paths[-1]
                self._steam_watch_queue.put(latest)
                self.event_logger.info("스팀 새 스크린샷 감지: %s", latest)
                self._queue_calc_trigger("steam_watch")
        except Exception:
            self.error_logger.exception("스팀 자동 감지 루프 오류")

    def minimize_window(self) -> None:
        if self.window:
            self.window.minimize()

    def close_window(self) -> None:
        # fix_a17: 트레이 최소화 옵션 ON이면 종료 대신 창 숨김 + 트레이 활성화
        if self._minimize_to_tray and self.window is not None:
            try:
                # 트레이 아이콘이 아직 안 뜬 상태면 첫 진입 때 한 번만 활성화
                if self._tray is None:
                    icon_path = RESOURCE_BASE_DIR / "app.ico"
                    self._tray = TrayManager(
                        icon_path=icon_path,
                        on_show=self._tray_show_window,
                        on_quit=self._tray_quit_app,
                        event_logger=self.event_logger,
                        error_logger=self.error_logger,
                    )
                    self._tray.start()
                # pystray 자체가 사용 불가능한 환경이면 안전하게 종료 폴백
                if self._tray is None or not self._tray.available:
                    self.event_logger.warning(
                        "트레이 사용 불가능한 환경 → 종료 폴백"
                    )
                    self.cleanup()
                    self.window.destroy()
                    return
                # 정상 경로: 창만 숨김, 프로세스/리소스는 그대로 유지
                self.window.hide()
                return
            except Exception:
                self.error_logger.exception("트레이 최소화 실패 → 종료 폴백")
                # 폴백으로 일반 종료
        self.cleanup()
        if self.window:
            self.window.destroy()

    # fix_a17: 트레이에서 호출되는 콜백들 (트레이 스레드에서 실행되므로 thread-safe하게)
    def _tray_show_window(self) -> None:
        try:
            if self.window is not None:
                self.window.show()
                # 일부 환경에서 show 후 포커스가 안 잡히는 경우 대비
                try:
                    self.window.restore()
                except Exception:
                    pass
        except Exception:
            self.error_logger.exception("트레이에서 창 복원 실패")

    def _tray_quit_app(self) -> None:
        try:
            self.cleanup()
        except Exception:
            self.error_logger.exception("트레이 종료 시 cleanup 실패")
        try:
            if self.window is not None:
                self.window.destroy()
        except Exception:
            self.error_logger.exception("트레이 종료 시 destroy 실패")

    def set_minimize_to_tray(self, enabled: bool) -> None:
        """fix_a17: 설정에서 체크박스 onchange로 호출됨."""
        self._minimize_to_tray = bool(enabled)
        self.config["minimize_to_tray_enabled"] = bool(enabled)
        try:
            save_config(self.config)
        except Exception:
            self.error_logger.exception("minimize_to_tray 저장 실패")

    def cleanup(self) -> None:
        try:
            self._stop_steam_watch(reason="cleanup")
        except Exception:
            pass
        try:
            self._hotkey_manager.stop()
        except Exception:
            pass
        # fix_a17: 트레이 아이콘 정리
        try:
            if self._tray is not None:
                self._tray.stop()
                self._tray = None
        except Exception:
            pass
        # fix_a19: 백그라운드 토큰 갱신 워커 정리
        try:
            self.chat_client.stop_background_refresh()
        except Exception:
            pass
        try:
            if self._lock_file.exists():
                self._lock_file.unlink()
        except Exception:
            pass

    # ── JS에서 호출하는 메서드들 ────────────────────────────

    def get_config(self) -> dict:
        chat_cfg = self.config.get("chat", {})
        self.chat_client.load_tokens()
        monitors = self._get_monitor_list()
        return {
            "monitors": monitors,
            "monitor_index": self._monitor_var,
            "hotkey_enabled": self._hotkey_enabled,
            "hotkey_display_name": str(self.config.get("hotkey", {}).get("display_name", "스팀 자동 감지")),
            "chat_enabled": self._chat_enabled,
            "chat_multiplier": self._multiplier,
            "client_id": chat_cfg.get("client_id", ""),
            "client_secret": chat_cfg.get("client_secret", ""),
            "redirect_uri": chat_cfg.get("redirect_uri", "http://127.0.0.1:8785/chzzk/callback"),
            "chat_status": self._get_chat_status(),
            "chat_connected": self.chat_client.has_valid_access_token(),
            "chicken_bonus_enabled": bool(self.config.get("chicken_bonus_enabled", True)),
            "chicken_bonus_damage": int(self.config.get("chicken_bonus_damage", 1000)),
            "capture_mode": self.config.get("capture_mode", "screen"),
            "steam_screenshot_dir": self.config.get("steam_screenshot_dir", ""),
            "steam_screenshot_delay": float(self.config.get("steam_screenshot_delay", 0.8)),
            "steam_screenshot_hotkey": str(self.config.get("steam_screenshot_hotkey", "f12")),
            "chat_message_format": self.config.get("chat_message_format", "!미션 !딜 {딜}"),
            "kill_bonus_enabled": bool(self.config.get("kill_bonus_enabled", True)),
            "kill_bonus_count": int(self.config.get("kill_bonus_count", 10)),
            "save_screenshot": bool(self.config.get("save_screenshot", True)),
            # fix_a26: 버전 표시·최초 실행 안내 카드 판정
            "app_label": APP_LABEL,
            "app_version": APP_VERSION,
            "manual_seen": bool(self.config.get("manual_seen", False)),
            "manual_available": self._manual_path() is not None,
            "update_repo": updater.get_repo(self.config),
            "save_debug_images": bool(self.config.get("save_debug_images", False)),
            "client_id": self.config.get("chat", {}).get("client_id", ""),
            "client_secret": self.config.get("chat", {}).get("client_secret", ""),
            "read_target": self.config.get("read_target", "damage"),
            "game_mode": self.config.get("game_mode", "squad4"),
            # fix_a17
            "minimize_to_tray_enabled": bool(self.config.get("minimize_to_tray_enabled", False)),
            # fix_a20: 카드 편집 토글 (기본 false, hotfix 유지). config.json에서 true로 켜면 더블클릭 편집 활성화
            "card_edit_enabled": bool(self.config.get("card_edit_enabled", False)),
        }

    def _get_chat_status(self) -> str:
        if self._connecting:
            return "치지직 로그인 진행 중... 브라우저를 확인하세요."
        if self.chat_client.has_valid_access_token():
            return self.chat_client.get_status_text()
        if self._last_connect_message:
            return self._last_connect_message
        return self.chat_client.get_status_text()

    def poll_state(self) -> dict:
        trigger_calc = False
        with self._pending_hotkey_lock:
            if self._pending_hotkey_count > 0:
                self._pending_hotkey_count -= 1
                trigger_calc = True
        # fix_a15.3: 핫키 등록 실패 상태 노출 (UI에서 사용자 안내)
        hotkey_failed = bool(getattr(self._hotkey_manager, "registration_failed", False))
        hotkey_error = str(getattr(self._hotkey_manager, "registration_error", "")) if hotkey_failed else ""
        # fix_a19: 송출 상태 (F9 핫키로 송출했을 때 UI에 표시용)
        # 한 번 읽으면 비움 (반복 표시 방지)
        send_status = getattr(self, "_last_send_status", "")
        if send_status:
            self._last_send_status = ""
        return {
            "chat_status": self._get_chat_status(),
            "chat_connected": self.chat_client.has_valid_access_token(),
            "trigger_calc": trigger_calc,
            "hotkey_failed": hotkey_failed,
            "hotkey_error": hotkey_error,
            "send_status": send_status,
            # fix_a26: 백그라운드 업데이트 확인 결과 (없으면 None) + 다운로드 진행 상태
            "update": getattr(self, "_update_info", None),
            "update_progress": getattr(self, "_update_progress", None),
        }

    def _get_monitor_list(self) -> list[str]:
        # fix_a15.1: mss 첫 호출이 느릴 수 있어 결과 캐싱 (1분간 유효)
        # 시작 직후 get_config 호출 시 mss 초기화로 1~2초 블로킹되는 문제 완화
        now = time.time()
        cache_age = now - getattr(self, '_monitor_list_cache_time', 0)
        cached = getattr(self, '_monitor_list_cache', None)
        if cached is not None and cache_age < 60:
            return cached
        try:
            with mss.mss() as sct:
                count = max(len(sct.monitors) - 1, 1)
            result = [str(i) for i in range(1, count + 1)]
        except Exception:
            result = ["1"]
        self._monitor_list_cache = result
        self._monitor_list_cache_time = now
        return result

    def refresh_monitors(self) -> list[str]:
        return self._get_monitor_list()

    def set_monitor(self, index: str) -> None:
        self._monitor_var = str(index)

    def set_hotkey(self, enabled: bool) -> None:
        self._hotkey_enabled = bool(enabled)
        if enabled:
            self._hotkey_manager.start()
        else:
            self._hotkey_manager.stop()

    def set_chat_enabled(self, enabled: bool) -> None:
        self._chat_enabled = bool(enabled)

    def set_multiplier(self, value: str) -> None:
        try:
            self._multiplier = max(1, int(str(value).replace("x", "")))
        except Exception:
            self._multiplier = 1

    def set_chicken_bonus_enabled(self, enabled: bool) -> None:
        self.config["chicken_bonus_enabled"] = bool(enabled)
        # fix_a22: 곡천님 킬 보너스 미적용 케이스 추적용. 딜 측도 같은 라인 박아 비교 가능.
        try:
            self.event_logger.info("[a22] set_chicken_bonus_enabled: %s", bool(enabled))
        except Exception:
            pass
        # fix_a22: save_config 누락 - 앱 재시작 시 설정 풀리던 문제 수정 (딜/킬 동일하게 이슈 있었음)
        try:
            save_config(self.config)
        except Exception:
            self.error_logger.exception("chicken_bonus_enabled 저장 실패")

    def set_chicken_bonus_damage(self, value: str) -> None:
        try:
            self.config["chicken_bonus_damage"] = max(0, int(str(value)))
            self.event_logger.info("[a22] set_chicken_bonus_damage: %s", self.config["chicken_bonus_damage"])
            save_config(self.config)
        except Exception:
            pass

    def set_chat_message_format(self, fmt: str) -> None:
        self.config["chat_message_format"] = str(fmt)

    def set_read_target(self, target: str) -> None:
        if target in ("damage", "kill"):
            self.config["read_target"] = target

    def set_game_mode(self, mode: str) -> None:
        # fix_a18: squad3는 미구현이므로 들어와도 squad4로 fallback
        if mode == "squad3":
            self.event_logger.warning("squad3는 미구현 - squad4로 자동 전환")
            mode = "squad4"
        if mode in ("auto", "solo", "duo", "squad4"):
            self.config["game_mode"] = mode

    def set_kill_bonus_enabled(self, enabled: bool) -> None:
        self.config["kill_bonus_enabled"] = bool(enabled)
        # fix_a22: 곡천님 킬 보너스 미적용 케이스 추적용
        try:
            self.event_logger.info("[a22] set_kill_bonus_enabled: %s", bool(enabled))
        except Exception:
            pass
        # fix_a22: save_config 누락 - 앱 재시작 시 설정 풀리던 문제 수정
        try:
            save_config(self.config)
        except Exception:
            self.error_logger.exception("kill_bonus_enabled 저장 실패")

    def set_kill_bonus_count(self, value: str) -> None:
        try:
            self.config["kill_bonus_count"] = max(0, int(str(value)))
            self.event_logger.info("[a22] set_kill_bonus_count: %s", self.config["kill_bonus_count"])
            save_config(self.config)
        except Exception:
            pass

    def set_save_screenshot(self, enabled: bool) -> None:
        self.config["save_screenshot"] = bool(enabled)

    def set_save_debug_images(self, enabled: bool) -> None:
        self.config["save_debug_images"] = bool(enabled)

    def set_client_credentials(self, client_id: str, client_secret: str) -> dict:
        """Client ID / Secret을 config에 저장하고 즉시 반영."""
        chat_cfg = self.config.setdefault("chat", {})
        if client_id.strip():
            chat_cfg["client_id"] = client_id.strip()
        if client_secret.strip():
            chat_cfg["client_secret"] = client_secret.strip()
        # redirect_uri 기본값 보장
        if not chat_cfg.get("redirect_uri", "").strip():
            chat_cfg["redirect_uri"] = "http://127.0.0.1:8785/chzzk/callback"
        save_config(self.config)
        self.event_logger.info("치지직 인증 정보 업데이트: client_id=%s", chat_cfg.get("client_id",""))
        return {"ok": True}

    def set_capture_mode(self, mode: str) -> None:
        if mode in ("screen", "steam"):
            self.config["capture_mode"] = mode
            self.event_logger.info("캡처 모드 변경: %s", mode)
            # 스팀 모드: 파일 감지 자동 → 핫키 불필요 / 화면 모드: F8 핫키 활성
            if mode == "steam":
                self._hotkey_manager.stop()
            elif self._hotkey_enabled:
                self._hotkey_manager.start()
            self._sync_steam_watch_state(reason="set_capture_mode")

    def set_steam_screenshot_dir(self, path: str) -> None:
        self.config["steam_screenshot_dir"] = path.strip()
        self.event_logger.info("스팀 스크린샷 폴더 변경: %s", self.config["steam_screenshot_dir"] or "(비어 있음)")
        self._sync_steam_watch_state(reason="set_steam_screenshot_dir")

    def set_steam_screenshot_delay(self, value: str) -> None:
        try:
            self.config["steam_screenshot_delay"] = max(0.3, min(3.0, float(value)))
            self.event_logger.info("스팀 스크린샷 딜레이 변경: %.2f초", float(self.config["steam_screenshot_delay"]))
        except Exception:
            pass

    def set_steam_screenshot_hotkey(self, value: str) -> None:
        key = str(value or "").strip().lower()
        if key in STEAM_HOTKEY_MAP:
            self.config["steam_screenshot_hotkey"] = key
            self.event_logger.info("스팀 스크린샷 핫키 변경: %s", key)
        else:
            self.error_logger.error("지원하지 않는 스팀 스크린샷 핫키: %s", value)

    def browse_steam_dir(self) -> str:
        """폴더 선택 다이얼로그 (Windows IFileOpenDialog 사용).
        BrowseForFolder(구형 COM)는 pywebview/WebView2와 충돌하여 화면 블랙/프리징 유발.
        IFileOpenDialog를 별도 스레드에서 실행해 충돌 방지.

        fix_a9: timeout을 60초 → 600초(10분)로 증가.
        Windows 폴더 다이얼로그는 모달이라 사용자가 응답할 때까지 기다려야 하는데,
        60초는 너무 짧아 사용자가 폴더 탐색 중 강제 종료될 위험이 있었음.
        """
        if os.name != "nt":
            return ""
        result_box: list[str] = []
        def _run():
            try:
                import subprocess
                ps_cmd = (
                    # fix_a15.2: 한글 폴더 경로 안전 - PowerShell이 utf-8로 출력하도록 강제
                    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;"
                    "Add-Type -AssemblyName System.Windows.Forms;"
                    "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
                    "$d.Description = '스팀 스크린샷 폴더 선택';"
                    "$d.ShowNewFolderButton = $false;"
                    "$null = $d.ShowDialog();"
                    "Write-Output $d.SelectedPath"
                )
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
                    capture_output=True, text=True, timeout=600,
                    creationflags=0x08000000,  # CREATE_NO_WINDOW
                    encoding="utf-8", errors="replace"  # fix_a15.2: 한글 폴더 경로 안전 처리
                )
                path = r.stdout.strip()
                if path:
                    result_box.append(path)
            except Exception as e:
                self.error_logger.warning("browse_steam_dir 실패: %s", e)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=610)
        path = result_box[0] if result_box else ""
        if path and Path(path).exists():
            self.config["steam_screenshot_dir"] = path
            self._sync_steam_watch_state(reason="browse_steam_dir")
        return path

    def calculate(self) -> dict:
        """JS에서 호출 - pywebview 작업 스레드에서 직접 계산."""
        if not self._calc_lock.acquire(blocking=False):
            return {"error": "이미 계산 중입니다."}
        try:
            return self._calculate_impl()
        except Exception as e:
            return {"error": str(e)}
        finally:
            self._calc_lock.release()

    def _calculate_impl(self) -> dict:
        now = time.time()
        cooldown = float(self.config.get("cooldown_seconds", 3))
        if now - self.last_run_time < cooldown:
            remaining = cooldown - (now - self.last_run_time)
            return {"error": f"쿨타임 중... {remaining:.1f}초 남음"}
        try:
            frame_bgr = self._grab_frame()
            if frame_bgr is None:
                return {"error": self._last_capture_error or "화면 캡처 실패"}

            # fix_a15.2: 첫 캡처에서 frame 크기 진단 로깅 (DPI 가상화 검출용)
            # 정상이면 (1920, 1080) 또는 사용자 모니터 해상도 그대로
            # DPI 가상화 작동하면 모니터 해상도보다 작게 (예: 1920 모니터에 125% → 1536)
            if not getattr(self, "_first_capture_logged", False):
                h0, w0 = frame_bgr.shape[:2]
                mode_name = self._last_capture_mode_used or "unknown"
                self.event_logger.info(
                    "첫 캡처 진단: mode=%s frame=%dx%d (DPI 가상화 발생 시 모니터보다 작게 잡힘)",
                    mode_name, w0, h0
                )
                self._first_capture_logged = True

            # 해상도 정규화: FHD(1920x1080) 기준으로 통일
            # QHD/4K 등 어떤 해상도든 내부 처리는 FHD 기준으로 동작
            h, w = frame_bgr.shape[:2]
            if w != 1920 or h != 1080:
                frame_bgr = cv2.resize(frame_bgr, (1920, 1080), interpolation=cv2.INTER_LINEAR)
                self.event_logger.info("해상도 정규화: %dx%d → 1920x1080", w, h)

            # 치킨(#1) 감지: 좌상단 노란색 숫자 폭으로 판별
            is_chicken = self._detect_chicken(frame_bgr)
            self.event_logger.info("치킨 감지: %s", "✅ 치킨!" if is_chicken else "❌ 비치킨")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_debug_images = bool(self.config.get("save_debug_images", False))
            save_full = bool(self.config.get("save_full_capture", False))

            debug_dir = DEBUG_DIR / timestamp
            if save_debug_images:
                debug_dir.mkdir(parents=True, exist_ok=True)

            if save_full:
                full_path = SCREENSHOTS_DIR / f"{timestamp}_full.png"
                save_image_unicode_safe(full_path, frame_bgr)

            game_mode_setting = self.config.get("game_mode", "squad4")
            # fix_a15: 결과창 진입 게이트 - 결과창이 아닌 캡처(다음 페이지 등)에서 OCR 차단
            # detect_game_mode와 같은 측정으로 best_score 계산, 임계값(0.085) 이하면 스킵
            # auto/manual 모두 적용 (수동 squad4 고정 사용자도 잘못된 캡처 방지)
            modes_cfg = self.config.get("modes", {})
            try:
                _detected_mode, _screen_score = score_result_screen(frame_bgr, modes_cfg)
            except Exception:
                _detected_mode, _screen_score = (game_mode_setting if game_mode_setting != "auto" else "squad4"), 1.0
            # 게이트 활성/임계값은 config로 제어 가능 (기본 활성, 임계값 0.085)
            gate_enabled = bool(self.config.get("result_screen_gate_enabled", True))
            gate_threshold = float(self.config.get("result_screen_min_score", RESULT_SCREEN_MIN_SCORE))
            if gate_enabled and _screen_score < gate_threshold:
                self.event_logger.info(
                    "결과창 게이트 차단: best_score=%.4f < %.4f (감지 모드=%s)",
                    _screen_score, gate_threshold, _detected_mode
                )
                return {"error": f"결과창이 아닌 화면으로 판단됨 (점수 {_screen_score:.3f} < {gate_threshold:.3f})"}

            if game_mode_setting == "auto":
                mode = _detected_mode
                self.event_logger.info("인원 자동 감지: %s (score=%.4f)", mode, _screen_score)
            else:
                mode = game_mode_setting

            # fix_a18: 3인 스쿼드 미구현 게이트
            # auto 감지 결과든 수동 설정이든 squad3는 OCR 차단 (잘못된 결과 송출 방지).
            # squad3 OCR 코드/ROI는 그대로 보존되어 있으므로, 본격 작업 시 이 가드만
            # 풀면 됨. 곡천님 PC는 squad4 강제 사용이라 직접 영향 없지만,
            # auto 감지가 squad3로 잘못 판정되는 케이스(47/49/50번 등) 보호.
            if mode == "squad3":
                self.event_logger.info(
                    "squad3 차단: 3인 스쿼드는 미구현 (감지/설정 모드=%s, score=%.4f)",
                    _detected_mode, _screen_score
                )
                return {"error": "3인 스쿼드 모드는 아직 미구현입니다. 솔로/듀오/4인 스쿼드만 사용 가능합니다."}

            mode_cfg = self.config.get("modes", {}).get(mode)
            if not mode_cfg:
                raise RuntimeError(f"{mode} 모드 설정 없음")

            read_target = self.config.get("read_target", "damage")  # 'damage' | 'kill' | 'both'
            max_damage = int(self.config["sanity"]["max_damage_per_player"])
            max_kill = int(self.config.get("sanity", {}).get("max_kill_per_player", 30))
            details: list[RoiResult] = []
            total_damage = 0
            total_kill = 0

            for idx, player in enumerate(mode_cfg.get("players", []), start=1):
                damage = 0
                kill = 0

                if read_target in ("damage", "both"):
                    damage, _ = self._extract_number(
                        frame_bgr, player["damage"], debug_dir,
                        f"{timestamp}_{mode}_p{idx}_damage", "damage", mode
                    )
                    if damage > max_damage:
                        damage = 0
                    total_damage += damage

                if read_target in ("kill", "both"):
                    if "kill" in player:
                        kill, _ = self._extract_number(
                            frame_bgr, player["kill"], debug_dir,
                            f"{timestamp}_{mode}_p{idx}_kill", "kill", mode
                        )
                        if kill > max_kill:
                            kill = 0
                        total_kill += kill

                details.append(RoiResult(idx, kill, damage))

            chicken_bonus = 0
            kill_bonus = 0
            if is_chicken and self.config.get("chicken_bonus_enabled", False):
                chicken_bonus = int(self.config.get("chicken_bonus_damage", 0))
            if is_chicken and self.config.get("kill_bonus_enabled", False):
                kill_bonus = int(self.config.get("kill_bonus_count", 0))

            # fix_a22: 보너스 계산 진단 - 곡천님 킬 보너스 미적용 케이스 추적용.
            # 입력값(config 상태 + is_chicken)과 결과값(chicken_bonus, kill_bonus)을 동시에 박음.
            # 하나라도 어긋나면 events.log만 봐도 즉시 위치 식별 가능.
            try:
                self.event_logger.info(
                    "[a22] 보너스 진단(_calculate_impl): is_chicken=%s "
                    "chicken_enabled=%s chicken_damage_cfg=%s → chicken_bonus=%d | "
                    "kill_enabled=%s kill_count_cfg=%s → kill_bonus=%d | "
                    "read_target=%s",
                    is_chicken,
                    self.config.get("chicken_bonus_enabled", False),
                    self.config.get("chicken_bonus_damage", 0),
                    chicken_bonus,
                    self.config.get("kill_bonus_enabled", False),
                    self.config.get("kill_bonus_count", 0),
                    kill_bonus,
                    self.config.get("read_target", "damage"),
                )
            except Exception:
                pass

            final_damage = (total_damage + chicken_bonus) * self._multiplier
            final_kill = (total_kill + kill_bonus) * self._multiplier
            msg_fmt = self.config.get("chat_message_format", "!미션 {합계}")
            read_target = self.config.get("read_target", "damage")
            summary = final_kill if read_target == "kill" else final_damage
            result_text = (msg_fmt
                .replace("{합계}", str(summary))
                .replace("{딜합계}", str(final_damage))
                .replace("{킬합계}", str(final_kill))
                .replace("{딜}", str(final_damage))
                .replace("{킬}", str(final_kill)))
            OVERLAY_PATH.write_text(result_text, encoding="utf-8")
            OUTBOX_PATH.write_text(result_text, encoding="utf-8")

            self.last_run_time = time.time()
            self._last_damage = total_damage
            self._last_kill = total_kill
            self._last_details = details
            self._last_is_chicken = is_chicken

            # fix_a9: 디버그 폴더 자동 정리 제거 - 사용자가 직접 관리
            # (UI의 "debug 폴더 비우기" 버튼으로 수동 삭제 가능)

            self._last_result_text = result_text
            self._pending_auto_send_message = result_text if self._chat_enabled else ""

            self.event_logger.info("계산 완료: 딜합=%d 킬합=%d 치킨보너스=%d 배수=%d 최종=%d | %s",
                                   total_damage, total_kill, chicken_bonus, self._multiplier, final_damage,
                                   " / ".join(f"P{d.player_index} {d.damage}딜 {d.kill}킬" for d in details))
            self.event_logger.info("캡처 사용 요약: mode=%s source=%s saved=%s auto_send=%s",
                                   self._last_capture_mode_used or "unknown",
                                   self._last_capture_source or "(없음)",
                                   self._last_capture_saved or "(없음)",
                                   "대기" if self._pending_auto_send_message else "안 함")

            # fix_a19: 기록에 추가 (송출 여부 무관, OCR 시점 추가)
            history_id = 0
            try:
                players_list = [{"kill": d.kill, "damage": d.damage} for d in details]
                history_id = self.history.add(
                    mode=mode,
                    players=players_list,
                    chicken_bonus=chicken_bonus,
                    kill_bonus=kill_bonus,
                    multiplier=self._multiplier,
                    total_damage=total_damage,
                    total_kill=total_kill,
                    final=final_damage,
                    final_kill=final_kill,
                    is_chicken=is_chicken,
                    read_target=read_target,
                    message=result_text,
                )
                self._last_history_id = history_id
            except Exception:
                self.error_logger.exception("기록 추가 실패")

            return {
                "p_damage": [d.damage for d in details],
                "p_kill": [d.kill for d in details],
                "total_damage": total_damage,
                "total_kill": total_kill,
                "final_kill": final_kill,
                "read_target": read_target,
                "is_chicken": is_chicken,
                "chicken_bonus": chicken_bonus,
                "kill_bonus": kill_bonus,
                "multiplier": self._multiplier,
                "final": final_damage,
                "capture_mode_used": self._last_capture_mode_used,
                "capture_source": self._last_capture_source,
                "capture_saved": self._last_capture_saved,
                "auto_send_ready": bool(self._pending_auto_send_message),
                "history_id": history_id,
            }

        except Exception as exc:
            self.error_logger.exception("계산 실패")
            return {"error": str(exc)}

    def _record_capture_snapshot(self, frame_bgr: np.ndarray, mode_used: str, source_desc: str) -> str:
        # 설정에서 캡처 이미지 저장 OFF이면 저장 안 함
        if not bool(self.config.get("save_screenshot", True)):
            self._last_capture_saved = ""
            return ""
        try:
            SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            out_path = SCREENSHOTS_DIR / f"{timestamp}_{mode_used}.png"
            save_image_unicode_safe(out_path, frame_bgr)
            self._last_capture_saved = str(out_path)
            self.event_logger.info("스크린샷 사본 저장: %s (source=%s)", out_path.name, source_desc)
            try:
                saved_files = sorted(SCREENSHOTS_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
                for old in saved_files[20:]:
                    old.unlink(missing_ok=True)
            except Exception:
                pass
            return str(out_path)
        except Exception:
            self.error_logger.exception("스크린샷 사본 저장 실패")
            self._last_capture_saved = ""
            return ""

    def _grab_frame(self) -> np.ndarray | None:
        """캡처 모드에 따라 프레임 취득."""
        self._last_capture_mode_used = ""
        self._last_capture_source = ""
        self._last_capture_saved = ""
        self._last_capture_error = ""

        mode = self.config.get("capture_mode", "screen")
        steam_dir = self.config.get("steam_screenshot_dir", "").strip()
        self.event_logger.info("캡처 시작: 설정 mode=%s steam_dir=%s", mode, steam_dir or "(비어 있음)")

        if mode == "steam":
            if not steam_dir:
                self._last_capture_error = "스팀 스크린샷 폴더가 비어 있습니다."
                self.error_logger.error(self._last_capture_error)
                return None
            frame = self._grab_from_steam(steam_dir)
            if frame is None:
                return None
            self._last_capture_mode_used = "steam"
            self._record_capture_snapshot(frame, "steam", self._last_capture_source or steam_dir)
            return frame

        try:
            monitor_index = int(self._monitor_var)
            with mss.mss() as sct:
                monitors = sct.monitors
                if monitor_index >= len(monitors):
                    self._last_capture_error = f"모니터 {monitor_index} 없음"
                    self.error_logger.error(self._last_capture_error)
                    return None
                shot = np.array(sct.grab(monitors[monitor_index]))
            frame = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)
            self._last_capture_mode_used = "screen"
            self._last_capture_source = f"screen_monitor_{monitor_index}"
            self._record_capture_snapshot(frame, "screen", self._last_capture_source)
            self.event_logger.info("직접 화면 캡처 사용: monitor=%s", monitor_index)
            return frame
        except Exception:
            self._last_capture_error = "화면 캡처 실패"
            self.error_logger.exception(self._last_capture_error)
            return None

    def _grab_from_steam(self, steam_dir: str) -> np.ndarray | None:
        """자동 감지 대기열에 잡힌 최신 스팀 스크린샷을 우선 사용하고, 없으면 새 파일을 대기한다."""
        try:
            folder = Path(steam_dir)
            if not folder.exists():
                self._last_capture_error = f"스팀 스크린샷 폴더 없음: {steam_dir}"
                self.error_logger.error(self._last_capture_error)
                return None

            queued_target = self._consume_queued_steam_screenshot()
            if queued_target is not None and queued_target.exists():
                img = load_image_unicode_safe(queued_target, cv2.IMREAD_COLOR)
                if img is not None:
                    self._last_capture_source = str(queued_target)
                    self.event_logger.info("스팀 스크린샷 대기열 사용: %s", queued_target)
                    return img

            existing, ext_counts, latest_before = snapshot_steam_files(folder)
            self.event_logger.info(
                "스팀 스크린샷 수동 대기 시작: root=%s 기존이미지=%d 확장자=%s 최신=%s",
                steam_dir,
                len(existing),
                ext_counts or {},
                latest_before or "(없음)",
            )

            trigger_ts = time.time()
            base_delay = float(self.config.get("steam_screenshot_delay", 0.8))
            deadline = time.time() + max(base_delay + 4.0, 5.0)
            target: Path | None = None

            while time.time() < deadline:
                queued_target = self._consume_queued_steam_screenshot()
                if queued_target is not None and queued_target.exists():
                    target = queued_target
                    break
                candidates: list[Path] = []
                for p in scan_steam_image_files(folder):
                    try:
                        rp = p.resolve()
                        mtime = p.stat().st_mtime
                    except FileNotFoundError:
                        continue
                    except Exception:
                        continue
                    if mtime + 0.001 < trigger_ts:
                        continue
                    if rp not in existing or mtime > existing.get(rp, 0.0) + 0.0001:
                        candidates.append(p)
                if candidates:
                    target = max(candidates, key=lambda p: p.stat().st_mtime)
                    break
                time.sleep(0.25)

            after_snapshot, after_counts, latest_after = snapshot_steam_files(folder)

            if target is None:
                self._last_capture_error = "새 스팀 스크린샷을 찾지 못했습니다."
                self.error_logger.error(
                    "%s root=%s 기존=%d 현재=%d 확장자=%s 최신=%s",
                    self._last_capture_error,
                    steam_dir,
                    len(existing),
                    len(after_snapshot),
                    after_counts or {},
                    latest_after or latest_before or "(없음)",
                )
                return None

            stable_count = 0
            last_size = -1
            stable_deadline = time.time() + 3.0
            while time.time() < stable_deadline:
                try:
                    size = target.stat().st_size
                except FileNotFoundError:
                    time.sleep(0.1)
                    continue
                if size > 0 and size == last_size:
                    stable_count += 1
                else:
                    stable_count = 0
                    last_size = size
                if stable_count >= 2:
                    break
                time.sleep(0.1)

            img = load_image_unicode_safe(target, cv2.IMREAD_COLOR)
            if img is None:
                self._last_capture_error = f"스크린샷 읽기 실패: {target}"
                self.error_logger.error(self._last_capture_error)
                return None

            self._last_capture_source = str(target)
            try:
                age = max(0.0, time.time() - target.stat().st_mtime)
            except Exception:
                age = -1.0
            self.event_logger.info(
                "스팀 스크린샷 사용: file=%s age=%.2f초 root=%s 현재이미지=%d",
                target,
                age,
                steam_dir,
                len(after_snapshot),
            )
            return img

        except Exception:
            self._last_capture_error = "스팀 스크린샷 캡처 실패"
            self.error_logger.exception(self._last_capture_error)
            return None

    def _detect_chicken(self, frame_bgr: np.ndarray) -> bool:
        """좌상단 #1 노란색 숫자 폭으로 치킨(1위) 감지.
        1은 좁아서 col110~125 구간 픽셀이 거의 없고, 2~9는 넓어서 많음."""
        try:
            roi = frame_bgr[40:160, 40:300]
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, np.array([18, 120, 150]), np.array([38, 255, 255]))
            total_px = int(mask.sum() // 255)
            col_px = (mask > 0).sum(axis=0)
            tail_px = int(col_px[110:125].sum())
            # #1: total>500 이고 tail<100 (숫자가 좁아서 끝부분 픽셀 없음)
            is_one = (total_px > 500) and (tail_px < 100)
            self.event_logger.info("치킨감지 total=%d tail=%d → %s", total_px, tail_px, "#1 치킨" if is_one else "비치킨")
            return is_one
        except Exception:
            return False

    def _extract_number(self, frame_bgr, roi, debug_dir, debug_name, field_name, mode):
        height, width = frame_bgr.shape[:2]
        x1, y1, x2, y2 = relative_roi_to_absolute(roi, width, height)
        x1, y1, x2, y2 = clamp_roi(x1, y1, x2, y2, width, height)
        cropped = frame_bgr[y1:y2, x1:x2]
        if cropped.size == 0:
            return 0, (x1, y1, x2, y2)

        # 전체 프레임이 이미 1920x1080으로 정규화됐으므로 별도 리사이즈 불필요

        max_allowed = int(self.config["sanity"]["max_damage_per_player"])
        template_result = None
        try:
            template_result = self.template_matcher.recognize(cropped, field_name, debug_dir, debug_name, mode)
        except Exception:
            self.error_logger.exception("템플릿 인식 실패: %s", debug_name)

        squad4_rule_only = True  # damage_only + squad4 고정

        if template_result is not None:
            value = int(template_result["value"])
            if 0 <= value <= max_allowed:
                return value, (x1, y1, x2, y2)

        if squad4_rule_only:
            best = getattr(self.template_matcher, "last_best_result", None)
            if best is not None:
                forced = int(best.get("value", 0))
                # fix_a9: rule_only 분기에도 sanity 검사 적용 (이상 값 차단)
                if not (0 <= forced <= max_allowed):
                    self.event_logger.warning("RULE_ONLY %s → %s 이지만 sanity 위반 (max=%d) → 0 처리",
                                              debug_name, forced, max_allowed)
                    return 0, (x1, y1, x2, y2)
                self.event_logger.warning("RULE_ONLY %s → %s (final=%.3f)",
                                          debug_name, forced, best.get("final_score", 0))
                return forced, (x1, y1, x2, y2)
            self.event_logger.error("RULE_ONLY %s 후보 없음 → 0", debug_name)
            return 0, (x1, y1, x2, y2)

        return 0, (x1, y1, x2, y2)

    def _send_chat(self, message: str) -> None:
        try:
            self.chat_client.send_message(message)
            self.event_logger.info("치지직 전송 완료: %s", message)
        except Exception:
            self.error_logger.exception("치지직 전송 실패")

    def manual_send(self, from_hotkey: bool = False) -> dict:
        # fix_a15.1: config 이상치에 의한 메시지 조립 실패가 UI를 죽이지 않도록 보호
        # fix_a19: 결과 dict 반환 (UI에 상태 메시지 표시용)
        # fix_a19 (수정): from_hotkey=True 일 때만 _last_send_status 박음 (UI 버튼은 dict 직접 사용 → 중복 방지)
        # fix_a25: 세션 락 가드 - 재시작 직후 이전 기록 송출 마킹 방지
        try:
            if not self.history.is_session_dirty():
                if from_hotkey:
                    self._last_send_status = "전송할 결과가 없습니다"
                self.event_logger.info("[a25] 세션 락: F8 한 번도 안 눌린 상태 - 수동 전송 차단")
                return {"ok": False, "status": "전송할 결과가 없습니다"}
            if self._last_damage == 0 and self._last_kill == 0 and not self._last_details:
                if from_hotkey:
                    self._last_send_status = "전송할 결과가 없습니다"
                return {"ok": False, "status": "전송할 결과가 없습니다"}
            chicken_bonus = 0
            kill_bonus = 0
            if self.config.get("chicken_bonus_enabled", False) and getattr(self, '_last_is_chicken', False):
                chicken_bonus = int(self.config.get("chicken_bonus_damage", 0))
            if self.config.get("kill_bonus_enabled", False) and getattr(self, '_last_is_chicken', False):
                kill_bonus = int(self.config.get("kill_bonus_count", 0))
            final = (self._last_damage + chicken_bonus) * self._multiplier
            final_kill = (self._last_kill + kill_bonus) * self._multiplier
            msg_fmt = self.config.get("chat_message_format", "!미션 {합계}")
            _rt = self.config.get("read_target", "damage")
            _summary = final_kill if _rt == "kill" else final
            msg = (msg_fmt
                .replace("{합계}", str(_summary))
                .replace("{딜합계}", str(final))
                .replace("{킬합계}", str(final_kill))
                .replace("{딜}", str(final))
                .replace("{킬}", str(final_kill)))
            # fix_a19: F9/수동 전송 시 가장 최근 기록을 송출됨으로 표시
            try:
                self.history.mark_sent_latest()
            except Exception:
                self.error_logger.exception("history mark_sent_latest 실패")
            threading.Thread(target=self._send_chat, args=(msg,), daemon=True).start()
            if from_hotkey:
                self._last_send_status = "전송 완료"
            return {"ok": True, "status": "전송 완료", "message": msg}
        except Exception:
            self.error_logger.exception("manual_send 처리 중 예외")
            if from_hotkey:
                self._last_send_status = "전송 실패"
            return {"ok": False, "status": "전송 실패"}

    def dispatch_auto_send(self) -> dict:
        if not self._pending_auto_send_message:
            return {"queued": False, "message": "전송 대기 메시지 없음"}
        message = self._pending_auto_send_message
        self._pending_auto_send_message = ""
        # fix_a19: 자동 전송도 송출이므로 가장 최근 기록을 송출됨으로 표시
        try:
            self.history.mark_sent_latest()
        except Exception:
            self.error_logger.exception("history mark_sent_latest 실패 (auto)")
        threading.Thread(target=self._send_chat, args=(message,), daemon=True).start()
        self.event_logger.info("자동 전송 시작: %s", message)
        return {"queued": True, "message": message}

    # fix_a19: 기록 탭용 JS API ─────────────────────────────────────

    def get_history(self) -> list[dict]:
        """JS에서 호출. 전체 기록 리스트 반환 (최신 → 오래된 순)."""
        try:
            return self.history.list_all()
        except Exception:
            self.error_logger.exception("get_history 실패")
            return []

    def log_debug(self, msg: str) -> dict:
        """fix_a20: JS에서 백엔드로 디버그 메시지 보내는 채널.
        events.log에 [JS] 태그로 기록되어 카드 편집 흐름 추적용.
        곡천님 환경에서 console.log가 사라지는 문제 우회.
        """
        try:
            self.event_logger.info("[JS] %s", str(msg)[:500])
            return {"ok": True}
        except Exception:
            return {"ok": False}

    def update_last_result(self, p_kills, p_damages) -> dict:
        """fix_a19: 메인 카드 더블클릭 수정 시 호출.
        - 메인 카드 값 (_last_damage, _last_kill, _last_details) 갱신
        - 가장 최근 history 항목도 동일 값으로 갱신 (수정된 항목 별도 마크 안 함)
        - fix_a19 (수정): 가장 최근 기록이 이미 송출된 상태면 수정 거부
          (자동 전송 ON 등으로 이미 채팅에 박힌 결과 수정 방지)
        - fix_a20: 진단 로그 추가 (진입/인자 타입/가드 분기/완료)
          타입 힌트 list 제거 - pywebview가 list가 아닌 다른 컨테이너로 넘길 가능성 검증용
        """
        # fix_a20: 진단 로그 - 함수 진입 (인자 타입과 내용 그대로 기록)
        try:
            self.event_logger.info(
                "[a20] update_last_result 진입: p_kills_type=%s p_kills=%r p_damages_type=%s p_damages=%r",
                type(p_kills).__name__, p_kills,
                type(p_damages).__name__, p_damages,
            )
        except Exception:
            pass

        try:
            # fix_a25: 세션 락 가드 - 이번 세션에서 F8(add) 한 번도 안 눌렸으면 차단.
            # 재시작 직후 이전 세션 마지막 항목을 의도치 않게 수정하는 것 방지.
            # (JS 측에도 빈 카드 가드가 있어 자연 차단되지만 백엔드도 명시 보호)
            if not self.history.is_session_dirty():
                self.event_logger.info("[a25] 세션 락: F8 한 번도 안 눌린 상태 - 카드 편집 차단")
                return {
                    "ok": False,
                    "error": "이번 실행에서 F8을 한 번도 안 눌렀습니다. 새 OCR 후 수정 가능합니다."
                }

            # 가장 최근 기록 송출 여부 체크
            try:
                items = self.history.list_all()
                # fix_a20: history 상태 진단
                self.event_logger.info(
                    "[a20] history 조회: count=%d latest_sent=%s",
                    len(items),
                    items[0].get("sent") if items else "(없음)",
                )
                if items and items[0].get("sent"):
                    self.event_logger.info("[a20] 가드 차단: 이미 송출된 결과 수정 시도")
                    return {
                        "ok": False,
                        "error": "이미 송출된 결과는 수정할 수 없습니다. 새로 F8을 눌러 OCR하세요."
                    }
            except Exception:
                self.event_logger.info("[a20] history 조회 실패 (무시하고 진행)")
                pass  # 기록 조회 실패는 무시하고 진행

            # fix_a20: 인자가 list/tuple이 아니면 변환 시도 (pywebview 직렬화 케이스 대응)
            try:
                if not hasattr(p_kills, "__iter__") or isinstance(p_kills, (str, bytes, dict)):
                    self.event_logger.info("[a20] p_kills 비-iterable, 빈 리스트로 fallback")
                    p_kills = []
                if not hasattr(p_damages, "__iter__") or isinstance(p_damages, (str, bytes, dict)):
                    self.event_logger.info("[a20] p_damages 비-iterable, 빈 리스트로 fallback")
                    p_damages = []
                p_kills = list(p_kills)
                p_damages = list(p_damages)
            except Exception as exc:
                self.event_logger.info("[a20] 인자 변환 실패: %s", exc)
                return {"ok": False, "error": f"인자 형식 오류: {exc}"}

            # 입력 검증 + RoiResult 갱신
            # fix_a24: 카드 편집은 사용자 명시 입력이므로 sanity 캡 적용 안 함.
            # OCR 단계의 max_kill/max_damage 캡은 garbage 차단용으로 _calculate_impl에 그대로 유지.
            # 음수만 0으로 막고 상한은 두지 않음 (곡천님 정책: "제한할 필요까진 없을 것 같다").
            new_details: list[RoiResult] = []
            new_total_damage = 0
            new_total_kill = 0
            for idx, (k, d) in enumerate(zip(p_kills, p_damages), start=1):
                try:
                    k_int = max(0, int(k))
                except Exception:
                    k_int = 0
                try:
                    d_int = max(0, int(d))
                except Exception:
                    d_int = 0
                new_details.append(RoiResult(idx, k_int, d_int))
                new_total_damage += d_int
                new_total_kill += k_int

            self._last_details = new_details
            self._last_damage = new_total_damage
            self._last_kill = new_total_kill

            # 보너스/배수 계산 (현재 설정 기준)
            chicken_bonus = 0
            kill_bonus = 0
            if self.config.get("chicken_bonus_enabled", False) and getattr(self, "_last_is_chicken", False):
                chicken_bonus = int(self.config.get("chicken_bonus_damage", 0))
            if self.config.get("kill_bonus_enabled", False) and getattr(self, "_last_is_chicken", False):
                kill_bonus = int(self.config.get("kill_bonus_count", 0))
            final_damage = (new_total_damage + chicken_bonus) * self._multiplier
            final_kill = (new_total_kill + kill_bonus) * self._multiplier

            msg_fmt = self.config.get("chat_message_format", "!미션 {합계}")
            read_target = self.config.get("read_target", "damage")
            summary = final_kill if read_target == "kill" else final_damage
            new_message = (msg_fmt
                .replace("{합계}", str(summary))
                .replace("{딜합계}", str(final_damage))
                .replace("{킬합계}", str(final_kill))
                .replace("{딜}", str(final_damage))
                .replace("{킬}", str(final_kill)))
            self._last_result_text = new_message
            # 자동 전송이 켜져 있고 아직 송출 전이면 갱신된 메시지로 교체
            if self._chat_enabled and self._pending_auto_send_message:
                self._pending_auto_send_message = new_message
            try:
                OVERLAY_PATH.write_text(new_message, encoding="utf-8")
                OUTBOX_PATH.write_text(new_message, encoding="utf-8")
            except Exception:
                pass

            # 가장 최근 history 항목 갱신
            players_list = [{"kill": d.kill, "damage": d.damage} for d in new_details]
            try:
                upd_ok = self.history.update_latest(
                    players=players_list,
                    chicken_bonus=chicken_bonus,
                    kill_bonus=kill_bonus,
                    multiplier=self._multiplier,
                    total_damage=new_total_damage,
                    total_kill=new_total_kill,
                    final=final_damage,
                    final_kill=final_kill,
                    message=new_message,
                )
                # fix_a20: history 갱신 결과 로그
                self.event_logger.info("[a20] history.update_latest 결과: ok=%s", upd_ok)
            except Exception:
                self.error_logger.exception("history update_latest 실패")
                self.event_logger.info("[a20] history.update_latest 예외 발생")

            # fix_a20: 정상 완료 로그 (반환 직전 새 값 확인)
            self.event_logger.info(
                "[a20] update_last_result 완료: total_damage=%d total_kill=%d final=%d final_kill=%d",
                new_total_damage, new_total_kill, final_damage, final_kill,
            )

            return {
                "ok": True,
                "total_damage": new_total_damage,
                "total_kill": new_total_kill,
                "final": final_damage,
                "final_kill": final_kill,
                "chicken_bonus": chicken_bonus,
                "kill_bonus": kill_bonus,
                "multiplier": self._multiplier,
                # fix_a21: 카드 편집 후 메인 카드의 +치킨 분기에서 사용
                "is_chicken": bool(getattr(self, "_last_is_chicken", False)),
            }
        except Exception as exc:
            self.error_logger.exception("update_last_result 실패")
            # fix_a20: 예외 발생도 events.log에 명시 (errors.log만 보면 놓칠 수 있음)
            self.event_logger.info("[a20] update_last_result 예외: %s", exc)
            return {"ok": False, "error": str(exc)}

    def resend_history(self, history_id: int) -> dict:
        """fix_a19: 기록 탭에서 재전송 버튼 클릭 시 호출.
        해당 항목의 message를 그대로 송출 (수정된 값이면 수정된 채로).
        """
        try:
            item = self.history.get_by_id(int(history_id))
            if not item:
                return {"ok": False, "error": "기록을 찾을 수 없습니다."}
            message = item.get("message", "")
            if not message:
                return {"ok": False, "error": "전송할 메시지가 비어 있습니다."}
            # 송출 표시
            try:
                self.history.mark_sent_by_id(int(history_id))
            except Exception:
                self.error_logger.exception("history mark_sent_by_id 실패")
            threading.Thread(target=self._send_chat, args=(message,), daemon=True).start()
            self.event_logger.info("기록 재전송: id=%d message=%s", history_id, message)
            return {"ok": True, "message": message}
        except Exception as exc:
            self.error_logger.exception("resend_history 실패")
            return {"ok": False, "error": str(exc)}


    def _connect_chzzk_worker(self) -> None:
        try:
            result = self.chat_client.start_interactive_login()
            self.chat_client.load_tokens()
            self._last_connect_message = result.message
        except Exception as exc:
            self._last_connect_message = f"치지직 연결 실패: {exc}"
            self.error_logger.exception("치지직 연결 작업 실패")
        finally:
            self._connecting = False

    def connect_chzzk(self, client_id: str = "", client_secret: str = "", redirect_uri: str = "") -> dict:
        self.config.setdefault("chat", {})
        if client_id.strip():
            self.config["chat"]["client_id"] = client_id.strip()
        if client_secret.strip():
            self.config["chat"]["client_secret"] = client_secret.strip()
        if redirect_uri.strip():
            self.config["chat"]["redirect_uri"] = redirect_uri.strip()
        save_config(self.config)

        with self._connect_lock:
            if self._connecting:
                return {
                    "message": "이미 치지직 로그인 진행 중입니다.",
                    "connected": self.chat_client.has_valid_access_token(),
                    "status": self._get_chat_status(),
                }
            self._connecting = True
            self._last_connect_message = "치지직 로그인 브라우저를 여는 중..."
            threading.Thread(target=self._connect_chzzk_worker, name="ChzzkLoginWorker", daemon=True).start()

        return {
            "message": self._last_connect_message,
            "connected": self.chat_client.has_valid_access_token(),
            "status": self._get_chat_status(),
        }

    def disconnect_chzzk(self) -> dict:
        self.chat_client.clear_tokens()
        self._last_connect_message = "치지직 연결 해제"
        return {"status": self._get_chat_status()}

    # ── fix_a26: 설명서 / 업데이트 ──────────────────────────────
    def _manual_path(self) -> Path | None:
        """메뉴얼.html 위치 — 실행 폴더(폴더판) → PyInstaller 리소스(설치판) 순."""
        for base in (RUNTIME_BASE_DIR, RESOURCE_BASE_DIR):
            p = base / "메뉴얼.html"
            if p.exists():
                return p
        return None

    def open_manual(self) -> dict:
        """기본 브라우저로 메뉴얼.html 열기. 최초 실행 카드에서도 이 메서드를 쓴다."""
        p = self._manual_path()
        if p is None:
            self.event_logger.warning("메뉴얼.html 없음")
            return {"ok": False, "error": "설명서 파일(메뉴얼.html)을 찾지 못했어요."}
        try:
            if os.name == "nt":
                os.startfile(str(p))  # type: ignore[attr-defined]
            else:
                webbrowser.open(p.as_uri())
            self.event_logger.info("설명서 열기: %s", p)
            return {"ok": True}
        except Exception as exc:
            self.error_logger.exception("설명서 열기 실패")
            return {"ok": False, "error": str(exc)}

    def dismiss_first_run(self) -> dict:
        """최초 실행 안내 카드 닫기 → 다음 실행부터는 설정 탭의 버튼만 남는다."""
        self.config["manual_seen"] = True
        try:
            save_config(self.config)
        except Exception:
            self.error_logger.exception("manual_seen 저장 실패")
        return {"ok": True}

    def open_url(self, url: str) -> None:
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            webbrowser.open(url)

    def get_app_info(self) -> dict:
        return {
            "label": APP_LABEL, "version": APP_VERSION, "fix": APP_FIX,
            "repo": updater.get_repo(self.config), "frozen": updater.is_frozen(),
        }

    def check_update(self, manual: bool = False) -> dict:
        """업데이트 확인을 백그라운드 스레드로 시작. 결과는 poll_state()["update"] 로 전달.
        manual=True 는 설정 탭의 [업데이트 확인] 버튼 — 결과에 checked_manually 표시."""
        if getattr(self, "_update_thread", None) is not None and self._update_thread.is_alive():
            return {"started": False, "reason": "already_running"}

        def _worker() -> None:
            try:
                info = updater.check_update(self.config)
                info["checked_manually"] = bool(manual)
                info["checked_at"] = time.time()
                self._update_info = info
                if info.get("available"):
                    self.event_logger.info("새 버전 있음: %s (현재 %s)", info.get("tag"), APP_LABEL)
                else:
                    self.event_logger.info("업데이트 확인: 최신 (%s) %s", APP_LABEL, info.get("error", ""))
            except Exception:
                self.error_logger.exception("업데이트 확인 실패")
                self._update_info = {"configured": False, "available": False, "current": APP_FIX,
                                     "current_label": APP_LABEL, "error": "확인 실패", "checked_manually": bool(manual)}

        self._update_thread = threading.Thread(target=_worker, name="update-check", daemon=True)
        self._update_thread.start()
        return {"started": True}

    def start_update(self) -> dict:
        """[업데이트] 버튼 — 설치 exe 내려받아 실행하고 앱 종료.
        폴더판(소스 실행)은 설치 exe를 덮어쓸 대상이 없으므로 릴리스 페이지만 연다."""
        info = getattr(self, "_update_info", None) or {}
        if not info.get("available"):
            return {"ok": False, "error": "새 버전 정보가 없어요. 먼저 업데이트 확인을 눌러주세요."}
        if not updater.is_frozen() or not info.get("url"):
            self.open_url(info.get("page") or "")
            return {"ok": True, "opened_page": True}
        if getattr(self, "_update_dl_thread", None) is not None and self._update_dl_thread.is_alive():
            return {"ok": False, "error": "이미 내려받는 중이에요."}

        def _progress(got: int, total: int) -> None:
            self._update_progress = {"got": got, "total": total, "state": "downloading"}

        def _worker() -> None:
            try:
                self._update_progress = {"got": 0, "total": 0, "state": "downloading"}
                path = updater.download_installer(info["url"], _progress)
                self._update_progress = {"got": 1, "total": 1, "state": "launching"}
                self.event_logger.info("설치 파일 내려받음: %s → 실행 후 종료", path)
                updater.launch_installer(path)
                time.sleep(1.0)
                # 설치마법사가 파일을 덮어쓸 수 있도록 앱 종료
                self._tray_quit_app()
            except Exception as exc:
                self.error_logger.exception("업데이트 다운로드/실행 실패")
                self._update_progress = {"got": 0, "total": 0, "state": "error", "error": str(exc)}

        self._update_dl_thread = threading.Thread(target=_worker, name="update-download", daemon=True)
        self._update_dl_thread.start()
        return {"ok": True}

    def open_folder(self, name: str) -> None:
        mapping = {"screenshot": SCREENSHOTS_DIR, "debug": DEBUG_DIR, "logs": LOGS_DIR, "config": RUNTIME_BASE_DIR}
        path = mapping.get(name)
        if not path:
            return
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        if path.exists():
            subprocess.Popen(["explorer", str(path)])

    def clear_folder(self, name: str) -> str:
        mapping = {"screenshot": SCREENSHOTS_DIR, "debug": DEBUG_DIR}
        path = mapping.get(name)
        if not path:
            return "알 수 없는 폴더"
        count = 0
        for item in path.iterdir():
            if item.name == ".keep":
                continue
            if item.is_file():
                item.unlink(); count += 1
            elif item.is_dir():
                shutil.rmtree(item); count += 1
        self.event_logger.info("%s 폴더 비우기: %d개", name, count)
        return f"{name} 폴더 비우기 완료 ({count}개 삭제)"

    def save_settings(self) -> None:
        self.config["monitor_index"] = int(self._monitor_var)
        self.config["default_mode"] = "squad4"
        # read_target은 set_read_target에서 이미 self.config에 반영됨 (덮어쓰지 않음)
        self.config["chat_multiplier"] = self._multiplier
        self.config.setdefault("hotkey", {})["enabled"] = self._hotkey_enabled
        capture_mode = self.config.get("capture_mode", "screen")
        self.config.setdefault("hotkey", {})["display_name"] = "스팀 자동 감지" if capture_mode == "steam" else "수동 계산"
        self.config.setdefault("chat", {})["enabled"] = self._chat_enabled
        # chicken_bonus, kill_bonus는 setter에서 이미 self.config에 반영됨
        save_config(self.config)
        self.event_logger.info("설정 저장 완료")


def _apply_windows_rounded_corners(window, api: WebApi) -> None:
    if os.name != "nt":
        return

    try:
        hwnd = int(window.native.Handle.ToInt32())
    except Exception:
        api.error_logger.exception("실창 핸들 확인 실패")
        return

    try:
        dwmapi = ctypes.windll.dwmapi
        preference = ctypes.c_int(2)  # DWMWCP_ROUND
        border_none = ctypes.c_int(0xFFFFFFFE)  # DWMWA_COLOR_NONE
        dark_mode = ctypes.c_int(1)

        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWA_BORDER_COLOR = 34

        hr_corner = dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(preference),
            ctypes.sizeof(preference),
        )
        hr_border = dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_BORDER_COLOR,
            ctypes.byref(border_none),
            ctypes.sizeof(border_none),
        )
        hr_dark = dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(dark_mode),
            ctypes.sizeof(dark_mode),
        )

        api.event_logger.info(
            "실창 둥근 모서리 적용 시도: hwnd=%s corner_hr=%s border_hr=%s dark_hr=%s",
            hwnd,
            hr_corner,
            hr_border,
            hr_dark,
        )
    except Exception:
        api.error_logger.exception("실창 둥근 모서리 적용 실패")


def _enable_dpi_awareness() -> str:
    """fix_a15.2: DPI 인식 활성화.

    Windows에서 디스플레이 스케일링(125%, 150% 등)이 적용된 환경에서
    mss 캡처가 가상 픽셀(스케일된 크기)을 반환하면 OCR ROI가 어긋남.
    Per-Monitor V2 DPI awareness를 활성화하면 mss가 진짜 픽셀을 반환.

    이 함수는 main() 진입 직후, 다른 GUI 라이브러리(webview, mss 등) 사용 전에 호출.
    이미 DPI awareness가 설정되어 있으면(예: manifest로) 무시됨.

    반환값: 적용 결과 문자열 (로그용)
    """
    if os.name != "nt":
        return "non-windows"
    # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
    PER_MONITOR_V2 = ctypes.c_void_p(-4)
    try:
        # Windows 10 1703+ : SetProcessDpiAwarenessContext (가장 최신/정확)
        user32 = ctypes.windll.user32
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            if user32.SetProcessDpiAwarenessContext(PER_MONITOR_V2):
                return "per_monitor_v2"
    except Exception:
        pass
    try:
        # Windows 8.1+ : SetProcessDpiAwareness (PROCESS_PER_MONITOR_DPI_AWARE = 2)
        shcore = ctypes.windll.shcore
        # 0=unaware, 1=system, 2=per-monitor
        hr = shcore.SetProcessDpiAwareness(2)
        if hr == 0:
            return "per_monitor"
    except Exception:
        pass
    try:
        # Windows Vista+ : SetProcessDPIAware (system-wide DPI)
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except Exception:
        pass
    return "failed"


def main() -> None:
    # fix_a15.2: webview/mss import 전에 DPI 인식 활성화
    # DPI 가상화 켜진 환경(125%/150%/200%)에서 캡처가 어긋나는 문제 방지
    _dpi_result = _enable_dpi_awareness()

    import webview

    # 환경 사전 검사
    # fix_a11: "무시하고 계속" 무한루프 버그 수정
    # 환경변수 PUBG_HELPER_SKIP_ENV_CHECK=1 가 있으면 검사를 건너뛴다.
    # 사용자가 "무시하고 계속" 클릭 시 이 변수를 세팅하고 새 프로세스를 띄우므로,
    # 재시작된 프로세스에서는 같은 경고가 다시 뜨지 않음.
    skip_env_check = os.environ.get("PUBG_HELPER_SKIP_ENV_CHECK", "").strip() == "1"
    env_issues = [] if skip_env_check else check_environment()

    if env_issues:
        dismissed = {"value": False}

        class EnvWarnApi:
            def close_window(self):
                win.destroy()
            def dismiss_env_warning(self):
                dismissed["value"] = True
                win.destroy()

        warn_api = EnvWarnApi()
        html = _make_env_warning_html(env_issues)
        win = webview.create_window(
            "PUBG 스트리머 헬퍼",
            html=html,
            js_api=warn_api,
            width=420, height=360,
            frameless=True,
            background_color="#141820",
        )
        webview.start()

        # 닫기(✕) 눌렀으면 종료, 무시하고 계속 눌렀으면 앱 재시작
        if not dismissed["value"]:
            return

        # fix_a11: 무시하고 계속 → 새 프로세스로 앱 재실행 (환경변수로 검사 건너뛰기 표시)
        # 같은 프로세스에서 webview.start() 를 두 번 호출하는 건 pywebview 가 잘 지원하지 않으므로
        # 재시작 흐름은 유지하되, 새 프로세스가 같은 경고를 다시 띄우지 않도록 환경변수 전달.
        import subprocess
        new_env = os.environ.copy()
        new_env["PUBG_HELPER_SKIP_ENV_CHECK"] = "1"
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable], env=new_env)
        else:
            subprocess.Popen([sys.executable, __file__], env=new_env)
        return


    # 중복 실행 확인
    api = WebApi()
    if api._is_duplicate:
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "PUBG 스트리머 헬퍼가 이미 실행 중입니다.\n\n작업표시줄 또는 트레이를 확인하세요.",
                "이미 실행 중",
                0x30
            )
        return

    runtime_ui = RUNTIME_BASE_DIR / "ui" / "index.html"
    resource_ui = RESOURCE_BASE_DIR / "ui" / "index.html"
    selected_ui = runtime_ui if runtime_ui.exists() else resource_ui
    ui_path = selected_ui.as_uri()
    api.event_logger.info("UI 로드 경로: %s", ui_path)

    icon_path = RESOURCE_BASE_DIR / "app.ico"
    window = webview.create_window(
        "PUBG 스트리머 헬퍼",
        url=ui_path,
        js_api=api,
        width=394,
        height=522,
        min_size=(394, 522),
        background_color="#141820",
        frameless=True,
        resizable=False,
        shadow=True,
    )
    api.window = window

    def on_before_show():
        _apply_windows_rounded_corners(window, api)
        api.schedule_startup_watch(1.2)
        # fix_a26: 시작 시 1회 업데이트 확인 (백그라운드, 실패해도 조용히)
        try:
            api.check_update(manual=False)
        except Exception:
            api.error_logger.exception("시작 시 업데이트 확인 예약 실패")

    def on_closed():
        api.cleanup()

    window.events.before_show += on_before_show
    window.events.closed += on_closed

    webview.start(debug=False, icon=str(icon_path) if icon_path.exists() else None)


if __name__ == "__main__":
    try:
        main()
    except Exception as startup_exc:
        write_fatal_startup_log(startup_exc)
        raise

