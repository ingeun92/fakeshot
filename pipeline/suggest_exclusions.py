"""카테고리마다 여분 이미지를 빼서 실물과 AI의 밝기, 대비, 색 등 차이를 가장 줄이는 조합을 찾는다.

카테고리마다 실물과 AI를 각각 --keep-per-category 장만 남긴다. 나머지를 뺄 후보로 삼아,
표준화한 지표의 평균 차이 제곱합이 가장 작아지는 조합을 좌표 하강과 무작위 재시작으로 찾는다.
결과는 원본 경로로 exclude.txt에 적는다 (--write). 기존 파일은 덮어쓰지 않는다.
"""
from __future__ import annotations

import argparse
import random
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
from PIL import Image

from pipeline.check_shortcuts import METRIC_NAMES, cliffs_delta, image_metrics, loo_logistic_accuracy
from pipeline.common import PRIVATE, load_json

METRICS = list(METRIC_NAMES)


def imbalance(z: np.ndarray, is_real: np.ndarray, keep: np.ndarray) -> float:
    """남긴 이미지에서 지표별 (실물 평균 - AI 평균)^2 의 합. z는 표준화한 지표."""
    r = z[keep & is_real].mean(0)
    a = z[keep & ~is_real].mean(0)
    return float(((r - a) ** 2).sum())


def search(z, is_real, groups, drop, restarts, seed):
    """groups: {(category, label): [인덱스]}. 각 그룹에서 drop[(category,label)]장을 뺀다."""
    rng = random.Random(seed)
    options = {g: list(combinations(idx, drop[g])) for g, idx in groups.items()}
    cats = sorted({c for c, _ in groups})
    best_score, best_choice = float("inf"), None
    for _ in range(restarts):
        choice = {g: rng.choice(opts) for g, opts in options.items()}
        improved = True
        while improved:
            improved = False
            for c in rng.sample(cats, len(cats)):
                gr, ga = (c, "real"), (c, "ai")
                cur = None
                for orr in options[gr]:
                    for oa in options[ga]:
                        trial = dict(choice)
                        trial[gr], trial[ga] = orr, oa
                        keep = np.ones(len(z), bool)
                        for sel in trial.values():
                            keep[list(sel)] = False
                        s = imbalance(z, is_real, keep)
                        if cur is None or s < cur[0] - 1e-12:
                            cur = (s, orr, oa)
                if (cur[1], cur[2]) != (choice[gr], choice[ga]):
                    choice[gr], choice[ga] = cur[1], cur[2]
                    improved = True
        keep = np.ones(len(z), bool)
        for sel in choice.values():
            keep[list(sel)] = False
        s = imbalance(z, is_real, keep)
        if s < best_score:
            best_score, best_choice = s, choice
    return best_score, best_choice


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--normalized", type=Path, default=PRIVATE / "normalized")
    ap.add_argument("--keep-per-category", type=int, default=6, help="카테고리마다 실물과 AI 각각 남길 장수")
    ap.add_argument("--restarts", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--write", type=Path, help="결과를 적을 exclude.txt 경로 (예: private/exclude.txt)")
    args = ap.parse_args(argv)

    if args.write and args.write.exists():
        print(f"{args.write} 가 이미 있어 덮어쓰지 않습니다. 지우거나 옮긴 뒤 다시 실행하세요.", file=sys.stderr)
        return 1
    items = [m for m in load_json(args.normalized / "manifest.json") if m["pool"] == "main"]
    groups: dict[tuple[str, str], list[int]] = {}
    for i, m in enumerate(items):
        groups.setdefault((m["category"], m["label"]), []).append(i)
    cats = sorted({c for c, _ in groups})
    drop = {}
    for c in cats:
        for lab in ("real", "ai"):
            n = len(groups.get((c, lab), []))
            if n < args.keep_per_category:
                print(f"{c}/{lab}가 {n}장뿐입니다 (필요 {args.keep_per_category}장).", file=sys.stderr)
                return 1
            drop[(c, lab)] = n - args.keep_per_category

    x = np.array([[v for v in image_metrics(Image.open(args.normalized / f"{m['key']}.png").convert("RGB")).values()]
                  for m in items])
    # 사분위 범위로 표준화한다. 표준편차를 쓰면 튀는 이미지가 스스로 척도를 키워 차이가 작게 평가된다.
    q25, q50, q75 = np.percentile(x, [25, 50, 75], axis=0)
    scale = np.where(q75 - q25 > 1e-9, q75 - q25, x.std(0) + 1e-9)
    z = (x - q50) / scale
    is_real = np.array([m["label"] == "real" for m in items])

    all_keep = np.ones(len(items), bool)
    before = imbalance(z, is_real, all_keep)
    score, choice = search(z, is_real, groups, drop, args.restarts, args.seed)
    keep = np.ones(len(items), bool)
    excluded = sorted((i for sel in choice.values() for i in sel), key=lambda i: items[i]["source"])
    keep[excluded] = False

    def report(mask):
        rows = []
        for j, name in enumerate(METRICS):
            rows.append(cliffs_delta(x[mask & is_real, j], x[mask & ~is_real, j]))
        loo = loo_logistic_accuracy(x[mask], is_real[mask].astype(int))
        return rows, loo

    d0, loo0 = report(all_keep)
    d1, loo1 = report(keep)
    print(f"불균형 점수: {before:.3f} → {score:.3f} (낮을수록 실물과 AI가 비슷함)")
    print(f"단순 분류기 leave-one-out 정확도: {loo0:.0%} → {loo1:.0%} (50%에 가까울수록 좋음)")
    print("\n지표별 Cliff's delta (0에 가까울수록 좋음, |0.33| 이상이면 경고)")
    for name, a, b in zip(METRICS, d0, d1):
        print(f"  {METRIC_NAMES[name]:24s} {a:+.2f} → {b:+.2f}")
    print("\n제외할 이미지:")
    for i in excluded:
        print(f"  {items[i]['source']}")

    if args.write:
        args.write.write_text(
            "# suggest_exclusions.py가 제안한 여분 제외 목록. 원본 경로 기준.\n"
            + "".join(f"{items[i]['source']}\n" for i in excluded), encoding="utf-8")
        print(f"\n저장: {args.write}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
