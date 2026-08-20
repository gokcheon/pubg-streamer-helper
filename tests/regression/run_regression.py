"""
PUBG 스트리머 헬퍼 - OCR 회귀 테스트
====================================
fix_a15에 추가된 회귀 검증 도구.
패치 후 이 스크립트를 돌려서 기존에 통과한 케이스가 깨졌는지 자동 감지.

사용법:
    cd <프로젝트 루트>
    python tests/regression/run_regression.py

옵션:
    --update-baseline   현재 결과를 새 baseline으로 저장 (의도된 변경 시)
    --quiet             요약만 출력
    --verbose           모든 케이스 상세 출력

출력:
    - PASS/FAIL 카운트
    - baseline 대비 회귀(REGRESSION) / 신규 통과(IMPROVED) 표시
    - 종료 코드: 0 = 모든 케이스 통과, 1 = 회귀 발생, 2 = 신규 실패만 발생

베이스라인 (expected_results.json):
    fix_a15 시점에 처음 만들어졌고, 의도된 패치 시에만 --update-baseline으로 갱신.
    기본 케이스: 50장 valid + 2장 invalid = 52장 모두 통과 = 100%
"""
import argparse
import ast
import json
import logging
import sys
from pathlib import Path

import cv2
import numpy as np

# ----------------------------------------------------------------
# 경로 셋업: 프로젝트 루트 자동 탐색
# ----------------------------------------------------------------
TEST_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TEST_DIR.parent.parent  # tests/regression → tests → 루트
TRUTH_PATH = TEST_DIR / "truth.json"
CAPTURES_DIR = TEST_DIR / "captures"
BASELINE_PATH = TEST_DIR / "expected_results.json"

W, H = 1920, 1080


# ----------------------------------------------------------------
# 매처 클래스 + score_result_screen 추출 (main.py 단일 파일에서)
# ----------------------------------------------------------------
def load_image_unicode_safe(p, flags=cv2.IMREAD_GRAYSCALE):
    if not Path(p).exists():
        return None
    d = np.fromfile(str(p), dtype=np.uint8)
    return cv2.imdecode(d, flags) if d.size else None


def save_image_unicode_safe(p, img):
    cv2.imwrite(str(p), img)


def load_matcher_and_helpers():
    main_py = PROJECT_ROOT / "main.py"
    if not main_py.exists():
        raise FileNotFoundError(f"main.py 없음: {main_py}")

    with open(str(main_py), encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)

    class_source = score_func_source = const_source = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "DigitTemplateMatcher":
            class_source = ast.get_source_segment(source, node)
        elif isinstance(node, ast.FunctionDef) and node.name == "score_result_screen":
            score_func_source = ast.get_source_segment(source, node)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "RESULT_SCREEN_MIN_SCORE":
                    const_source = ast.get_source_segment(source, node)

    if class_source is None:
        raise RuntimeError("DigitTemplateMatcher 클래스 추출 실패")

    ns = {
        "np": np, "cv2": cv2, "Path": Path, "logging": logging,
        "load_image_unicode_safe": load_image_unicode_safe,
        "save_image_unicode_safe": save_image_unicode_safe,
        "RUNTIME_BASE_DIR": PROJECT_ROOT,
        "RESOURCE_BASE_DIR": PROJECT_ROOT,
    }
    exec(class_source, ns)
    if score_func_source:
        exec(score_func_source, ns)
    if const_source:
        exec(const_source, ns)

    return (
        ns["DigitTemplateMatcher"],
        ns.get("score_result_screen"),
        ns.get("RESULT_SCREEN_MIN_SCORE", 0.085),
    )


class _SilentLogger:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def exception(self, *a, **k): pass
    def debug(self, *a, **k): pass


# ----------------------------------------------------------------
# OCR 한 장 처리
# ----------------------------------------------------------------
def normalize_resolution(img):
    h, w = img.shape[:2]
    if w != 1920 or h != 1080:
        return cv2.resize(img, (1920, 1080), interpolation=cv2.INTER_LINEAR)
    return img


def run_ocr_one(img_path, matcher, score_fn, score_threshold, modes_cfg, debug_dir):
    """한 장 처리 결과 반환:
    {
        'gated': bool,           # 결과창 게이트 차단됐는지
        'screen_score': float,   # 결과창 신뢰도
        'slots': [{kill, damage}, ...]  # gated=False일 때만
    }
    """
    data = np.fromfile(str(img_path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        return {"error": "load_fail"}

    img = normalize_resolution(img)
    detected_mode, screen_score = score_fn(img, modes_cfg)

    if screen_score < score_threshold:
        return {
            "gated": True,
            "screen_score": round(screen_score, 4),
            "detected_mode": detected_mode,
        }

    # squad4 강제 (회귀 테스트는 squad4 데이터셋)
    mode = "squad4"
    rois = modes_cfg[mode]["players"]
    slots = []
    for i, p in enumerate(rois):
        kill = dmg = None
        for field in ("kill", "damage"):
            roi = p[field]
            x1, y1 = int(roi[0] * W), int(roi[1] * H)
            x2, y2 = int(roi[2] * W), int(roi[3] * H)
            crop = img[y1:y2, x1:x2]
            try:
                res = matcher.recognize(crop, field, debug_dir, f"reg_{img_path.stem[:20]}_p{i+1}_{field}", mode)
            except Exception:
                res = None
            if res is not None:
                v = int(res["value"])
            elif matcher.last_best_result is not None:
                v = int(matcher.last_best_result.get("value", -1))
            else:
                v = None
            if field == "kill":
                kill = v
            else:
                dmg = v
        slots.append({"kill": kill, "damage": dmg})
    return {
        "gated": False,
        "screen_score": round(screen_score, 4),
        "detected_mode": detected_mode,
        "slots": slots,
    }


# ----------------------------------------------------------------
# 결과 vs 정답 비교
# ----------------------------------------------------------------
def compare_to_truth(result, expected_slots, expected_gated):
    """반환: (status, details)
    status: 'PASS' | 'FAIL'
    details: 실패 시 어디가 틀렸는지 설명
    """
    if expected_gated:
        if result.get("gated"):
            return "PASS", f"gated correctly (score={result.get('screen_score')})"
        # gated 기대인데 통과됨
        slots = result.get("slots", [])
        summary = " | ".join(f"P{i+1}={s['kill']}/{s['damage']}" for i, s in enumerate(slots))
        return "FAIL", f"expected GATED but processed → {summary}"

    if result.get("gated"):
        return "FAIL", f"unexpected GATE (score={result.get('screen_score')})"

    if "slots" not in result:
        return "FAIL", f"no slots: {result}"

    actual_slots = result["slots"]
    mismatches = []
    for i, ((tk, td), s) in enumerate(zip(expected_slots, actual_slots)):
        if s["kill"] != tk:
            mismatches.append(f"P{i+1}킬: 정답={tk} 실제={s['kill']}")
        if s["damage"] != td:
            mismatches.append(f"P{i+1}딜: 정답={td} 실제={s['damage']}")
    if mismatches:
        return "FAIL", "; ".join(mismatches)
    return "PASS", "all slots match"


# ----------------------------------------------------------------
# Baseline 비교
# ----------------------------------------------------------------
def diff_against_baseline(current, baseline):
    """현재 PASS/FAIL vs baseline PASS/FAIL 비교
    반환:
        regressions: baseline=PASS인데 current=FAIL  (회귀 - 가장 위험)
        improvements: baseline=FAIL인데 current=PASS (개선)
        stable_fails: baseline=FAIL이고 current=FAIL (이미 알고 있는 실패)
    """
    regressions, improvements, stable_fails = [], [], []
    for fname, cur in current.items():
        base = baseline.get(fname)
        if base is None:
            # baseline에 없는 케이스 → 신규 추가
            if cur["status"] == "FAIL":
                stable_fails.append(fname)
            continue
        if base["status"] == "PASS" and cur["status"] == "FAIL":
            regressions.append(fname)
        elif base["status"] == "FAIL" and cur["status"] == "PASS":
            improvements.append(fname)
        elif base["status"] == "FAIL" and cur["status"] == "FAIL":
            stable_fails.append(fname)
    return regressions, improvements, stable_fails


# ----------------------------------------------------------------
# 메인
# ----------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="PUBG 헬퍼 OCR 회귀 테스트")
    parser.add_argument("--update-baseline", action="store_true",
                        help="현재 결과를 새 baseline으로 저장")
    parser.add_argument("--quiet", action="store_true", help="요약만 출력")
    parser.add_argument("--verbose", action="store_true", help="상세 출력")
    args = parser.parse_args()

    # 로드
    print("=" * 70)
    print("PUBG 스트리머 헬퍼 - OCR 회귀 테스트")
    print("=" * 70)
    print(f"프로젝트 루트: {PROJECT_ROOT}")
    print(f"테스트 데이터: {CAPTURES_DIR}")
    print()

    DigitTemplateMatcher, score_result_screen, score_threshold = load_matcher_and_helpers()
    if score_result_screen is None:
        print("⚠️  score_result_screen 함수 없음 (a14 이전 버전?). 게이트 검증 불가.")
        score_result_screen = lambda img, cfg: ("squad4", 1.0)  # 항상 통과

    # fix_a26: 깃허브 클론 직후에는 config.json 이 없을 수 있음 → 템플릿으로 폴백
    _cfg_path = PROJECT_ROOT / "config.json"
    if not _cfg_path.exists():
        _cfg_path = PROJECT_ROOT / "config.default.json"
    config = json.load(open(str(_cfg_path), encoding="utf-8"))
    matcher = DigitTemplateMatcher(lambda: config, _SilentLogger(), _SilentLogger(), _SilentLogger())
    modes_cfg = config["modes"]

    truth_data = json.load(open(str(TRUTH_PATH), encoding="utf-8"))
    truth_map = truth_data["truth"]
    invalid_set = set(truth_data["invalid"])

    debug_dir = Path("/tmp/regression_dbg")
    debug_dir.mkdir(exist_ok=True, parents=True)

    # 모든 캡처 처리
    all_files = sorted(CAPTURES_DIR.iterdir())
    if not all_files:
        print(f"❌ 캡처 폴더 비어있음: {CAPTURES_DIR}")
        return 3

    current_results = {}
    print(f"처리 중... (총 {len(all_files)}장)")
    for i, f in enumerate(all_files, 1):
        if not f.suffix.lower() in (".png", ".jpg", ".jpeg"):
            continue
        result = run_ocr_one(f, matcher, score_result_screen, score_threshold, modes_cfg, debug_dir)
        if f.name in invalid_set:
            status, detail = compare_to_truth(result, None, expected_gated=True)
        elif f.name in truth_map:
            status, detail = compare_to_truth(result, truth_map[f.name], expected_gated=False)
        else:
            status, detail = "SKIP", "정답 데이터 없음"
        current_results[f.name] = {"status": status, "detail": detail, "raw": result}
        if args.verbose or (status == "FAIL" and not args.quiet):
            print(f"  [{i:02d}/{len(all_files)}] {status} {f.name}")
            print(f"      {detail}")

    print()
    # 통계
    pass_count = sum(1 for r in current_results.values() if r["status"] == "PASS")
    fail_count = sum(1 for r in current_results.values() if r["status"] == "FAIL")
    skip_count = sum(1 for r in current_results.values() if r["status"] == "SKIP")
    total = len(current_results)
    print(f"결과: PASS={pass_count}/{total}  FAIL={fail_count}  SKIP={skip_count}")

    # Baseline 비교
    exit_code = 0
    if BASELINE_PATH.exists() and not args.update_baseline:
        baseline = json.load(open(str(BASELINE_PATH), encoding="utf-8"))
        baseline_results = baseline.get("results", {})
        regressions, improvements, stable_fails = diff_against_baseline(current_results, baseline_results)

        print()
        print("Baseline 대비:")
        if regressions:
            print(f"  ⚠️  REGRESSION ({len(regressions)}건) - 이전엔 PASS였는데 현재 FAIL:")
            for fname in regressions:
                print(f"      {fname}")
                print(f"        {current_results[fname]['detail']}")
            exit_code = 1
        if improvements:
            print(f"  ✓ IMPROVED ({len(improvements)}건) - 이전엔 FAIL이었는데 현재 PASS:")
            for fname in improvements:
                print(f"      {fname}")
        if stable_fails:
            print(f"  · 알려진 실패 ({len(stable_fails)}건):")
            for fname in stable_fails:
                print(f"      {fname}: {current_results[fname]['detail']}")
        if not regressions and not improvements and not stable_fails:
            print("  변동 없음 (baseline과 동일)")
    else:
        if args.update_baseline:
            baseline_data = {
                "_meta": {
                    "description": "회귀 테스트 baseline",
                    "total": total,
                    "pass": pass_count,
                    "fail": fail_count,
                },
                "results": current_results,
            }
            json.dump(baseline_data, open(str(BASELINE_PATH), "w", encoding="utf-8"),
                      indent=2, ensure_ascii=False)
            print(f"\n✓ Baseline 저장: {BASELINE_PATH}")
        else:
            print(f"\n⚠️  baseline 없음 ({BASELINE_PATH})")
            print(f"   --update-baseline 으로 현재 결과를 baseline으로 저장 가능")

    print("=" * 70)

    if fail_count > 0 and exit_code == 0:
        # baseline에 없는 신규 실패
        exit_code = 2
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
