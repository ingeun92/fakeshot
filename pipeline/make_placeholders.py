"""실제 사진이 준비되기 전에 전체 흐름을 점검하기 위한 가짜 이미지를 만든다.

실물과 AI를 같은 방식으로 만들기 때문에 판별은 불가능하다. 화면과 파이프라인 점검용이다.
기본 출력: private/placeholder_raw/ (raw/와 같은 폴더 구조)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from pipeline.common import PRIVATE

CATEGORIES = ["food", "indoor", "street", "nature", "object"]


def fake_photo(rng: np.random.Generator, w: int, h: int, n: int) -> Image.Image:
    yy, xx = np.mgrid[0:h, 0:w] / max(w, h)
    c1, c2 = rng.uniform(40, 220, 3), rng.uniform(40, 220, 3)
    t = (np.sin(xx * rng.uniform(2, 6)) + np.cos(yy * rng.uniform(2, 6)) + 2) / 4
    arr = c1 * t[..., None] + c2 * (1 - t[..., None]) + rng.normal(0, 6, (h, w, 3))
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    for _ in range(6):
        x0, y0 = rng.uniform(0, w * 0.8), rng.uniform(0, h * 0.8)
        d.ellipse([x0, y0, x0 + rng.uniform(w * 0.05, w * 0.3), y0 + rng.uniform(h * 0.05, h * 0.3)],
                  fill=tuple(int(v) for v in rng.uniform(0, 255, 3)))
    s = max(w, h) // 8
    d.text((w // 2 - s // 2, h // 2 - s // 2), str(n), fill=(255, 255, 255), font_size=s)
    return img


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=PRIVATE / "placeholder_raw")
    ap.add_argument("--per-category", type=int, default=7)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    rng = np.random.default_rng(args.seed)
    n = 0
    for label in ("real", "ai"):
        for cat in CATEGORIES:
            d = args.out / label / cat
            d.mkdir(parents=True, exist_ok=True)
            for i in range(args.per_category):
                n += 1
                w, h = (1200, 1600) if i % 2 else (1600, 1200)
                fake_photo(rng, w, h, n).save(d / f"{cat}_{i:02d}.jpg", quality=92)
        pd = args.out / "practice" / label
        pd.mkdir(parents=True, exist_ok=True)
        for i in range(2):
            n += 1
            fake_photo(rng, 1400, 1400, n).save(pd / f"practice_{i}.jpg", quality=92)
    print(f"가짜 이미지 {n}장 → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
