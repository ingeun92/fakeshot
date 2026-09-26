"""참가자별 고유 링크를 만든다.

순서 균형을 위해 AB와 BA 그룹을 번갈아 배정한다.
결과: private/participants.csv (보낼 링크 목록), private/seed_participants.sql
이미 링크를 보낸 뒤 다시 만들면 기존 링크가 쓸모없어지므로, 파일이 있으면 --add 로만 추가한다.
"""
from __future__ import annotations

import argparse
import csv
import secrets
import sys
from pathlib import Path

from pipeline.common import PRIVATE, read_csv_rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=20, help="만들 링크 수")
    ap.add_argument("--base-url", required=True, help="배포한 웹 주소 (예: https://fakeshot-pilot.netlify.app/)")
    ap.add_argument("--add", action="store_true", help="기존 목록에 이어서 추가")
    ap.add_argument("--private", type=Path, default=PRIVATE)
    args = ap.parse_args(argv)

    csv_path = args.private / "participants.csv"
    rows: list[dict] = []
    if csv_path.exists():
        if not args.add:
            print(f"{csv_path} 가 이미 있습니다. 링크를 더 만들려면 --add 를 붙이세요.", file=sys.stderr)
            return 1
        rows = read_csv_rows(csv_path)

    base = args.base_url if args.base_url.endswith("/") else args.base_url + "/"
    existing = {r["token"] for r in rows}
    new_rows = []
    for _ in range(args.n):
        token = secrets.token_urlsafe(9)
        while token in existing:
            token = secrets.token_urlsafe(9)
        existing.add(token)
        group = "AB" if (len(rows) + len(new_rows)) % 2 == 0 else "BA"
        new_rows.append({"no": str(len(rows) + len(new_rows) + 1), "token": token, "order_group": group,
                         "link": f"{base}?p={token}", "name": "", "exclude": ""})
    rows += new_rows

    args.private.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:   # BOM: 엑셀에서 한글이 깨지지 않게
        w = csv.DictWriter(f, fieldnames=["no", "token", "order_group", "link", "name", "exclude"])
        w.writeheader()
        w.writerows(rows)
    values = ",\n  ".join(f"('{r['token']}', '{r['order_group']}')" for r in rows)
    (args.private / "seed_participants.sql").write_text(
        f"insert into participants (token, order_group) values\n  {values}\non conflict (token) do nothing;\n",
        encoding="utf-8")
    print(f"링크 {len(new_rows)}개 추가, 총 {len(rows)}개 → {csv_path}")
    print(f"Supabase SQL Editor에서 실행: {args.private / 'seed_participants.sql'}")
    print("participants.csv의 name 칸에 누구에게 보냈는지 적고, 분석에서 뺄 사람(본인 등)은 exclude 칸에 1을 적으세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
