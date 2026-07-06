#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
フナヨミAI — verify.py（的中率テストハーネス）
過去レースに対して GPTs式（gpts.py・オッズ不使用） と 現行EV式（engine.py） の
買い目を両方生成し、実結果と突き合わせて 的中率・回収率 を採点する。

使い方:
  python3 engine/verify.py --hd 20260703 --jcd 22          # 福岡の1日12R
  python3 engine/verify.py --hd 20260703 --jcd 22 --rno 5  # 1レースだけ
  python3 engine/verify.py --hd 20260703 --jcd 12,21,22    # 複数場
結果:
  verify/log.jsonl に1レース1行追記 → 最後にサマリー表を表示
"""
import argparse, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch as F
from engine import player_scores, motor_ranks, areness, head_probs, combo_probs, ev_gate
from gpts import gpts_yosou
from result import parse_result

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "verify" / "log.jsonl"


def build_race(jcd, rno, hd):
    rl = F.parse_racelist(F.get("racelist", jcd, rno, hd))
    if len(rl["players"]) != 6:
        return None
    bi = F.parse_beforeinfo(F.get("beforeinfo", jcd, rno, hd))
    od = F.parse_odds3t(F.get("odds3t", jcd, rno, hd))
    return {
        "meta": {"jcd": jcd, "place": F.VENUES.get(jcd, jcd), "rno": rno, "hd": hd,
                 "title": rl["title"], "distance": rl["distance"], "deadline": None},
        "players": rl["players"], "before": bi, "odds": od,
        "gate": "OK" if bi["published"] else "WAIT",
    }


def ev_yosou(race):
    """現行engine.pyのEVゲート買い目（比較用）。"""
    ps = player_scores(race)
    mo = motor_ranks(race)
    ar = areness(race, mo, ps)
    hp = head_probs(race, ps, mo, ar)
    cp = combo_probs(race, hp, ps)
    ev = ev_gate(cp, race["odds"]["odds3t"])
    skip = (not ev["bet_ok"]) or ar["decision"] in ("NO_GO", "NO_GO_HARD")
    return {"skip": skip, "buys": [r["c"] for r in ev["passed"]],
            "odds": {r["c"]: r["odds"] for r in ev["passed"]}}


def score(buys, res, odds_map=None):
    """100円/点で採点。hit=結果と完全一致があるか。"""
    if not buys:
        return {"bet": 0, "hit": False, "ret": 0}
    hit = res["san3t"] in buys
    ret = res["pay3t"] if hit else 0
    return {"bet": len(buys) * 100, "hit": hit, "ret": ret}


def run_race(jcd, rno, hd):
    race = build_race(jcd, rno, hd)
    if race is None:
        return None, "パース失敗"
    res = parse_result(F.get("raceresult", jcd, rno, hd))
    if res is None:
        return None, "結果未確定"
    g = gpts_yosou(race)
    e = ev_yosou(race)
    row = {
        "hd": hd, "jcd": jcd, "place": race["meta"]["place"], "rno": rno,
        "result": res["san3t"], "pay": res["pay3t"], "kimarite": res.get("kimarite"),
        "gpts": {"star": g["gpts_star"], "skip": g["skip"], "buys": g["buys"],
                 **score([] if g["skip"] else g["buys"], res)},
        "ev": {"skip": e["skip"], "buys": e["buys"],
               **score([] if e["skip"] else e["buys"], res)},
    }
    return row, None


def summary(rows, methods=(("GPTs式", "gpts"), ("現行EV式", "ev"))):
    def agg(key):
        played = [r for r in rows if not r[key]["skip"] and r[key]["buys"]]
        hits = [r for r in played if r[key]["hit"]]
        bet = sum(r[key]["bet"] for r in played)
        ret = sum(r[key]["ret"] for r in played)
        return len(played), len(hits), bet, ret
    print("\n" + "=" * 62)
    print(f"検証サマリー（{len(rows)}レース・100円/点）")
    print("-" * 62)
    print(f"{'方式':<10}{'参加':>4}{'的中':>4}{'的中率':>8}{'投資':>9}{'回収':>9}{'回収率':>8}")
    for label, key in methods:
        n, h, bet, ret = agg(key)
        hr = f"{h/n*100:.0f}%" if n else "—"
        roi = f"{ret/bet*100:.0f}%" if bet else "—"
        print(f"{label:<9}{n:>4}{h:>4}{hr:>8}{bet:>8}円{ret:>8}円{roi:>8}")
    print("=" * 62)
    misses = [r for r in rows if not r["gpts"]["skip"] and not r["gpts"]["hit"]]
    if misses:
        print("GPTs式ハズレ内訳（結果 | 買い目）:")
        for r in misses:
            print(f"  {r['place']}{r['rno']}R ★{r['gpts']['star']} 結果{r['result']}({r['kimarite'] or '?'}) ¥{r['pay']:,} | {' '.join(r['gpts']['buys'])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hd", required=True)
    ap.add_argument("--jcd", required=True, help="場コード（カンマ区切りで複数可）")
    ap.add_argument("--rno", type=int, help="指定レースのみ（省略で1-12R全部）")
    ap.add_argument("--sleep", type=float, default=0.6, help="HTTPマナー間隔")
    ap.add_argument("--only", choices=["gpts", "ev", "both"], default="gpts",
                     help="表示する方式（デフォルト: gpts式のみ。比較したい時だけ both）")
    a = ap.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for jcd in [j.zfill(2) for j in a.jcd.split(",")]:
        rnos = [a.rno] if a.rno else range(1, 13)
        for rno in rnos:
            try:
                row, err = run_race(jcd, rno, a.hd)
            except Exception as ex:
                row, err = None, f"{type(ex).__name__}: {ex}"
            if row is None:
                print(f"  skip {F.VENUES.get(jcd, jcd)}{rno}R: {err}")
            else:
                rows.append(row)
                with LOG.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                g = row["gpts"]
                gm = "見送" if g["skip"] else ("◎的中" if g["hit"] else "✗")
                line = f"  {row['place']}{rno:>2}R 結果{row['result']} ¥{row['pay']:,} | GPTs:{gm}"
                if a.only == "both":
                    e = row["ev"]
                    em = "見送" if e["skip"] else ("◎的中" if e["hit"] else "✗")
                    line += f" EV:{em}"
                print(line)
            time.sleep(a.sleep)
    if rows:
        methods = (("GPTs式", "gpts"),) if a.only != "both" else (("GPTs式", "gpts"), ("現行EV式", "ev"))
        summary(rows, methods)


if __name__ == "__main__":
    main()
