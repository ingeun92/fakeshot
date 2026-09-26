"""Supabase 연결 점검. config.js를 채운 뒤 실행한다.

1. 키와 주소가 맞는지, 브라우저가 쓰는 함수를 호출할 수 있는지
2. 정답이 든 테이블을 브라우저 키로 읽을 수 없는지
3. (--token을 주면) 그 토큰이 등록돼 있는지
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

from pipeline.common import WEB


def read_config() -> tuple[str, str]:
    text = (WEB / "config.js").read_text(encoding="utf-8")
    url = re.search(r"SUPABASE_URL:\s*'([^']*)'", text).group(1)
    key = re.search(r"SUPABASE_KEY:\s*'([^']*)'", text).group(1)
    return url.rstrip("/"), key


def request(url: str, key: str, path: str, body=None):
    headers = {"apikey": key, "Content-Type": "application/json"}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url + path, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return res.status, json.loads(res.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="ignore")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token", help="등록한 참가자 토큰 하나")
    args = ap.parse_args(argv)
    url, key = read_config()
    if not url or not key:
        print("web/config.js에 SUPABASE_URL과 SUPABASE_KEY를 먼저 넣어 주세요.")
        return 1
    ok = True

    status, body = request(url, key, "/rest/v1/rpc/get_state", {"p_token": "__smoke_test__"})
    if status == 200 and isinstance(body, dict) and body.get("error") == "unknown_token":
        print("통과  함수 호출: 브라우저가 서버 함수를 호출할 수 있습니다.")
    else:
        ok = False
        print(f"실패  함수 호출: {status} {body}\n      schema.sql을 실행했는지, 주소와 키가 맞는지 확인하세요.")

    for table in ("items", "responses", "participants", "sessions"):
        status, body = request(url, key, f"/rest/v1/{table}?select=*&limit=1")
        if status == 200 and body not in ([], None):
            ok = False
            print(f"실패  {table} 테이블이 브라우저 키로 읽힙니다! schema.sql을 다시 실행하세요.")
        else:
            print(f"통과  {table} 테이블은 브라우저 키로 읽을 수 없습니다.")

    if args.token:
        status, body = request(url, key, "/rest/v1/rpc/get_state", {"p_token": args.token})
        if status == 200 and isinstance(body, dict) and body.get("ok"):
            print(f"통과  토큰 확인: 등록돼 있습니다 (그룹 {body['order_group']}, 상태 {body['next']}).")
        else:
            ok = False
            print(f"실패  토큰 확인: {body}\n      seed_participants.sql을 실행했는지 확인하세요.")

    print("\n모두 통과" if ok else "\n실패 항목을 확인하세요.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
