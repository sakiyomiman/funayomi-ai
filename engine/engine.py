#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
フナヨミAI — engine.py（決定的計算エンジン・LLM不使用）
race.json → 台帳×選手力×番長×荒れ度×スジ×期待値ゲート×資金配分 → analysis.json

設計正本:
  vault reports/2026-06-29_競艇予想AIプロンプト_設計分析.md §8
  vault reports/2026-07-03_競艇予想AI社員設計_プロの設計と強い計算式.md
使い方:
  python3 engine/engine.py [--in work/race.json] [--out work/analysis.json]
"""
import argparse, json, math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COURSE = json.loads((ROOT / "engine" / "course_base.json").read_text(encoding="utf-8"))

# 全国のコース別1着率プライア（ファクター統計・§1-3序列1位の土台）
NATIONAL_PRIOR = {1: 55.9, 2: 14.4, 3: 12.5, 4: 10.6, 5: 5.9, 6: 1.7}
# 難水面7場（当地勝率を加味する場・§1-3）
DIFFICULT = {"03", "04", "15", "16", "17", "20", "22"}
# 級別ポイント（A1=1着率28.8/A2=19.9/B1=9.8/B2=3.7 を100点スケール化）
CLS_PTS = {"A1": 100, "A2": 69, "B1": 34, "B2": 13}

# スジ表: P(2着=j | 1着=i) の基礎重み（§1-3 決まり手×スジ / 8-5）
SUJI2 = {
    1: {2: 32, 3: 18, 4: 22, 5: 17, 6: 9},    # 1逃げ→2差し残り/4カド/5まくり差し・6は薄い
    2: {1: 42, 3: 22, 4: 14, 5: 12, 6: 10},   # 2差し→2-1本線
    3: {4: 30, 5: 24, 1: 22, 2: 14, 6: 10},   # 3まくり→3-4/3-5
    4: {5: 32, 3: 20, 1: 20, 2: 14, 6: 14},   # 4まくり→4-5>4-3
    5: {4: 30, 6: 22, 1: 20, 3: 16, 2: 12},   # 5まくり差し→5-4>5-3
    6: {5: 28, 4: 24, 1: 20, 2: 14, 3: 14},
}
# 市場ブレンド（自モデルの過信をオッズ由来の市場確率で矯正・じゃい/卍式の裏返し）
MARKET_LAMBDA = 0.35   # 0=モデルのみ / 1=市場のみ。検証ログ100Rでキャリブレーションする

def clamp(v, lo, hi): return max(lo, min(hi, v))

# ---------- 選手力AI（§3-②） ----------
def player_scores(race):
    jcd = race["meta"]["jcd"]
    out = {}
    for p in race["players"]:
        cls_pt = CLS_PTS.get(p["cls"], 30)
        win_pt = clamp(p["zk"] / 8.0 * 100, 0, 100)          # 全国勝率8.00=満点
        st_pt  = clamp(100 - (p["st"] - 0.10) * 500, 0, 100)  # ST .10=100 / .20=50
        score = 0.45 * cls_pt + 0.35 * win_pt + 0.20 * st_pt
        # 当地補正は難水面場のみ（±8上限）
        if jcd in DIFFICULT and p["tk"] > 0:
            score += clamp((p["tk"] - p["zk"]) * 4, -8, 8)
        # F持ちは減点（スタート踏み込めない）
        score -= p["F"] * 6
        out[p["w"]] = round(clamp(score, 0, 100), 1)
    return out

# ---------- 番長AI（会場データ増強版・§3既存/8-5＋原本Bonus） ----------
def _venue_motor_bank(race):
    """motorbank.pyが貯めた会場公式モーターデータ（data/motor/{jcd}_{hd}.json）を読む。
    無ければNone＝従来のm2/m3のみで動く（ネットワークには行かない）。"""
    p = ROOT / "data" / "motor" / f"{race['meta']['jcd']}_{race['meta']['hd']}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return {int(k): v for k, v in d["motors"].items()} if d.get("ok") else None


def _venue_bonus(v):
    """原本番長AIのBonus規約（最大+7目安）＋近況Trend補正を会場データから計算。"""
    b = 0.0
    if v.get("yusho") is not None and v["yusho"] >= 2:      b += 3   # 優勝≥2 → +3
    if v.get("yushutsu") is not None and v["yushutsu"] >= 5: b += 2   # 優出≥5 → +2
    if v.get("top3_rate") is not None and v["top3_rate"] >= 55: b += 2  # top3率≥55 → +2
    # 近況5節Trend（蒲郡等）: 通算との乖離で調子の上げ下げを見る（原本EWMAの代替）
    if v.get("recent_win2") is not None and v.get("win2") is not None:
        d = v["recent_win2"] - v["win2"]
        if d >= 8:    b += 3
        elif d >= 4:  b += 1.5
        elif d <= -8: b -= 3
        elif d <= -4: b -= 1.5
    return min(b, 7.0)   # 原本の上限目安


def _percentile(pool, x):
    """percentile_rank_inc（原本番長AIのP2）: 母集団poolでのxの下位割合×100"""
    if not pool:
        return None
    below = sum(1 for v in pool if v < x)
    eq = sum(1 for v in pool if v == x)
    return (below + 0.5 * eq) / len(pool) * 100


def motor_ranks(race):
    """番長AI: モーターだけを分析（原本準拠）。
    会場公式ranking（motorbankキャッシュ）があれば P2=全母集団パーセンタイル で評価し、
    無ければ出走表のm2/m3プロキシにフォールバック。"""
    m2 = {p["w"]: p["m2"] for p in race["players"]}
    proxy = {w: 0.6 * m2[w] + 0.4 * next(p["m3"] for p in race["players"] if p["w"] == w)
             for w in m2}
    bank = _venue_motor_bank(race)
    mn = {p["w"]: p["mn"] for p in race["players"]}
    p2map = {}
    if bank and all(mn[w] in bank for w in proxy):
        # 原本モード: P2（会場の全モーター母集団に対するパーセンタイル）を基礎点に
        pool = [v["win2"] for v in bank.values()]
        for w in proxy:
            v = bank[mn[w]]
            p2 = _percentile(pool, v["win2"])
            p2map[w] = round(p2, 1)
            proxy[w] = p2 + _venue_bonus(v)   # Base=P2 ＋ Bonus（優出優勝/top3/近況Trend）
    vals = list(proxy.values())
    mu = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals)) or 1.0
    out = {}
    order = sorted(proxy, key=proxy.get, reverse=True)
    for w, v in proxy.items():
        z = (v - mu) / sd
        rank = "◎" if z >= 0.8 else "◯" if z >= 0.2 else "△" if z >= -0.5 else "×"
        out[w] = {"proxy": round(v, 1), "z": round(z, 2), "rank": rank,
                  "pos": order.index(w) + 1,            # レース内モーター順位1-6
                  "P2": p2map.get(w),                    # 会場母集団パーセンタイル（原本P2）
                  "venue_data": bool(p2map),             # 公式rankingが効いてるか
                  "upper": m2[w] >= 40.0, "lower": m2[w] <= 25.0}
    return out

# ---------- 荒れ度AI（原本PART2の使えるレイヤをコード化） ----------
def areness(race, motors, pscores):
    jcd = race["meta"]["jcd"]
    w = race["before"]["weather"]
    flags, score = [], 0.0
    # L1 場の素性
    if jcd in {"02", "03", "04"}: score += 1.8; flags.append(f"難水面{COURSE[jcd]['place']}+1.8")
    if jcd in {"18", "24"}:       score -= 0.7; flags.append("イン強場-0.7")
    if COURSE[jcd]["rank"] in ("C", "D"): score += 1.0; flags.append(f"1C{COURSE[jcd]['p1']}%の荒れ場+1.0")
    # L2 風・波
    ws = w.get("wind_ms") or 0
    if ws >= 5: score += 2.2; flags.append(f"風{ws}m+2.2")
    elif ws >= 4: score += 1.2; flags.append(f"風{ws}m+1.2")
    if (w.get("wave_cm") or 0) >= 5: score += 0.8; flags.append("波5cm+0.8")
    # L3 進入（スタ展の並び乱れ）
    entry = race["before"].get("entry") or []
    if entry and entry != sorted(entry):
        score += 1.5; flags.append(f"スタ展進入乱れ{entry}+1.5")
    # L5 級別構成
    lane1 = next(p for p in race["players"] if p["w"] == 1)
    if lane1["cls"] in ("B1", "B2"): score += 1.2; flags.append(f"1号{lane1['cls']}+1.2")
    if all(p["cls"] == "A1" for p in race["players"]): score -= 0.6; flags.append("全員A1-0.6")
    # L6 攻撃筋（番長上端×ST速い 2-4号）
    atk = []
    for p in race["players"]:
        if p["w"] in (2, 3, 4) and (motors[p["w"]]["rank"] in ("◎",) or motors[p["w"]]["upper"]) and p["st"] <= 0.16:
            atk.append(p["w"])
    if atk: score += 2.0; flags.append(f"攻撃筋{atk}+2.0")
    # L7 bancho_input（GPTs原本チェーンの配線・2026-07-05Sakiyomi AI Lab 設計）
    # ⚠️ 07-05監査(15R)の教訓で厳格化:
    #   - ◯まで数えると15/15レースで発火し全体が荒れ側に倒れる → 強機=◎のみ
    #   - 1号機弱は「×かつ5位以下」の明確な弱機のみ（△4位で+1.5→鉄板誤NO_GOの実害）
    #   - 1号機が◎なら外の強機は半減相殺（イン残り優勢）
    #   - 場のイン力で割引（Sランク場は外機がいても1号は飛びにくい）
    bancho = 0.0
    if motors[1]["rank"] == "×" and motors[1]["pos"] >= 5:
        bancho += 1.5; flags.append(f"1号機弱(順位{motors[1]['pos']}/×)+1.5")
    strong_out = [w for w in (3, 4, 5, 6) if motors[w]["rank"] == "◎"]
    if strong_out:
        bancho += 1.2; flags.append(f"外に強機◎{strong_out}+1.2")
    if len(strong_out) >= 2:
        bancho += 1.3; flags.append(f"外に複数強機({len(strong_out)}基)+1.3")
    if motors[1]["rank"] == "◎" and bancho > 0:
        bancho *= 0.5; flags.append("1号機◎で相殺×0.5")
    vf = {"S": 0.6, "A": 0.8, "B": 1.0, "C": 1.15, "D": 1.25}.get(COURSE[jcd]["rank"], 1.0)
    if bancho > 0 and vf != 1.0:
        flags.append(f"場ランク{COURSE[jcd]['rank']}補正×{vf}")
    score += bancho * vf
    score = clamp(score, 0, 10)
    star = 1 if score >= 8 else 2 if score >= 6.5 else 3 if score >= 4.5 else 4 if score >= 2.5 else 5
    decision = {5: "AI_OK_STRONG", 4: "AI_OK", 3: "HUMAN_CHECK", 2: "NO_GO", 1: "NO_GO_HARD"}[star]
    return {"score": round(score, 1), "star": star, "decision": decision,
            "flags": flags, "attack_lanes": atk}

# ---------- 頭確率（§4-3 ベイズ的分解） ----------
def head_probs(race, pscores, motors, are):
    jcd = race["meta"]["jcd"]
    venue_p1 = COURSE[jcd]["p1"]
    prior = dict(NATIONAL_PRIOR)
    prior[1] = venue_p1                                # 1Cは場の実数に置換
    rest = 100 - venue_p1
    rest_nat = sum(NATIONAL_PRIOR[k] for k in (2, 3, 4, 5, 6))
    for k in (2, 3, 4, 5, 6):
        prior[k] = NATIONAL_PRIOR[k] / rest_nat * rest  # 残りを全国比で配る
    st1 = next(p["st"] for p in race["players"] if p["w"] == 1)
    raw = {}
    for p in race["players"]:
        w = p["w"]
        mult = 0.55 + 0.9 * (pscores[w] / 100.0)        # 選手補正 0.55〜1.45
        if w in (2, 3, 4):                              # 相対ST補正（序列3位）
            d = st1 - p["st"]
            if d >= 0.05: mult *= 1.30
            elif d >= 0.02: mult *= 1.15
            elif d <= -0.05: mult *= 0.85
        if motors[w]["upper"]: mult *= 1.10             # 両端のみ微補正（序列7位）
        if motors[w]["lower"]: mult *= 0.92
        raw[w] = prior[w] * mult
    if are["star"] <= 2:                                # 荒れ帯はイン信頼を削る
        raw[1] *= 0.85
    s = sum(raw.values())
    return {w: raw[w] / s for w in raw}

# ---------- 3連単確率＝頭×スジ2着×3着 ----------
def combo_probs(race, heads, pscores):
    strength = {w: 0.5 + pscores[w] / 100.0 for w in heads}
    probs = {}
    for i in heads:
        w2 = {j: SUJI2[i][j] * strength[j] for j in heads if j != i}
        s2 = sum(w2.values())
        for j in w2:
            p2 = w2[j] / s2
            w3 = {k: (10 + NATIONAL_PRIOR[k]) * strength[k] for k in heads if k not in (i, j)}
            s3 = sum(w3.values())
            for k in w3:
                probs[f"{i}-{j}-{k}"] = heads[i] * p2 * (w3[k] / s3)
    return probs

# ---------- オッズ番人（EVゲート・§3-①最優先） ----------
def ev_gate(probs, odds, max_pts=5):
    # 市場確率（払戻率75%を織り込み）とモデル確率を幾何ブレンド→正規化
    blended = {}
    for c, o in odds.items():
        pm = probs.get(c)
        if pm is None or o <= 0: continue
        pmkt = 0.75 / o
        blended[c] = (pm ** (1 - MARKET_LAMBDA)) * (pmkt ** MARKET_LAMBDA)
    s = sum(blended.values()) or 1.0
    blended = {c: v / s for c, v in blended.items()}
    rows = []
    for c, p in blended.items():
        o = odds[c]
        rows.append({"c": c, "odds": o, "p": round(p, 4), "ev": round(p * o, 2)})
    rows.sort(key=lambda r: -r["ev"])
    passed = [r for r in rows if r["ev"] >= 1.0][:max_pts]
    goukai = None
    if passed:
        goukai = round(1 / sum(1 / r["odds"] for r in passed), 1)
    verdict_bet = bool(passed) and (goukai is None or goukai >= 2.0)
    return {"all": rows[:18], "passed": passed, "synth_odds": goukai,
            "bet_ok": verdict_bet}

# ---------- 資金配分（均等払戻・§4-2） ----------
def allocate(passed, total=3000):
    if not passed: return []
    inv = sum(1 / r["odds"] for r in passed)
    synth = 1 / inv
    out = []
    for r in passed:
        amt = max(100, round(total * synth / r["odds"] / 100) * 100)
        out.append({**r, "amt": amt, "pay": round(amt * r["odds"])})
    return out

# ---------- UIスコア6軸＋総合 ----------
def ui_scores(race, pscores, motors, are, heads, ev):
    jcd = race["meta"]["jcd"]
    course = clamp(COURSE[jcd]["p1"] * 1.5, 40, 97)
    player = clamp(sum(sorted(pscores.values(), reverse=True)[:3]) / 3 * 1.1, 30, 96)
    mvals = [m["proxy"] for m in motors.values()]
    motor = clamp(50 + (max(mvals) - (sum(mvals) / len(mvals))) * 2.2, 35, 92)
    top = max(heads, key=heads.get)
    suji = clamp(heads[top] * 100 * 1.15, 35, 92)
    tenji = 70 if race["before"]["published"] else 40
    arescore = clamp(are["score"] * 10, 5, 95)
    total = round(0.30 * course + 0.24 * player + 0.10 * motor +
                  0.16 * suji + 0.08 * tenji + 0.12 * (100 - arescore))
    # 判定4段階（買い→小口勝負→見送り推奨→見送り）。数字だけでなく言葉で温度差を伝える。
    if not ev["bet_ok"] or are["decision"] in ("NO_GO", "NO_GO_HARD"):
        verdict = "見送り"          # 期待値ゲート不通過 or 荒れ度NO-GO＝ハッキリ買うな
    elif total >= 75:
        verdict = "買い"           # 総合75+・期待値も通過＝自信あり
    elif total >= 60:
        verdict = "小口勝負"        # 60-74・期待値は通ったが強くはない＝張るなら少額で
    else:
        verdict = "見送り推奨"      # 40-59・期待値はギリ立つが根拠が弱い＝実質見送り寄り
    return {"course": round(course), "player": round(player), "motor": round(motor),
            "suji": round(suji), "tenji": tenji, "are": round(arescore),
            "total": total, "verdict": verdict}

# ---------- 予想マーク ----------
def marks(heads, pscores):
    order = sorted(heads, key=heads.get, reverse=True)
    mk = {order[0]: "◎", order[1]: "◯", order[2]: "▲", order[3]: "△"}
    kill = sorted(order[4:])
    return {"marks": mk, "kill": kill}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=str(ROOT / "work" / "race.json"))
    ap.add_argument("--out", default=str(ROOT / "work" / "analysis.json"))
    ap.add_argument("--total", type=int, default=3000)
    ap.add_argument("--quick", action="store_true",
                     help="HTML生成前提の分析はせず、チャット即答用のテキストサマリーだけ標準出力する（爆速モード）")
    a = ap.parse_args()
    race = json.loads(Path(a.inp).read_text(encoding="utf-8"))

    ps = player_scores(race)
    mo = motor_ranks(race)
    ar = areness(race, mo, ps)
    hp = head_probs(race, ps, mo, ar)
    cp = combo_probs(race, hp, ps)
    ev = ev_gate(cp, race["odds"]["odds3t"])
    al = allocate(ev["passed"], a.total)
    sc = ui_scores(race, ps, mo, ar, hp, ev)
    mk = marks(hp, ps)
    jcd = race["meta"]["jcd"]

    analysis = {
        "course_base": {**COURSE[jcd], "jcd": jcd},
        "player_scores": {str(k): v for k, v in ps.items()},
        "motors": {str(k): v for k, v in mo.items()},
        "areness": ar,
        "head_probs": {str(k): round(v, 4) for k, v in hp.items()},
        "ev": ev, "allocation": al, "scores": sc,
        "marks": {str(k): v for k, v in mk["marks"].items()}, "kill": mk["kill"],
        "engine": "funayomi v1.0",
    }
    Path(a.out).write_text(json.dumps(analysis, ensure_ascii=False, indent=1), encoding="utf-8")

    if a.quick:
        print_quick_summary(race, analysis)
        return
    heads_s = " ".join(f"{w}:{hp[w]*100:.0f}%" for w in sorted(hp, key=hp.get, reverse=True))
    print(f"総合{sc['total']} 判定[{sc['verdict']}] 荒れ★{ar['star']}({ar['decision']}) 頭確率 {heads_s}")
    print(f"EV通過{len(ev['passed'])}点 合成{ev['synth_odds']}倍 → {a.out}")

def print_quick_summary(race, a):
    """爆速モード：HTML生成・予想師の文章執筆をすっ飛ばして、数値だけをその場で読める形に整形する。"""
    m, cb, sc, ar = race["meta"], a["course_base"], a["scores"], a["areness"]
    names = {p["w"]: p["name"] for p in race["players"]}
    print(f"\n【{m['place']}{m['rno']}R】{m['title'] or ''} 締切{m['deadline'] or '—'}")
    print(f"コース基礎値: {cb['place']}1C {cb['p1']}%（{cb['rank']}ランク） / 荒れ度★{ar['star']}（{ar['decision']}）")
    print(f"判定: {sc['total']}点 → 「{sc['verdict']}」\n")
    print("印 艇 選手           頭確率  選手力 モーター")
    order = sorted(a["head_probs"], key=lambda w: -a["head_probs"][w])
    mkmap = a["marks"]
    for w in order:
        mk = mkmap.get(w, " ")
        print(f" {mk}  {w}  {names[int(w)]:<10} {a['head_probs'][w]*100:>5.0f}%   "
              f"{a['player_scores'][w]:>4.0f}   {a['motors'][w]['rank']}")
    print()
    if a["ev"]["passed"]:
        print(f"推奨買い目（期待値1超・{len(a['ev']['passed'])}点・合成{a['ev']['synth_odds']}倍）:")
        for r in a["ev"]["passed"]:
            print(f"  {r['c']}  {r['odds']:>7.1f}倍  期待値{r['ev']:.2f}")
    else:
        print("期待値1超の買い目なし＝見送り")
    print(f"\n※爆速モードにつき詳細レポート（HTML）は未生成。フル版が要る時は texts.json→build.py まで回して")

if __name__ == "__main__":
    main()
