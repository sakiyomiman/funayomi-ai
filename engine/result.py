#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
フナヨミAI — result.py（レース結果取得）
boatrace.jp の raceresult ページから 3連単の結果・払戻・決まり手 を取る。
検証（verify.py）用。標準ライブラリのみ。

使い方:
  python3 engine/result.py --jcd 22 --rno 5 --hd 20260704
"""
import argparse, json, re, sys, unicodedata, urllib.request

BASE = "https://www.boatrace.jp/owpc/pc/race"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) funayomi/1.0"}


def get(page, jcd, rno, hd):
    url = f"{BASE}/{page}?rno={rno}&jcd={jcd}&hd={hd}"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")


def strip(seg):
    t = re.sub(r"<[^>]+>", " ", seg)
    t = t.replace("&nbsp;", " ").replace("&yen;", "¥")
    t = unicodedata.normalize("NFKC", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_result(h):
    """3連単の組番・払戻・決まり手・着順(1-3着の艇番)を抜く。未確定ならNone。"""
    text = strip(h)
    out = {}
    # 払戻テーブル: 「3連単 1-2-3 ¥1,240 人気 5」の並びを拾う
    m = re.search(r"3連単\s+(\d)\s*-\s*(\d)\s*-\s*(\d)\s+¥\s*([\d,]+)", text)
    if not m:
        return None
    out["san3t"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    out["pay3t"] = int(m.group(4).replace(",", ""))
    m = re.search(r"3連複\s+(\d)\s*[=\-]\s*(\d)\s*[=\-]\s*(\d)\s+¥\s*([\d,]+)", text)
    if m:
        out["san3f"] = "=".join(sorted([m.group(1), m.group(2), m.group(3)]))
        out["pay3f"] = int(m.group(4).replace(",", ""))
    m = re.search(r"2連単\s+(\d)\s*-\s*(\d)\s+¥\s*([\d,]+)", text)
    if m:
        out["ni2t"] = f"{m.group(1)}-{m.group(2)}"
        out["pay2t"] = int(m.group(3).replace(",", ""))
    # 決まり手
    for k in ("逃げ", "差し", "まくり差し", "まくり", "抜き", "恵まれ"):
        if k in text:
            out["kimarite"] = k
            if k == "まくり" and "まくり差し" in text:
                out["kimarite"] = "まくり差し"
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jcd", required=True)
    ap.add_argument("--rno", required=True, type=int)
    ap.add_argument("--hd", required=True)
    a = ap.parse_args()
    r = parse_result(get("raceresult", a.jcd.zfill(2), a.rno, a.hd))
    if r is None:
        print("未確定 or パース失敗", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
