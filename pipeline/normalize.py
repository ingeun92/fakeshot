"""1단계: 원본 사진을 정규화해 private/normalized/ 에 저장한다.

폴더 구조 (카테고리 이름은 자유):
  raw/real/<카테고리>/*    직접 촬영한 사진
  raw/ai/<카테고리>/*      AI로 생성한 이미지
  raw/practice/real/*      연습 문항용 (채점 안 함)
  raw/practice/ai/*

처리: 회전 보정, sRGB 변환, 정사각형 크롭(가운데보다 살짝 위), 768px 리사이즈, 메타데이터 제거.
결과는 무손실 PNG로 저장하고, 배포용 JPEG 압축은 build_sets.py에서 한 번만 한다.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from PIL import Image

from pipeline.common import (IMAGE_EXTS, PRIVATE, ROOT, SIZE, WATERMARK_MARGIN_PX, normalize_image,
                             save_json, trimmed_bottom_right)


def collect(raw: Path) -> list[dict]:
    entries = []
    for label in ("real", "ai"):
        base = raw / label
        if base.is_dir():
            for cat_dir in sorted(p for p in base.iterdir() if p.is_dir()):
                for f in sorted(cat_dir.iterdir()):
                    if f.suffix.lower() in IMAGE_EXTS:
                        entries.append({"pool": "main", "label": label, "category": cat_dir.name, "src": f})
            loose = [f for f in base.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS]
            if loose:
                print(f"경고: {base} 바로 아래 파일 {len(loose)}개는 카테고리 폴더에 넣어야 처리됩니다.",
                      file=sys.stderr)
        pbase = raw / "practice" / label
        if pbase.is_dir():
            for f in sorted(pbase.iterdir()):
                if f.suffix.lower() in IMAGE_EXTS:
                    entries.append({"pool": "practice", "label": label, "category": "practice", "src": f})
    return entries


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=ROOT / "raw")
    ap.add_argument("--out", type=Path, default=PRIVATE / "normalized")
    ap.add_argument("--size", type=int, default=SIZE)
    args = ap.parse_args(argv)

    entries = collect(args.raw)
    if not entries:
        print(f"{args.raw} 에서 이미지를 찾지 못했습니다.", file=sys.stderr)
        return 1
    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)

    manifest, failed = [], []
    for i, e in enumerate(entries):
        key = f"{e['pool']}-{e['label']}-{e['category']}-{i:04d}"
        try:
            with Image.open(e["src"]) as img:
                src_size = img.size
                w, h = img.size
                if img.getexif().get(0x0112) in (5, 6, 7, 8):   # 90도 회전 태그면 가로세로가 바뀐다
                    w, h = h, w
                trimmed = trimmed_bottom_right(w, h)
                out, upscaled = normalize_image(img, args.size)
        except Exception as exc:  # 깨진 파일 하나 때문에 전체가 멈추지 않게
            failed.append((e["src"], exc))
            continue
        out.save(args.out / f"{key}.png", format="PNG")
        manifest.append({
            "key": key, "pool": e["pool"], "label": e["label"], "category": e["category"],
            "source": str(e["src"].relative_to(ROOT) if e["src"].is_relative_to(ROOT) else e["src"]),
            "source_size": list(src_size), "upscaled": upscaled,
            "watermark_risk": e["label"] == "ai" and trimmed < WATERMARK_MARGIN_PX,
        })
    save_json(args.out / "manifest.json", manifest)

    counts = {}
    for m in manifest:
        k = (m["pool"], m["label"])
        counts[k] = counts.get(k, 0) + 1
    print(f"정규화 완료: {len(manifest)}장 → {args.out}")
    for (pool, label), n in sorted(counts.items()):
        print(f"  {pool:8s} {label:4s} {n}장")
    ups = [m for m in manifest if m["upscaled"]]
    if ups:
        print(f"\n주의: 원본 짧은 변이 {args.size}px보다 작아 확대된 이미지 {len(ups)}장 (확대 흔적이 단서가 될 수 있음)")
        for m in ups:
            print(f"  {m['source']} {m['source_size']}")
    risky = [m for m in manifest if m["watermark_risk"]]
    if risky:
        print(f"\n주의: 오른쪽 아래가 충분히 잘리지 않아 워터마크가 남을 수 있는 AI 이미지 {len(risky)}장. "
              "세로 3:4로 다시 생성하세요.")
        for m in risky:
            print(f"  {m['source']} {m['source_size']}")
    for src, exc in failed:
        print(f"실패: {src}: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
