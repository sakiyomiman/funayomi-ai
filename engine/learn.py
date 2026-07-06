#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
フナヨミAI — learn.py（フナヨミAI頭脳の夜間反省会）

人間の記憶構造:
  data/races/  = エピソード記憶（レース原本・collect.pyが貯める）
  brain/       = 意味記憶（抽象化された知恵・ここを育てる）
  engine/      = 手続き記憶（ベース予想スキル・凍結）

学習ルールV1: 場の選球眼（venue_ban）
  「参加N回以上 かつ 回収率がしきい値未満」の場を出禁にする。
  一番過学習しにくい種類の知恵（場の相性は構造的・水面特性由来）から始める。

🔒 walk-forward検証ゲート（過学習の防止装置・この仕組みの心臓部）:
  日付順に「その日より前のデータだけ」で学習→その日に適用、を全日繰り返す。
  = 学習ルール自体が"見たことない日"で本当に得をするかを測る。
  改善しなければ params.json は更新しない（間違った知恵を覚えない）。

使い方:
  python3 engine/learn.py            # 反省会（walk-forward検証→合格なら頭脳更新）
  python3 engine/learn.py --dry      # 検証だけして頭脳は更新しない
"""
import argparse, json, sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gpts import gpts_yosou

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "brain"
DATA = ROOT / "data" / "races"

# 学習ルールV1のパラメータ（知恵の採用基準）
BAN_MIN_N = 10        # 最低この回数参加した場だけ判断対象（少数で決めつけない）
BAN_ROI = 0.55        # 回収率がこれ未満なら出禁


def simulate(doc, brain):
    """1レースをbrain付きで走らせて損益を返す。(参加したか, 投資, 回収)"""
    y = gpts_yosou(doc, brain=brain)
    if y["skip"]:
        return False, 0, 0
    res, pay = doc["result"]["san3t"], doc["result"]["pay3t"]
    n = len(y["buys"]); amt = (1000 // n) // 100 * 100
    inv = amt * n
    ret = amt / 100 * pay if res in y["buys"] else 0
    return True, inv, ret


def learn_venue_ban(docs):
    """場別成績からvenue_banを学習（docs=学習に使うレース群）"""
    stat = defaultdict(lambda: [0, 0, 0])   # jcd -> [参加, 投資, 回収]
    for doc in docs:
        played, inv, ret = simulate(doc, brain={})   # ベーススキルで測る
        if played:
            s = stat[doc["meta"]["jcd"]]
            s[0] += 1; s[1] += inv; s[2] += ret
    ban = {}
    for jcd, (n, inv, ret) in stat.items():
        if n >= BAN_MIN_N and inv > 0 and ret / inv < BAN_ROI:
            ban[jcd] = f"参加{n}R 回収率{ret/inv*100:.0f}%<{BAN_ROI*100:.0f}% で出禁"
    return ban, stat


def freshness_check():
    """🌡 データ資産の鮮度チェック（CLAUDE.md更新日ルール準拠・夜の反省会で毎回炙る）"""
    warns = []
    # コース基礎値: 四半期(90日)超えで警告
    cb = json.loads((ROOT / "engine" / "course_base.json").read_text(encoding="utf-8"))
    upd = cb.get("_updated")
    if upd:
        age = (date.today() - date.fromisoformat(upd)).days
        if age > 90:
            warns.append(f"コース基礎値が{age}日前のまま（四半期超過）→ boatrace.jp stadiumから更新して")
    # 当日の会場モーターキャッシュ: 今日分が1件も無ければ注意
    today = date.today().strftime("%Y%m%d")
    if not list((ROOT / "data" / "motor").glob(f"*_{today}.json")):
        warns.append("今日の会場モーターキャッシュが0件（今日はまだ予想を回してない・異常ではない）")
    for w in warns:
        print(f"🌡 {w}")
    if not warns:
        print("🌡 データ鮮度: 全て許容ライン内")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    freshness_check()

    files = sorted(DATA.glob("*.json"))
    docs_by_day = defaultdict(list)
    for f in files:
        docs_by_day[f.name[:8]].append(json.loads(f.read_text(encoding="utf-8")))
    days = sorted(docs_by_day)
    if len(days) < 3:
        print("日数不足（3日以上必要）。collect.pyでデータを増やして"); return

    # ---- 🔒 walk-forward検証 ----
    print(f"walk-forward検証: {len(days)}日分 {sum(len(v) for v in docs_by_day.values())}レース")
    base = {"inv": 0, "ret": 0}
    wise = {"inv": 0, "ret": 0}
    for i, day in enumerate(days):
        if i == 0:      # 初日は学習材料が無いので両者ベースで
            train = []
        else:
            train = [d for dd in days[:i] for d in docs_by_day[dd]]
        ban, _ = learn_venue_ban(train) if train else ({}, None)
        for doc in docs_by_day[day]:
            _, bi, br = simulate(doc, brain={})
            base["inv"] += bi; base["ret"] += br
            _, wi, wr = simulate(doc, brain={"venue_ban": ban})
            wise["inv"] += wi; wise["ret"] += wr
        tag = f"(出禁{len(ban)}場)" if ban else ""
        print(f"  {day}: 学習{len(train)}R→適用 {tag}")

    b_roi = base["ret"] / base["inv"] * 100 if base["inv"] else 0
    w_roi = wise["ret"] / wise["inv"] * 100 if wise["inv"] else 0
    b_pnl = base["ret"] - base["inv"]; w_pnl = wise["ret"] - wise["inv"]
    print("-" * 60)
    print(f"ベースのみ:   投資{base['inv']:,}円 損益{b_pnl:+,.0f}円 回収率{b_roi:.0f}%")
    print(f"頭脳あり:     投資{wise['inv']:,}円 損益{w_pnl:+,.0f}円 回収率{w_roi:.0f}%")
    passed = w_pnl > b_pnl
    print(f"検証ゲート: {'✅ 合格（知恵は見たことない日でも得をする）' if passed else '❌ 不合格（この知恵は覚えない）'}")

    if a.dry or not passed:
        return

    # ---- 合格→全データで最終学習して頭脳に書き込む ----
    all_docs = [d for dd in days for d in docs_by_day[dd]]
    ban, stat = learn_venue_ban(all_docs)
    BRAIN.mkdir(exist_ok=True)
    params = {"version": 1, "updated": date.today().isoformat(),
              "trained_on": f"{days[0]}〜{days[-1]} {len(all_docs)}R",
              "venue_ban": ban}
    (BRAIN / "params.json").write_text(json.dumps(params, ensure_ascii=False, indent=1), encoding="utf-8")
    (BRAIN / "stats.json").write_text(json.dumps(
        {j: {"n": s[0], "inv": s[1], "ret": s[2]} for j, s in stat.items()},
        ensure_ascii=False, indent=1), encoding="utf-8")
    with (BRAIN / "lessons.md").open("a", encoding="utf-8") as f:
        f.write(f"\n## [{date.today().isoformat()}] 場の選球眼を更新\n")
        f.write(f"- 検証: walk-forward {len(days)}日 → ベース{b_pnl:+,.0f}円 / 頭脳あり{w_pnl:+,.0f}円 ✅\n")
        for j, reason in ban.items():
            f.write(f"- 出禁: 場{j} — {reason}\n")
    print(f"\n🧠 頭脳更新: 出禁{len(ban)}場 → brain/params.json")
    for j, r in ban.items():
        print(f"   場{j}: {r}")


if __name__ == "__main__":
    main()
