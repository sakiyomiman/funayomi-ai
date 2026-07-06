#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
フナヨミAI — gpts.py（GPTs原本チェーンの忠実再現・買い目ビルダー）

原本＝開発者がGPTsで運用していた3体チェーン:
  モーター番長AI（機力ランク◎◯△×）
  → 荒れ度アナライザーAI（★とNO-GO判定）
  → 高度分析AI（伝説の予想師: 荒れ度→展開シナリオ→買い目3〜6点）

再現の核心（高度分析AI④買い目構築・原文ママ）:
  - 本線: イン軸 or モーター最上位軸
  - 対抗: 機力◎選手
  - 穴:   外枠の気配◎・スタート巧者
  - 「頭候補」×「相手2〜3艇」で3〜6点に絞る
  ※ オッズは一切使わない（原本の入力に無い）← ここが現行EVゲートとの決定的な差

荒れ度★の対応（スケールが逆なので変換）:
  engine.areness star: 5=超安定 … 1=大荒れ(NO_GO_HARD)
  原本 高度分析AI ★:   1=堅い(イン鉄板) … 4=大荒れ前提
  → gpts_star = clamp(6 - engine_star, 1, 4)

使い方:
  python3 engine/gpts.py [--in work/race.json]   # 買い目をJSON+テキストで出す
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import player_scores, motor_ranks, areness, head_probs, clamp

ROOT = Path(__file__).resolve().parent.parent
BRAIN_FILE = ROOT / "brain" / "params.json"
_brain_cache = None


def load_brain():
    """フナヨミAI頭脳（学習済みの知恵）を読む。無ければ空＝ベーススキルのみで動く。"""
    global _brain_cache
    if _brain_cache is None:
        _brain_cache = (json.loads(BRAIN_FILE.read_text(encoding="utf-8"))
                        if BRAIN_FILE.exists() else {})
    return _brain_cache


def gpts_yosou(race, brain=None):
    """原本チェーン再現: 番長→荒れ度→予想師の買い目3〜6点。オッズ不使用。
    brain=学習済みの知恵（Noneならbrain/params.jsonを自動ロード・{}で無効化）"""
    if brain is None:
        brain = load_brain()
    # 🧠 頭脳ルール1: 場の選球眼（学習で「勝てない場」と確定した場は参加しない）
    jcd = race["meta"]["jcd"]
    ban = brain.get("venue_ban", {})
    if jcd in ban:
        return {"engine_star": None, "gpts_star": None, "decision": "BRAIN_BAN",
                "skip": True, "final_analysis_status": "SKIPPED_BY_BRAIN",
                "are_flags": [], "scenario": f"頭脳の記憶: {ban[jcd]}",
                "honmei": None, "order": [], "motor_top": None,
                "attack": [], "outer": [], "buys": []}
    ps = player_scores(race)          # 選手力（級別・勝率・ST）
    mo = motor_ranks(race)            # 番長: 機力相対ランク
    ar = areness(race, mo, ps)        # 荒れ度: ★とNO-GO
    hp = head_probs(race, ps, mo, ar) # 頭候補の序列付けに使う（確率値そのものは買い目に不使用）

    star = clamp(6 - ar["star"], 1, 4)   # 原本スケール ★1=堅い〜★4=大荒れ

    # --- 原本の登場人物を決める ---
    order = sorted(hp, key=hp.get, reverse=True)          # 総合の序列（イン基礎値×機力×ST）
    rivals = [w for w in order if w != 1]                 # 1号以外の序列
    motor_top = max(mo, key=lambda w: mo[w]["proxy"])     # 番長の機力最上位
    # 攻撃筋（荒れ度AIレイヤ6）: 2〜4号で機力上位×ST速い
    atk = ar["attack_lanes"] or [w for w in (2, 3, 4) if mo[w]["rank"] in ("◎", "◯")]
    # 外枠の気配◎（穴候補）: 4〜6号で機力上端
    outer = [w for w in (4, 5, 6) if mo[w]["upper"] or mo[w]["rank"] == "◎"]

    A, B = rivals[0], rivals[1]                           # 相手1・2番手
    C = rivals[2] if len(rivals) > 2 else B

    buys, scenario = [], ""
    if star == 1:
        # ★1 堅い＝イン鉄板。1号頭固定×相手2〜3艇
        scenario = "イン逃げ本線（★1鉄板）。1号頭固定、相手は序列上位。"
        buys = [f"1-{A}-{B}", f"1-{B}-{A}", f"1-{A}-{C}", f"1-{B}-{C}"]
    elif star == 2:
        # ★2 本線＋外押さえ。イン本線に機力◎の筋を足す
        scenario = "イン本線＋外押さえ（★2）。機力最上位の筋をケア。"
        buys = [f"1-{A}-{B}", f"1-{B}-{A}", f"1-{A}-{C}"]
        M = motor_top if motor_top != 1 else A
        if f"1-{M}-{A}" not in buys and M != A:
            buys.append(f"1-{M}-{A}")
        buys.append(f"{M}-1-{A}" if M != A else f"{B}-1-{A}")   # 押さえ: 機力◎頭
        buys = buys[:5]
    elif star == 3:
        # ★3 外頭シナリオ本命。攻撃筋Xの頭と1号本線を併記
        X = (atk or [motor_top if motor_top != 1 else A])[0]
        if X == 1: X = A
        scenario = f"外頭シナリオ本命（★3）。攻撃筋{X}号の一撃と1号残しの両にらみ。"
        rest = [w for w in order if w not in (1, X)]
        a2 = rest[0]
        buys = [f"1-{X}-{a2}", f"1-{a2}-{X}", f"{X}-1-{a2}", f"{X}-{a2}-1", f"1-{a2}-{rest[1]}", f"{X}-1-{rest[1]}"]
    else:
        # ★4 大荒れ前提。攻撃筋・外枠気配◎を頭に、1号は2-3着押さえ
        cand = [w for w in (atk + outer) if w != 1]
        X = cand[0] if cand else A
        Y = next((w for w in cand[1:] if w != X), B if B != X else C)
        scenario = f"大荒れ前提（★4）。{X}号・{Y}号の外勢を頭に、1号は連下まで。"
        a2 = next(w for w in order if w not in (X, Y))
        buys = [f"{X}-{Y}-1", f"{X}-1-{Y}", f"{Y}-{X}-1", f"{Y}-1-{X}", f"{X}-{Y}-{a2}", f"{X}-{a2}-1"]

    # 重複除去して3〜6点に収める（原本ルール）
    seen, final = set(), []
    for b in buys:
        x = b.split("-")
        if len(set(x)) == 3 and b not in seen:
            seen.add(b); final.append(b)
    final = final[:6]

    # 参加ゲート（2段）:
    #  ① 原本: 荒れ度NO-GO＝参加しない
    #  ② ★2見送り（2026-07-05検証56Rで確定: ★2は的中15%/回収34%の病巣。
    #     ★1=鉄板・★3=外頭シナリオだけ参加で回収率68%→92%に改善）
    skip_nogo = ar["decision"] in ("NO_GO", "NO_GO_HARD")
    skip_star2 = star == 2
    skip = skip_nogo or skip_star2
    status = ("SKIPPED_BY_ARERDO" if skip_nogo
              else "SKIPPED_MID_RISK" if skip_star2 else "ANALYZED")
    return {
        "engine_star": ar["star"], "gpts_star": star,
        "decision": ar["decision"],
        "skip": skip,
        "final_analysis_status": status,
        "are_flags": ar["flags"],
        "scenario": scenario,
        "honmei": order[0], "order": order,
        "motor_top": motor_top, "attack": atk, "outer": outer,
        "buys": final,
    }


def inject(race, y, analysis_path):
    """work/analysis.json の買い目・判定を新チェーン（GPTs式＋頭脳＋番人）で上書きする。
    UI(build.py)はanalysis.jsonを読むだけなので、ここを差し替えれば
    かっこいいページに新ロジックの予想がそのまま載る（テンプレは無傷）。"""
    from engine import player_scores, motor_ranks, areness, head_probs, combo_probs
    from gatekeeper import odds_guard
    analysis = json.loads(Path(analysis_path).read_text(encoding="utf-8"))
    odds = (race.get("odds") or {}).get("odds3t") or {}

    if y["skip"]:
        analysis["scores"]["verdict"] = "見送り"
        analysis["ev"]["passed"] = []
        analysis["ev"]["synth_odds"] = None
        analysis["ev"]["bet_ok"] = False
        analysis["allocation"] = []
    else:
        buys = y["buys"]
        if y["gpts_star"] == 1:                      # オッズ番人は★1にだけ・参考程度
            g = odds_guard(race, buys)
            if not g["skip"] and g["kept"]:
                buys = g["kept"]
        ps = player_scores(race); mo = motor_ranks(race)
        ar = areness(race, mo, ps); hp = head_probs(race, ps, mo, ar)
        cp = combo_probs(race, hp, ps)
        rows = []
        for c in buys:
            o = odds.get(c)
            p = cp.get(c, 0.0)
            rows.append({"c": c, "odds": o if o else 0.0, "p": round(p, 4),
                         "ev": round(p * o, 2) if o else 0.0})
        inv = sum(1 / r["odds"] for r in rows if r["odds"] > 0)
        synth = round(1 / inv, 1) if inv else None
        n = len(buys); amt = (1000 // n) // 100 * 100 or 100
        analysis["ev"]["passed"] = rows
        analysis["ev"]["synth_odds"] = synth
        analysis["ev"]["bet_ok"] = True
        analysis["allocation"] = [{**r, "amt": amt,
                                   "pay": round(amt * r["odds"]) if r["odds"] else 0} for r in rows]
        analysis["scores"]["verdict"] = "買い" if y["gpts_star"] == 1 else "小口勝負"
    analysis["sakiyomi"] = {"engine": "sakiyomi v1.0 (gpts+brain+guard)",
                            "star": y["gpts_star"], "status": y["final_analysis_status"],
                            "scenario": y["scenario"]}
    Path(analysis_path).write_text(json.dumps(analysis, ensure_ascii=False, indent=1), encoding="utf-8")
    return analysis


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=str(ROOT / "work" / "race.json"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--inject", action="store_true",
                     help="work/analysis.json の買い目/判定を新チェーンの結果で上書き（build.py連携用）")
    a = ap.parse_args()
    race = json.loads(Path(a.inp).read_text(encoding="utf-8"))
    y = gpts_yosou(race)
    if a.inject:
        analysis = inject(race, y, ROOT / "work" / "analysis.json")
        v = analysis["scores"]["verdict"]
        b = " / ".join(r["c"] for r in analysis["ev"]["passed"]) or "なし"
        print(f"inject完了: 判定[{v}] 買い目[{b}] → work/analysis.json（build.pyがこのまま描画する）")
        return
    if a.json:
        print(json.dumps(y, ensure_ascii=False, indent=1)); return
    m = race["meta"]
    print(f"\n【GPTs式】{m['place']}{m['rno']}R  荒れ度★{y['gpts_star']}(原本スケール) 判定={y['decision']}")
    print(f"シナリオ: {y['scenario']}")
    if y["skip"]:
        why = "荒れ度NO-GO" if y["final_analysis_status"] == "SKIPPED_BY_ARERDO" else "★2=中途半端リスク帯"
        print(f"→ {why}＝このレースは参加しない（status={y['final_analysis_status']}）")
    else:
        print(f"買い目({len(y['buys'])}点): {' / '.join(y['buys'])}")


if __name__ == "__main__":
    main()
