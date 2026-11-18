# 🎯 PUBG 스트리머 헬퍼 (PUBG Streamer Helper)

PUBG 결과창을 화면에서 읽어(OCR) 팀의 **킬·딜을 치지직 채팅에 자동으로 보내주는** 방송용 도구예요.
결과창에서 <kbd>F8</kbd> 한 번이면 P1~P4 딜이 카드에 뜨고, <kbd>F9</kbd>로 채팅에 전송돼요.

**OCR-based PUBG match-result → Chzzk chat helper for streamers.** FHD / QHD 지원.

## 다운로드

👉 **[Releases](../../releases/latest)** 에서 `pubg_streamer_helper_setup_fix_aNN.exe` 를 받아 설치하세요.
설치판은 새 버전이 나오면 앱 안에서 자동으로 알려드려요 (상단 배너 → [업데이트] 한 번).
사용법은 설치 후 **설정 탭 → 사용 설명서 열기** 또는 이 저장소의 `메뉴얼.html` 을 보세요.

## 주요 기능

- 결과창 OCR: 솔로 / 듀오 / 4인 스쿼드 (3인 스쿼드는 준비 중) — 커스텀 숫자 템플릿 매칭, Tesseract 불필요
- 치지직 연동: 공식 오픈 API OAuth, 토큰 자동 갱신, 메시지 형식 커스텀 (`!미션 {합계}` 등)
- 캡처 방식 2가지: 화면 직접 캡처(F8) / 스팀 스크린샷 폴더 자동 감지(F12)
- 치킨 보너스·킬 보너스·배수, 최근 20판 기록 탭(재전송), 트레이 최소화, 자동 업데이트

## 개발·검증

- `python main.py` 로 폴더판 실행 (Python 3.11, `pip install -r requirements.txt`)
- `python tests/regression/run_regression.py` — 스쿼드4 FHD 캡처 52장 회귀 테스트 (릴리스 전 52/52 필수)
- 새 버전 배포: `version.py` 의 `APP_FIX` +1 → `올리기.bat` (깃허브 서버가 빌드·릴리스)
- 이 저장소는 배포 출력용이에요 — 올리기.bat 이 매번 전체를 새로 올리므로(force push) 웹에서 직접 고친 내용은 다음 배포 때 사라져요

## ⚠ 이용 조건

- **무단 재배포 · 무단 수정 · 2차 배포 · 판매를 금지합니다**
- 공식 배포처는 이 저장소(github.com/gokcheon/pubg-streamer-helper)뿐입니다
- PUBG(크래프톤)·네이버/치지직 공식과 무관한 개인 제작 도구이며, 사용에 따른 책임은 사용자에게 있습니다
- 이 저장소는 오픈소스가 아닙니다 — 자세한 조건은 [LICENSE](LICENSE) 파일을 봐주세요

---

Made by **곡천 (gokcheon)**
