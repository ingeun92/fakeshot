"""배포 폴더(web/)에 정답을 추론할 단서가 남아 있는지 점검한다.

- items.json에는 id, set, file 외의 필드가 없어야 한다
- 이미지 파일 이름은 무작위 16자리 hex여야 한다
- 모든 이미지는 같은 크기, 같은 압축 설정이고 메타데이터가 없어야 한다
- 원본 파일 이름이나 정규화 key가 web/ 어디에도 나오면 안 된다
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image

from pipeline.common import PRIVATE, SIZE, WEB

HEX_NAME = re.compile(r"^[0-9a-f]{16}$")
TEXT_EXTS = {".js", ".json", ".html", ".css", ".txt", ".md"}


def check(web: Path, private_manifest: Path | None = None, size: int = SIZE) -> list[str]:
    problems: list[str] = []
    items_path = web / "items.json"
    if not items_path.exists():
        return [f"{items_path} 가 없습니다."]
    items = json.loads(items_path.read_text(encoding="utf-8"))

    for it in items:
        if set(it) != {"id", "set", "file"}:
            problems.append(f"items.json 항목에 허용되지 않은 필드: {sorted(set(it) - {'id', 'set', 'file'})}")
        if not HEX_NAME.match(str(it.get("id", ""))):
            problems.append(f"id가 무작위 hex가 아닙니다: {it.get('id')}")
        if it.get("set") not in {"A", "B", "P"}:
            problems.append(f"알 수 없는 세트: {it.get('set')}")
        if it.get("file") != f"img/{it.get('id')}.jpg":
            problems.append(f"파일 경로가 id와 다릅니다: {it.get('file')}")

    img_dir = web / "img"
    listed = {Path(it["file"]).name for it in items if "file" in it}
    present = {p.name for p in img_dir.iterdir() if p.is_file() and not p.name.startswith(".")} if img_dir.exists() else set()
    for extra in sorted(present - listed):
        problems.append(f"items.json에 없는 파일이 img/에 있습니다: {extra}")
    for missing in sorted(listed - present):
        problems.append(f"items.json에 있는 파일이 없습니다: {missing}")

    qtables = None
    for name in sorted(present & listed):
        with Image.open(img_dir / name) as im:
            if im.format != "JPEG":
                problems.append(f"{name}: JPEG가 아닙니다 ({im.format})")
                continue
            if im.size != (size, size):
                problems.append(f"{name}: 크기가 {im.size} 입니다 (기대 {size}x{size})")
            if len(im.getexif()) or "exif" in im.info:
                problems.append(f"{name}: EXIF가 남아 있습니다")
            if im.info.get("icc_profile"):
                problems.append(f"{name}: 색 프로파일이 남아 있습니다")
            if im.info.get("comment"):
                problems.append(f"{name}: 주석이 남아 있습니다")
            q = getattr(im, "quantization", None)
            if qtables is None:
                qtables = q
            elif q != qtables:
                problems.append(f"{name}: 다른 이미지와 압축 설정이 다릅니다")

    if private_manifest and private_manifest.exists():
        private = json.loads(private_manifest.read_text(encoding="utf-8"))
        needles = set()
        for r in private:
            needles.add(r["key"])
            needles.add(Path(r["source"]).stem)
        needles = {n for n in needles if len(n) >= 6}
        for f in web.rglob("*"):
            if f.is_file() and f.suffix in TEXT_EXTS:
                text = f.read_text(encoding="utf-8", errors="ignore")
                for n in needles:
                    if n in text:
                        problems.append(f"{f.relative_to(web)}에 비공개 이름이 들어 있습니다: {n}")
        by_set: dict[str, dict[str, int]] = {}
        for r in private:
            if r["set"] in ("A", "B"):
                by_set.setdefault(r["set"], {"real": 0, "ai": 0})[r["label"]] += 1
        for s, c in by_set.items():
            if c["real"] != c["ai"]:
                problems.append(f"세트 {s}의 실물과 AI 수가 다릅니다: {c}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--web", type=Path, default=WEB)
    ap.add_argument("--manifest", type=Path, default=PRIVATE / "manifest_build.json")
    args = ap.parse_args(argv)
    problems = check(args.web, args.manifest)
    if problems:
        print("누수 점검 실패:", *problems, sep="\n  ")
        return 1
    print("누수 점검 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
