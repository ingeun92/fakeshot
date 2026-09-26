"""2단계: 실물과 AI 사이에 내용과 무관한 차이(지름길)가 있는지 점검한다.

지표별로 두 그룹을 비교하고, 효과 크기(Cliff's delta)가 중간 이상이면 경고한다.
모든 지표를 함께 넣은 단순 분류기가 실물과 AI를 잘 맞히면 지름길이 남아 있다는 뜻이다.
결과: private/check/report.md, contact_real.jpg, contact_ai.jpg
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.stats import mannwhitneyu

from pipeline.common import PRIVATE, as_array, encode_jpeg, load_json

DELTA_WARN = 0.33      # Cliff's delta 중간 효과 기준
LOO_WARN = 0.70        # 단순 분류기 정확도 경고 기준

METRIC_NAMES = {
    "brightness": "밝기 평균",
    "contrast": "대비 (밝기 표준편차)",
    "saturation": "채도 평균",
    "colorfulness": "색 풍부도",
    "sharpness": "선명도 (라플라시안 분산)",
    "noise": "노이즈 추정치",
    "jpeg_kb": "배포 JPEG 용량 (KB)",
}


def _laplacian(y: np.ndarray) -> np.ndarray:
    return (y[:-2, 1:-1] + y[2:, 1:-1] + y[1:-1, :-2] + y[1:-1, 2:] - 4 * y[1:-1, 1:-1])


def image_metrics(img: Image.Image) -> dict[str, float]:
    rgb = as_array(img)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    hsv = np.asarray(img.convert("HSV"), dtype=np.float64)
    rg = r - g
    yb = 0.5 * (r + g) - b
    colorfulness = np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean())
    # Immerkaer 노이즈 추정
    k = (y[:-2, :-2] - 2 * y[:-2, 1:-1] + y[:-2, 2:]
         - 2 * y[1:-1, :-2] + 4 * y[1:-1, 1:-1] - 2 * y[1:-1, 2:]
         + y[2:, :-2] - 2 * y[2:, 1:-1] + y[2:, 2:])
    h, w = y.shape
    noise = np.sqrt(np.pi / 2) * np.abs(k).sum() / (6 * (w - 2) * (h - 2))
    return {
        "brightness": float(y.mean()),
        "contrast": float(y.std()),
        "saturation": float(hsv[..., 1].mean()),
        "colorfulness": float(colorfulness),
        "sharpness": float(_laplacian(y).var()),
        "noise": float(noise),
        "jpeg_kb": len(encode_jpeg(img)) / 1024,
    }


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    """P(a > b) - P(a < b). 양수면 a(실물) 쪽 값이 크다."""
    u = mannwhitneyu(a, b, alternative="two-sided").statistic
    return float(2 * u / (len(a) * len(b)) - 1)


def loo_logistic_accuracy(x: np.ndarray, y: np.ndarray, l2: float = 1.0, steps: int = 400) -> float:
    """표준화한 지표로 로지스틱 회귀를 학습해 leave-one-out 정확도를 잰다."""
    n = len(y)
    correct = 0
    for i in range(n):
        mask = np.arange(n) != i
        xt, yt = x[mask], y[mask]
        mu, sd = xt.mean(0), xt.std(0) + 1e-9
        xs = np.c_[np.ones(len(xt)), (xt - mu) / sd]
        w = np.zeros(xs.shape[1])
        for _ in range(steps):
            p = 1 / (1 + np.exp(-xs @ w))
            grad = xs.T @ (p - yt) / len(yt) + l2 * np.r_[0, w[1:]] / len(yt)
            w -= 0.5 * grad
        xi = np.r_[1, (x[i] - mu) / sd]
        correct += int((1 / (1 + np.exp(-xi @ w)) >= 0.5) == y[i])
    return correct / n


def contact_sheet(paths: list[Path], out: Path, thumb: int = 160, cols: int = 6) -> None:
    rows = max(1, -(-len(paths) // cols))
    sheet = Image.new("RGB", (cols * thumb, rows * (thumb + 16)), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    for i, p in enumerate(paths):
        with Image.open(p) as im:
            t = im.resize((thumb, thumb), Image.Resampling.LANCZOS)
        x, y = (i % cols) * thumb, (i // cols) * (thumb + 16)
        sheet.paste(t, (x, y))
        draw.text((x + 4, y + thumb + 2), p.stem.split("-", 3)[-1][:24], fill=(200, 200, 200))
    sheet.save(out, quality=88)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--normalized", type=Path, default=PRIVATE / "normalized")
    ap.add_argument("--out", type=Path, default=PRIVATE / "check")
    ap.add_argument("--exclude", type=Path, default=PRIVATE / "exclude.txt",
                    help="빼기로 한 이미지 key 목록 (한 줄에 하나)")
    args = ap.parse_args(argv)

    manifest = load_json(args.normalized / "manifest.json")
    excluded = set()
    if args.exclude.exists():
        excluded = {ln.strip() for ln in args.exclude.read_text().splitlines() if ln.strip() and not ln.startswith("#")}
    main_items = [m for m in manifest if m["pool"] == "main" and m["key"] not in excluded]
    real = [m for m in main_items if m["label"] == "real"]
    ai = [m for m in main_items if m["label"] == "ai"]
    if len(real) < 3 or len(ai) < 3:
        print("실물과 AI가 각각 3장 이상 있어야 비교할 수 있습니다.", file=sys.stderr)
        return 1

    for m in main_items:
        with Image.open(args.normalized / f"{m['key']}.png") as img:
            m["metrics"] = image_metrics(img.convert("RGB"))

    args.out.mkdir(parents=True, exist_ok=True)
    lines = ["# 지름길 점검 결과", "",
             f"실물 {len(real)}장, AI {len(ai)}장 비교 (제외 {len(excluded)}장)", "",
             "| 지표 | 실물 평균 | AI 평균 | Cliff's delta | p | 판정 |",
             "|---|---|---|---|---|---|"]
    warnings = []
    for key, name in METRIC_NAMES.items():
        a = np.array([m["metrics"][key] for m in real])
        b = np.array([m["metrics"][key] for m in ai])
        d = cliffs_delta(a, b)
        p = mannwhitneyu(a, b, alternative="two-sided").pvalue
        flag = abs(d) >= DELTA_WARN
        lines.append(f"| {name} | {a.mean():.2f} | {b.mean():.2f} | {d:+.2f} | {p:.3f} | {'**차이 있음**' if flag else '양호'} |")
        if flag:
            higher = "실물" if d > 0 else "AI"
            group = real if d > 0 else ai
            extremes = sorted(group, key=lambda m: m["metrics"][key], reverse=True)[:3]
            warnings.append((name, higher, [m["key"] for m in extremes]))

    x = np.array([[m["metrics"][k] for k in METRIC_NAMES] for m in real + ai])
    y = np.array([1] * len(real) + [0] * len(ai))
    loo = loo_logistic_accuracy(x, y)
    lines += ["", f"모든 지표를 합친 단순 분류기의 leave-one-out 정확도: **{loo:.0%}** "
              f"(50%에 가까울수록 좋음, {LOO_WARN:.0%} 이상이면 경고)"]

    cats = sorted({m["category"] for m in main_items})
    lines += ["", "## 카테고리 균형", "", "| 카테고리 | 실물 | AI |", "|---|---|---|"]
    for c in cats:
        nr = sum(m["category"] == c for m in real)
        na = sum(m["category"] == c for m in ai)
        lines.append(f"| {c} | {nr} | {na} |{' 불균형' if nr != na else ''}")

    ups = [m for m in main_items if m["upscaled"]]
    if ups:
        lines += ["", "## 확대된 이미지", ""] + [f"- {m['key']} (원본 {m['source_size']})" for m in ups]

    if warnings:
        lines += ["", "## 조치 제안", ""]
        for name, higher, keys in warnings:
            lines.append(f"- {name}: {higher} 쪽이 더 높습니다. 값이 가장 큰 {higher} 이미지: {', '.join(keys)}. "
                         "교체하거나 exclude.txt에 넣은 뒤 다시 점검하세요.")

    contact_sheet([args.normalized / f"{m['key']}.png" for m in real], args.out / "contact_real.jpg")
    contact_sheet([args.normalized / f"{m['key']}.png" for m in ai], args.out / "contact_ai.jpg")
    lines += ["", "두 그룹 썸네일을 나란히 비교하세요: contact_real.jpg, contact_ai.jpg"]

    report = "\n".join(lines) + "\n"
    (args.out / "report.md").write_text(report, encoding="utf-8")
    print(report)
    ok = not warnings and loo < LOO_WARN
    print("판정:", "통과" if ok else "지름길 의심. 위 조치 제안을 확인하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
