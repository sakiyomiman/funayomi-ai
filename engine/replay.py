#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
サキヨミAI — replay.py（貯めたレースデータに現行ロジックを一括適用・ネット不要）
data/races/*.json を全部読み、gpts式（番長→荒れ度→ゲート→買い目）で採点する。
ロジックを変更したら、これを叩くだけで全レース即再検証（1秒）。

使い方:
  python3 engine/replay.py                 # 全データで検証
  python3 engine/replay.py --hd 20260703   # 日付で絞る
  python3 engine/replay.py --detail        # レースごとの明細も出す
資金ルール: 1レース1,000円を買い目点数で均等割（100円単位切捨て）
"""
import argparse, json, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gpts import gpts_yosou
from gatekeeper import odds_guard

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "races"


def judge_skip(res3t, pay):
    """見送りの答え合わせ: 1号飛び or 3000円以上なら『荒れ』＝見送り正解"""
    return (not res3t.startswith("1-")) or pay >= 3000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hd", default=None, help="日付前方一致（例 202606 で6月全部）")
    ap.add_argument("--jcd", default=None, help="会場コードで絞る（例 07=蒲郡）")
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--guard", action="store_true", help="オッズ番人AI（第4段）を通す")
    a = ap.parse_args()

    files = sorted(DATA.glob("*.json"))
    if a.hd:
        files = [f for f in files if f.name.startswith(a.hd)]
    if a.jcd:
        files = [f for f in files if f.name.split("_")[1] == a.jcd.zfill(2)]
    if not files:
        print("データなし。先に collect.py で収集して"); return

    rows = []
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        y = gpts_yosou(doc)
        res, pay = doc["result"]["san3t"], doc["result"]["pay3t"]
        row = {"place": doc["meta"]["place"], "rno": doc["meta"]["rno"], "hd": doc["meta"]["hd"],
               "star": y["gpts_star"], "status": y["final_analysis_status"],
               "skip": y["skip"], "buys": y["buys"], "res": res, "pay": pay,
               "kimarite": doc["result"].get("kimarite")}
        if not y["skip"] and a.guard:
            g = odds_guard(doc, y["buys"])
            if g["skip"]:
                row["skip"] = True
                row["status"] = f"SKIPPED_BY_ODDS({g['skip_reason']})"
            else:
                row["buys"] = g["kept"]
                row["dropped"] = g["dropped"]
        if row["skip"]:
            row["skip_ok"] = judge_skip(res, pay)
        else:
            n = len(row["buys"]); amt = (1000 // n) // 100 * 100
            row["inv"] = amt * n
            row["hit"] = res in row["buys"]
            row["ret"] = amt / 100 * pay if row["hit"] else 0
        rows.append(row)

    # ---- サマリー ----
    part = [r for r in rows if not r["skip"]]
    skips = [r for r in rows if r["skip"]]
    inv = sum(r["inv"] for r in part); ret = sum(r["ret"] for r in part)
    hits = [r for r in part if r["hit"]]
    print("=" * 66)
    print(f"REPLAY: {len(rows)}レース（{len(set(r['hd'] for r in rows))}日分・1R=1,000円均等）")
    print("=" * 66)
    print(f"参加 {len(part)}R / 見送り {len(skips)}R（見送り率{len(skips)/len(rows)*100:.0f}%）")
    if part:
        print(f"的中 {len(hits)}/{len(part)} ({len(hits)/len(part)*100:.0f}%)  "
              f"投資{inv:,}円 回収{ret:,.0f}円 損益{ret-inv:+,.0f}円 回収率{ret/inv*100:.0f}%")
    if skips:
        ok = sum(1 for r in skips if r["skip_ok"])
        saved = sum(1 for r in skips if r["skip_ok"] and r["pay"] >= 3000)
        print(f"見送り精度 {ok}/{len(skips)} ({ok/len(skips)*100:.0f}%)  うち3千円超の荒れ回避{saved}本")
    # ★別
    print("-" * 66)
    bys = defaultdict(list)
    for r in part: bys[r["star"]].append(r)
    for s in sorted(bys):
        rs = bys[s]; h = [r for r in rs if r["hit"]]
        i2 = sum(r["inv"] for r in rs); r2 = sum(r["ret"] for r in rs)
        print(f"★{s}: 参加{len(rs):>3} 的中{len(h):>2}({len(h)/len(rs)*100:>3.0f}%) 回収率{r2/i2*100:>4.0f}%")
    # 場別
    byp = defaultdict(list)
    for r in part: byp[r["place"]].append(r)
    print("-" * 66)
    for p, rs in sorted(byp.items()):
        h = [r for r in rs if r["hit"]]
        i2 = sum(r["inv"] for r in rs); r2 = sum(r["ret"] for r in rs)
        print(f"{p}: 参加{len(rs):>3} 的中{len(h):>2}({len(h)/len(rs)*100:>3.0f}%) 回収率{r2/i2*100:>4.0f}%")
    # ハズレ解剖
    miss = [r for r in part if not r["hit"]]
    axis = sum(1 for r in miss if r["res"].split("-")[0] not in {b.split("-")[0] for b in r["buys"]})
    aite = len(miss) - axis
    print("-" * 66)
    print(f"ハズレ{len(miss)}本の内訳: 軸ミス{axis} / 頭◯相手×{aite}")

    if a.detail:
        print("\n--- 明細 ---")
        for r in rows:
            if r["skip"]:
                mark = "✅正解" if r["skip_ok"] else "❌損"
                print(f"{r['hd'][-4:]} {r['place']}{r['rno']:>2}R 見送り({r['status'][:12]}) 結果{r['res']} ¥{r['pay']:,} {mark}")
            else:
                mark = f"🎯+{r['ret']:,.0f}円" if r["hit"] else "✗"
                print(f"{r['hd'][-4:]} {r['place']}{r['rno']:>2}R ★{r['star']}参加 結果{r['res']} ¥{r['pay']:,} {mark}")


if __name__ == "__main__":
    main()
