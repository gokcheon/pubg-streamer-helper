"""자동 업데이트 (fix_a26) — GitHub Releases에서 새 버전 확인·다운로드·설치 실행.

보드게임 오버레이 fix28~fix33의 updater를 이 앱 구조(단일 main.py, RESOURCE_BASE_DIR)에 맞게 이식.

동작 흐름:
  ① 저장소 주소 알아내기 — 업데이트주소.txt(배포판 동봉) 또는 config "update_repo"
  ② GitHub 최신 릴리스 조회 → 태그의 마지막 숫자(fix_a27 → 27)를 내 버전(APP_FIX)과 비교
  ③ 새 버전이면 UI 상단 배너 → [업데이트] 시 설치 exe를 임시 폴더에 내려받아 실행
     (설치판 전용 — 폴더판(소스 실행)은 릴리스 페이지 안내만)

실패는 전부 조용히 넘어간다 (인터넷 없음 / 주소 미설정 / 저장소 없음 = 기능 잠자기).
네트워크 호출은 반드시 백그라운드 스레드에서 (main.py check_update 참고) — UI 스레드에서 부르면 창이 멈춘다.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

from version import APP_FIX, APP_LABEL

REPO_FILE = "업데이트주소.txt"          # 배포판에 동봉 — "아이디/저장소" 한 줄
TIMEOUT = 8
UA = {"User-Agent": f"pubg-streamer-helper-{APP_LABEL}", "Accept": "application/vnd.github+json"}


def _candidate_dirs() -> list[Path]:
    """업데이트주소.txt 를 찾을 폴더 — 실행 폴더, PyInstaller 리소스 폴더 순."""
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent)
    dirs.append(Path(__file__).resolve().parent)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass))
    return dirs


def get_repo(cfg: dict | None = None) -> str:
    """저장소 주소 — config["update_repo"] 가 우선, 없으면 업데이트주소.txt. 형식: '아이디/저장소'."""
    repo = str((cfg or {}).get("update_repo", "") or "").strip()
    if not repo:
        for d in _candidate_dirs():
            try:
                for line in (d / REPO_FILE).read_text(encoding="utf-8-sig").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        repo = line
                        break
            except OSError:
                continue
            if repo:
                break
    return repo if re.match(r"^[\w.-]+/[\w.-]+$", repo) else ""


def tag_fix(tag: str) -> int | None:
    """태그에서 fix 번호 추출 — 'fix_a27'·'fix27'·'v1.0.27' 전부 마지막 숫자 사용."""
    nums = re.findall(r"\d+", str(tag or ""))
    return int(nums[-1]) if nums else None


def check_update(cfg: dict | None = None) -> dict:
    """최신 릴리스 확인 (블로킹, 최대 TIMEOUT초).
    반환: {configured, available, current, tag, fix, url, name, page, notes, error}
    """
    base = {"configured": False, "available": False, "current": APP_FIX, "current_label": APP_LABEL, "error": ""}
    repo = get_repo(cfg)
    if not repo:
        base["error"] = "저장소 주소 없음"
        return base
    base["configured"] = True
    base["page"] = f"https://github.com/{repo}/releases/latest"
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}/releases/latest",
                         headers=UA, timeout=TIMEOUT)
        if r.status_code == 404:
            base["error"] = "아직 릴리스가 없음"
            return base
        if r.status_code != 200:
            base["error"] = f"GitHub 응답 {r.status_code}"
            return base
        rel = r.json()
    except Exception as exc:  # 네트워크 없음 등
        base["error"] = f"연결 실패: {type(exc).__name__}"
        return base
    fix = tag_fix(rel.get("tag_name"))
    base["tag"] = str(rel.get("tag_name", ""))
    base["fix"] = fix
    if fix is None or fix <= APP_FIX:
        return base
    asset = next((a for a in rel.get("assets") or []
                  if str(a.get("name", "")).lower().endswith(".exe")), None)
    sha_asset = next((a for a in rel.get("assets") or []
                      if str(a.get("name", "")).lower().endswith(".exe.sha256")), None)
    return {
        **base, "available": True,
        "url": (asset or {}).get("browser_download_url", ""),
        "sha256_url": (sha_asset or {}).get("browser_download_url", ""),  # fix_a29: 있으면 다운로드 후 검증
        "name": (asset or {}).get("name", ""),
        "page": str(rel.get("html_url", "") or base["page"]),
        "notes": str(rel.get("body", "") or "")[:600],
    }


def _fetch_expected_sha256(sha256_url: str) -> str:
    if not sha256_url:
        return ""
    try:
        r = requests.get(sha256_url, headers={"User-Agent": UA["User-Agent"]}, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code == 200:
            token = r.text.strip().split()[0] if r.text.strip() else ""
            return token.lower() if re.fullmatch(r"[0-9a-fA-F]{64}", token) else ""
    except Exception:
        pass
    return ""


def download_installer(url: str, progress=None, sha256_url: str = "") -> Path:
    """설치 exe를 임시 폴더로 내려받는다 (스트리밍). progress(received, total) 콜백 선택.
    fix_a29: 릴리스에 .sha256 이 같이 있으면 내려받은 파일의 해시를 비교하고, 다르면 실행하지 않는다."""
    dest = Path(tempfile.gettempdir()) / "pubg_streamer_helper_update.exe"
    with requests.get(url, headers={"User-Agent": UA["User-Agent"]},
                      timeout=TIMEOUT, stream=True, allow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    got += len(chunk)
                    if progress:
                        try:
                            progress(got, total)
                        except Exception:
                            pass
    if dest.stat().st_size < 1024 * 100:      # 100KB 미만이면 설치 파일이 아님 (오류 페이지 등)
        raise RuntimeError("내려받은 파일이 설치 파일이 아니에요.")
    expected = _fetch_expected_sha256(sha256_url)
    if expected:
        h = hashlib.sha256()
        with dest.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        if h.hexdigest().lower() != expected:
            try:
                dest.unlink()
            except Exception:
                pass
            raise RuntimeError("내려받은 설치 파일의 검증(SHA256)에 실패했어요. 릴리스 페이지에서 직접 받아주세요.")
    return dest


def launch_installer(path: Path) -> None:
    """설치 마법사 실행 (UAC 창이 뜨는 표준 경로). 호출 쪽에서 앱을 곧바로 종료할 것."""
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:                        # 개발 환경 폴백 (실사용 아님)
        subprocess.Popen([str(path)])


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


if __name__ == "__main__":   # `python updater.py` → 업데이트주소.txt의 저장소 주소 (올리기.bat용)
    print(get_repo({}))
