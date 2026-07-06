#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
サキヨミAI — gatekeeper.py（オッズ番人AI・第4段）

役割: 「当たるか」ではなく「この配当なら買っていいか」を判定する。
      予想（gpts式・オッズフリー）が出した買い目に対して、削る/見送るだけ。
      ⚠️ 買い目の追加・変更は絶対にしない（オッズで予想を作らない＝本プロジェクトの鉄則）

設計（2026-07-05 Sakiyomi AI Lab 設計）:
  1. 3連単5倍未満は基本買わない（本線=1点目だけは例外で残す）
  2. 期待値（純モデル確率×オッズ）が低い券は削る
  3. AI本命度が高いのにオッズが高い券は残す（=EVが自然に高く出る）
  4. 人気サイドばかりなら点数を絞る
  5. 万舟狙いだけに寄ったら見送り
  ※ モデル確率はオッズ不使用の combo_probs を使う（市場との循環参照を断つ）

チェーン: 番長 → 荒れ度ゲート → 高度分析(買い目) → 【オッズ番人】 → 購入
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import player_scores, motor_ranks, areness, head_probs, combo_probs

# チューニング可能な閾値（将来 knowledge/params.json で学習対象にする）
MIN_ODDS = 5.0        # ルール1: これ未満は本線以外削る
EV_MIN = 0.75         # ルール2: モデル確率×オッズ がこれ未満は削る
POPULAR_ODDS = 10.0   # ルール4: 全部これ未満＝人気サイドのみ→絞る
LONGSHOT_ODDS = 50.0  # ルール5: 残った券が全部これ以上＝万舟寄り→見送り
SYNTH_MIN = 2.5       # 残した買い目の合成オッズがこれ未満→旨みなし見送り


def odds_guard(race, buys):
    """買い目リストにオッズフィルタを適用。
    返り値: {"kept": [...], "dropped": [(組, 理由)], "skip": bool, "skip_reason": str|None}
    オッズ情報が無い場合は素通し（番人は削るだけの存在・情報が無ければ黙る）。"""
    odds = (race.get("odds") or {}).get("odds3t") or {}
    if not buys or not odds:
        return {"kept": buys, "dropped": [], "skip": False, "skip_reason": None,
                "detail": []}

    # 純モデル確率（オッズ不使用）
    ps = player_scores(race)
    mo = motor_ranks(race)
    ar = areness(race, mo, ps)
    hp = head_probs(race, ps, mo, ar)
    cp = combo_probs(race, hp, ps)

    honsen = buys[0]                      # 本線＝予想AIの1点目
    kept, dropped, detail = [], [], []
    for c in buys:
        o = odds.get(c)
        p = cp.get(c, 0.0)
        ev = round(p * o, 2) if o else None
        detail.append({"c": c, "odds": o, "p": round(p, 4), "ev": ev})
        if o is None:                     # オッズ欠損は残す（情報なしで削らない）
            kept.append(c); continue
        if c == honsen:                   # ルール1例外: 本線は安くても残す
            kept.append(c); continue
        if o < MIN_ODDS:
            dropped.append((c, f"{o}倍<{MIN_ODDS}倍")); continue
        if ev is not None and ev < EV_MIN:
            dropped.append((c, f"EV{ev}<{EV_MIN}")); continue
        kept.append(c)

    # ルール4: 人気サイドばかり→EV上位2点に絞る
    kept_odds = [odds[c] for c in kept if c in odds]
    if kept_odds and all(o < POPULAR_ODDS for o in kept_odds) and len(kept) > 2:
        ev_of = {d["c"]: (d["ev"] or 0) for d in detail}
        keep2 = sorted(kept, key=lambda c: -ev_of.get(c, 0))[:2]
        for c in kept:
            if c not in keep2:
                dropped.append((c, "人気サイド過多→絞り"))
        kept = keep2

    # ルール5: 万舟寄りだけ残った→見送り
    if kept_odds and kept and all(odds.get(c, 0) >= LONGSHOT_ODDS for c in kept):
        return {"kept": [], "dropped": dropped, "skip": True,
                "skip_reason": f"残存が全部{LONGSHOT_ODDS}倍超=万舟頼み", "detail": detail}

    # 合成オッズチェック: 全部残しても旨みが無いなら見送り
    inv = sum(1 / odds[c] for c in kept if c in odds and odds[c] > 0)
    synth = round(1 / inv, 1) if inv else None
    if synth is not None and synth < SYNTH_MIN:
        return {"kept": [], "dropped": dropped, "skip": True,
                "skip_reason": f"合成{synth}倍<{SYNTH_MIN}倍=旨みなし", "detail": detail}

    if not kept:
        return {"kept": [], "dropped": dropped, "skip": True,
                "skip_reason": "全点フィルタ落ち", "detail": detail}
    return {"kept": kept, "dropped": dropped, "skip": False,
            "skip_reason": None, "synth": synth, "detail": detail}
