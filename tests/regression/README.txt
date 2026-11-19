PUBG 스트리머 헬퍼 - OCR 회귀 테스트
========================================

이 폴더는 fix_a15에서 추가된 자동 회귀 검증 도구입니다.
패치 후 이 테스트를 돌려서 기존에 통과한 케이스가 깨졌는지 자동 감지.

────────────────────────────────────────────
[1] 무엇을 테스트하나
────────────────────────────────────────────

스쿼드 4인 모드 캡처 52장 (정답 포함):
  - 50장: 결과창 정상 캡처 (각 4슬롯 × 2필드 = 정답 400개)
  - 2장:  결과창 아닌 캡처 (게이트로 차단되어야 함)

검증 항목:
  ✓ DigitTemplateMatcher의 OCR 정확도 (킬/딜 숫자)
  ✓ score_result_screen 게이트 (결과창 아닌 캡처 차단)

fix_a15 baseline: 52/52 PASS (100%)

────────────────────────────────────────────
[2] 사용법
────────────────────────────────────────────

# 회귀 테스트 실행 (baseline과 비교)
python tests/regression/run_regression.py

# 의도된 변경 후 baseline 갱신 (예: 새 패치가 의도적으로 다른 결과)
python tests/regression/run_regression.py --update-baseline

# 모든 케이스 상세 출력
python tests/regression/run_regression.py --verbose

# 요약만 (회귀 케이스만 출력)
python tests/regression/run_regression.py --quiet

종료 코드:
  0 = 모든 케이스 PASS, baseline과 동일
  1 = REGRESSION 발생 (이전엔 통과했는데 현재 실패) - 위험
  2 = 신규 실패만 발생 (baseline에 없는 새 케이스 실패)
  3 = 환경 오류 (캡처 폴더 없음 등)

────────────────────────────────────────────
[3] 패치 워크플로우 (이걸 지키면 회귀 위험 최소)
────────────────────────────────────────────

1) 패치 전: 현재 baseline이 PASS인지 확인
     python tests/regression/run_regression.py
     → "변동 없음" 확인

2) 패치 작업 (main.py 수정)

3) 패치 후: 회귀 테스트
     python tests/regression/run_regression.py

   결과 보기:
   - PASS 52/52, 변동 없음 → 안전한 패치 (배포 가능)
   - REGRESSION X건 → 위험! 패치가 기존 케이스를 깸. 원인 분석 필요
   - IMPROVED X건 + REGRESSION 0 → 개선 (의도된 패치라면 --update-baseline)

4) 의도된 동작 변경이라면 baseline 갱신
     python tests/regression/run_regression.py --update-baseline
     → expected_results.json 갱신, 다음 패치부터 새 baseline 기준

────────────────────────────────────────────
[4] 새 캡처 추가하기
────────────────────────────────────────────

검증 데이터 늘리고 싶을 때:

1) captures/ 폴더에 새 .png 파일 추가
2) truth.json 열어서 정답 추가:
   {
     "truth": {
       "기존파일.png": [[킬,딜],[킬,딜],[킬,딜],[킬,딜]],
       "새파일.png": [[1,90],[2,282],[0,100],[2,295]],   ← 추가
       ...
     },
     "invalid": [
       "결과창아닌파일.png"   ← 결과창이 아닌 캡처는 여기에
     ]
   }
3) baseline 갱신:
     python tests/regression/run_regression.py --update-baseline

캡처가 많을수록 회귀 검출 신뢰도 ↑. 솔로/듀오/스쿼드3도 모이면
모드별 폴더로 분리 권장 (현재는 squad4 강제 처리).

────────────────────────────────────────────
[5] 폴더 구조
────────────────────────────────────────────

tests/regression/
  ├ run_regression.py        실행 스크립트
  ├ truth.json               정답 데이터 (파일명 → [(킬,딜) x4])
  ├ expected_results.json    baseline (현재 PASS/FAIL 기록)
  ├ captures/                캡처 파일들 (.png)
  └ README.txt               이 파일

────────────────────────────────────────────
[6] 알려진 한계
────────────────────────────────────────────

이 회귀 테스트가 검증하는 범위:
  ✓ 스쿼드 4인 모드 OCR
  ✓ 결과창 게이트 (score_result_screen)

검증 안 되는 범위 (별도 검증 필요):
  ✗ 솔로/듀오/스쿼드3 OCR (캡처 모이면 추가 권장)
  ✗ QHD/4K 해상도 (다운스케일 시 점수 분포 다를 수 있음)
  ✗ 모드 자동 감지 (auto 모드, 현재는 squad4 강제 사용)
  ✗ 치지직 채팅 전송 / OAuth (네트워크 의존)
  ✗ pywebview UI 동작

이 한계를 인지하고: 회귀 테스트가 PASS여도 위 항목은 별도로
실전 테스트해야 함.
