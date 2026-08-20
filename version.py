"""앱 버전 — 단일 진실 (fix_a29에서 신설).

매 배포마다 APP_FIX 숫자만 +1 하면:
  · 설정 탭의 버전 표시
  · 자동 업데이트의 "내 버전" 비교값 (GitHub 릴리스 태그의 마지막 숫자와 비교)
  · 설치마법사 AppVersion (build.yml / build_exe.bat 이 여기서 읽어 주입)
이 전부 함께 바뀐다. 다른 곳에 버전을 하드코딩하지 말 것.

태그 규칙: 올리기.bat 이 APP_LABEL(fix_a29)을 그대로 git 태그로 쓴다.
updater.tag_fix() 는 태그 문자열의 마지막 숫자(26)만 본다.
"""
APP_FIX = 29                          # fix 번호 (자동 업데이트 비교 기준)
APP_VERSION = f"1.0.{APP_FIX}"        # 설치마법사 AppVersion 표기 (숫자.숫자.숫자 형식 필수)
APP_LABEL = f"fix_a{APP_FIX}"         # 화면 표시·git 태그·zip 폴더명

if __name__ == "__main__":
    # `python version.py`       → 1.0.26   (build.yml 이 설치마법사 AppVersion 으로)
    # `python version.py label` → fix_a29  (올리기.bat 이 릴리스 태그로)
    import sys
    print(APP_LABEL if "label" in sys.argv else APP_VERSION)
