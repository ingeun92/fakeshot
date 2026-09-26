"""3단계: 문항을 골라 A/B 세트와 반분용 절반을 배정하고 배포용 파일을 만든다.

- 세트마다 실물과 AI를 같은 수로, 카테고리가 고르게 섞이도록 배정한다.
- 이미지는 무작위 이름의 JPEG로 web/img/ 에 저장한다 (같은 설정으로 한 번만 압축).
- web/items.json 에는 id, 세트, 파일 경로만 들어간다. 정답은 들어가지 않는다.
- 정답이 담긴 파일은 private/ 에만 쓴다: manifest_build.json, seed_items.sql
"""
from __future__ import annotations

import argparse
import random
import secrets
import sys
from pathlib import Path

from PIL import Image

from pipeline.check_leak import check as check_leak
from pipeline.common import PRIVATE, WEB, encode_jpeg, load_exclusions, load_json, save_json


def spread_by_category(items: list[dict], rng: random.Random) -> list[dict]:
    """카테고리를 번갈아 가며 꺼내, 앞에서부터 잘라도 카테고리가 고르게 섞이게 한다."""
    by_cat: dict[str, list[dict]] = {}
    for it in items:
        by_cat.setdefault(it["category"], []).append(it)
    for lst in by_cat.values():
        rng.shuffle(lst)
    cats = sorted(by_cat)
    rng.shuffle(cats)
    out = []
    while any(by_cat[c] for c in cats):
        for c in cats:
            if by_cat[c]:
                out.append(by_cat[c].pop())
    return out


# 카테고리가 번갈아 나오는 목록을 이 순서로 나누면 세트와 절반에 카테고리가 고르게 퍼진다
SLOTS = [("A", 1), ("B", 1), ("A", 2), ("B", 2)]


def assign(items_by_label: dict[str, list[dict]], per_class: int, rng: random.Random) -> list[dict]:
    chosen = []
    for label in ("real", "ai"):
        ordered = spread_by_category(items_by_label[label], rng)[:per_class]
        for i, it in enumerate(ordered):
            set_name, half = SLOTS[i % 4]
            chosen.append({**it, "set": set_name, "half": half})
    return chosen


def pick_practice(practice: list[dict], n: int, rng: random.Random) -> list[dict]:
    pool = practice[:]
    rng.shuffle(pool)
    picked = []
    for label in ("ai", "real"):          # 가능하면 양쪽을 하나씩은 넣는다
        match = next((p for p in pool if p["label"] == label and p not in picked), None)
        if match and len(picked) < n:
            picked.append(match)
    for p in pool:
        if len(picked) >= n:
            break
        if p not in picked:
            picked.append(p)
    return [{**p, "set": "P", "half": None} for p in picked]


def sql_str(v) -> str:
    return "null" if v is None else "'" + str(v).replace("'", "''") + "'"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--normalized", type=Path, default=PRIVATE / "normalized")
    ap.add_argument("--exclude", type=Path, default=PRIVATE / "exclude.txt")
    ap.add_argument("--per-class", type=int, default=30, help="실물과 AI 각각의 본 문항 수 (세트당 절반)")
    ap.add_argument("--practice", type=int, default=3)
    ap.add_argument("--seed", type=int, default=20260925)
    ap.add_argument("--web", type=Path, default=WEB)
    ap.add_argument("--private", type=Path, default=PRIVATE)
    ap.add_argument("--force", action="store_true",
                    help="이미 실제 사진으로 만든 세트가 있어도 새로 만든다 (DB 재시드와 재배포 필요)")
    args = ap.parse_args(argv)

    previous = args.private / "manifest_build.json"
    if previous.exists() and not args.force:
        sources = [r.get("source", "") for r in load_json(previous)]
        if any("placeholder_raw" not in src for src in sources):
            print("이미 실제 사진으로 만든 세트가 있습니다. 다시 만들면 문항 id가 모두 바뀌어,\n"
                  "DB의 문항과 배포된 웹이 어긋나고 진행 중인 참가자가 풀 수 없게 됩니다.\n"
                  "파일럿 시작 전이고 교체가 목적이라면 --force 를 붙이세요.", file=sys.stderr)
            return 1

    if args.per_class % 2:
        print("--per-class는 짝수여야 두 세트에 똑같이 나눌 수 있습니다.", file=sys.stderr)
        return 1
    manifest = load_json(args.normalized / "manifest.json")
    excluded, unmatched = load_exclusions(args.exclude, manifest)
    if unmatched:
        print("exclude.txt에 해당 이미지가 없는 줄이 있습니다. 확인 후 다시 실행하세요:", *unmatched, sep="\n  ", file=sys.stderr)
        return 1
    usable = [m for m in manifest if m["key"] not in excluded]
    by_label = {lab: [m for m in usable if m["pool"] == "main" and m["label"] == lab] for lab in ("real", "ai")}
    practice = [m for m in usable if m["pool"] == "practice"]

    short = {lab: args.per_class - len(v) for lab, v in by_label.items() if len(v) < args.per_class}
    if short or len(practice) < args.practice:
        for lab, n in short.items():
            print(f"{lab} 이미지가 {n}장 부족합니다 (필요 {args.per_class}장).", file=sys.stderr)
        if len(practice) < args.practice:
            print(f"연습 이미지가 {args.practice - len(practice)}장 부족합니다.", file=sys.stderr)
        return 1

    rng = random.Random(args.seed)
    chosen = assign(by_label, args.per_class, rng) + pick_practice(practice, args.practice, rng)

    img_dir = args.web / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    for old in img_dir.glob("*.jpg"):
        old.unlink()

    public, private_rows = [], []
    size = None
    for it in chosen:
        item_id = secrets.token_hex(8)
        with Image.open(args.normalized / f"{it['key']}.png") as img:
            size = img.width
            (img_dir / f"{item_id}.jpg").write_bytes(encode_jpeg(img.convert("RGB")))
        public.append({"id": item_id, "set": it["set"], "file": f"img/{item_id}.jpg"})
        private_rows.append({"id": item_id, "set": it["set"], "half": it["half"], "label": it["label"],
                             "category": it["category"], "key": it["key"], "source": it["source"]})

    public.sort(key=lambda r: r["id"])   # 파일 순서로 정답이 드러나지 않게 id 순으로 둔다
    save_json(args.web / "items.json", public)
    save_json(args.private / "manifest_build.json", private_rows)

    values = ",\n  ".join(
        f"({sql_str(r['id'])}, {sql_str(r['set'])}, {sql_str(r['half'])}, {sql_str(r['label'])}, {sql_str(r['category'])})"
        for r in sorted(private_rows, key=lambda r: r["id"]))
    (args.private / "seed_items.sql").write_text(
        "-- build_sets.py가 생성. 정답이 들어 있으니 공유하지 말 것.\n"
        "-- 응답 기록이 남아 있으면 먼저 supabase/reset_test_data.sql 을 실행한다.\n"
        "begin;\n"
        "delete from items;\n"
        f"insert into items (item_id, set_name, half, label, category) values\n  {values};\n"
        "commit;\n", encoding="utf-8")

    print(f"배포 파일 생성: 문항 {len(public)}개 → {img_dir}, {args.web / 'items.json'}")
    for s in ("A", "B", "P"):
        rows = [r for r in private_rows if r["set"] == s]
        detail = ", ".join(f"{lab} {sum(r['label'] == lab for r in rows)}" for lab in ("real", "ai"))
        print(f"  세트 {s}: {len(rows)}개 ({detail})")
    print(f"정답 파일 (비공개): {args.private / 'seed_items.sql'}")

    problems = check_leak(args.web, args.private / "manifest_build.json", size=size)
    if problems:
        print("\n누수 점검 실패:", *problems, sep="\n  ", file=sys.stderr)
        return 1
    print("누수 점검 통과")
    print("\n다음 순서로 반영하세요: (응답 기록이 있으면 supabase/reset_test_data.sql) → "
          "private/seed_items.sql 실행 → web 폴더 다시 배포")
    return 0


if __name__ == "__main__":
    sys.exit(main())
