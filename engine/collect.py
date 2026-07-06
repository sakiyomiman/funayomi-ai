#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
フナヨミAI — collect.py（検証用レースデータ一括収集・並列）
出走表/直前情報/オッズ/結果 の4点セットを data/races/{hd}_{jcd}_{rno}.json に貯める。
一度貯めたら replay.py で何度でも瞬時に再分析できる（収集と分析の分離）。

使い方:
  python3 engine/collect.py --hd 20260703 --jcd 04,07,12,13     # 場指定
  python3 engine/collect.py --hd 20260704 --jcd auto            # 開催場を自動検出
  ※ 既に保存済みのレースはスキップ（レジューム可）
"""
import argparse, json, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch as F
from result import parse_result

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "races"


def collect_race(jcd, rno, hd):
    """1レース分の全データを取得して保存。戻り値: (key, status)"""
    key = f"{hd}_{jcd}_{rno:02d}"
    path = OUT / f"{key}.json"
    if path.exists():
        return key, "cached"
    try:
        rl = F.parse_racelist(F.get("racelist", jcd, rno, hd))
        if len(rl["players"]) != 6:
            return key, f"skip({len(rl['players'])}艇)"
        bi = F.parse_beforeinfo(F.get("beforeinfo", jcd, rno, hd))
        od = F.parse_odds3t(F.get("odds3t", jcd, rno, hd))
        res = parse_result(F.get("raceresult", jcd, rno, hd))
        if res is None:
            return key, "結果なし"
        doc = {
            "meta": {"jcd": jcd, "place": F.VENUES.get(jcd, jcd), "rno": rno, "hd": hd,
                     "title": rl["title"], "distance": rl["distance"], "deadline": None},
            "players": rl["players"], "before": bi, "odds": od, "gate": "OK",
            "result": res,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        return key, "OK"
    except Exception as e:
        return key, f"ERR:{type(e).__name__}"


def detect_venues(hd):
    """開催場の自動検出（各場1Rの出走表が6艇立てで取れるか）"""
    found = []
    def probe(jcd):
        try:
            rl = F.parse_racelist(F.get("racelist", jcd, 1, hd))
            return jcd if len(rl["players"]) == 6 else None
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(probe, f"{i:02d}"): i for i in range(1, 25)}
        for f in as_completed(futs):
            r = f.result()
            if r: found.append(r)
    return sorted(found)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hd", required=True)
    ap.add_argument("--jcd", required=True, help="場コードのカンマ区切り or 'auto'")
    ap.add_argument("--workers", type=int, default=5, help="並列数（上げすぎ厳禁・マナー）")
    a = ap.parse_args()

    if a.jcd == "auto":
        venues = detect_venues(a.hd)
        print(f"開催場検出: {venues}")
    else:
        venues = [j.zfill(2) for j in a.jcd.split(",")]

    jobs = [(j, r) for j in venues for r in range(1, 13)]
    print(f"{a.hd}: {len(venues)}場 × 12R = {len(jobs)}レースを並列{a.workers}で収集")
    ok = cached = bad = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(collect_race, j, r, a.hd): (j, r) for j, r in jobs}
        for f in as_completed(futs):
            key, st = f.result()
            if st == "OK": ok += 1
            elif st == "cached": cached += 1
            else: bad += 1; print(f"  {key}: {st}")
    print(f"完了: 新規{ok} / 既存{cached} / 失敗{bad}  ({time.time()-t0:.0f}秒)")
    print(f"→ data/races/ 累計 {len(list(OUT.glob('*.json')))}レース")


if __name__ == "__main__":
    main()
