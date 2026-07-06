#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
フナヨミAI — build.py
確定UIテンプレ（template/report.html）に race.json + analysis.json + texts.json を注入して
レース専用HTMLを races/ に生成する。テンプレのデザインは一切変更しない。

置換するのは JS データブロック3箇所＋スコアカウンタ2箇所のみ。
表示の書き換えは末尾に注入する hydrate スクリプトが DOM 経由で行う。

使い方:
  python3 engine/build.py [--race work/race.json] [--analysis work/analysis.json]
                          [--texts work/texts.json] [--out races/auto]
texts.json（予想師AI=LLMが書く。無ければ自動文で代替＝崩れない）:
  {"summary": "...", "sumnote": "...", "per_lane_ai": {"1": "...", ...},
   "simu": "...", "judge": "...", "agents": {"course": "...", "player": "...",
   "motor": "...", "suji": "...", "tenji": "...", "are": "..."},
   "odds_note": "...", "before_note": "...", "venue_note": "..."}
"""
import argparse, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def jload(p, default=None):
    p = Path(p)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))

def js(v):
    return json.dumps(v, ensure_ascii=False)

# ---------------- P配列（テンプレの選手データ） ----------------
def build_players(race, analysis, texts):
    ps, mo, hp = analysis["player_scores"], analysis["motors"], analysis["head_probs"]
    marks = analysis["marks"]
    tenji = race["before"].get("tenji", {})
    est = race["before"].get("est", {})
    entry = race["before"].get("entry", [])
    course_of = {b: i + 1 for i, b in enumerate(entry)}  # 艇番→スタ展コース
    exs = {w: (tenji.get(str(w)) or tenji.get(w) or {}).get("ex") for w in range(1, 7)}
    valid_ex = [v for v in exs.values() if v]
    best_ex = min(valid_ex) if valid_ex else None
    best_zk = max(p["zk"] for p in race["players"])
    atk = set(analysis["areness"].get("attack_lanes", []))
    main_lane = min(atk) if atk else int(max(
        (w for w in hp if w != "1"), key=lambda w: hp[w], default="1"))
    out = []
    for p in sorted(race["players"], key=lambda x: x["w"]):
        w = p["w"]
        t = tenji.get(str(w)) or tenji.get(w) or {}
        e = est.get(str(w)) or est.get(w) or {}
        hi = []
        if mo[str(w)]["upper"]: hi.append("m2")
        if p["t2"] >= 60: hi.append("t2")
        if p["zk"] == best_zk and p["zk"] >= 6.5: hi.append("zk")
        if t.get("ex") and t["ex"] == best_ex: hi.append("ex")
        if e.get("st") is not None and 0 <= e["st"] <= 0.05: hi.append("est")
        ai = (texts.get("per_lane_ai") or {}).get(str(w)) or auto_lane_text(p, mo[str(w)], ps[str(w)], hp.get(str(w), 0))
        est_disp = e.get("raw") or "—"
        if est_disp.startswith("."): pass
        out.append({
            "w": w, "name": p["name"], "cls": p["cls"], "shibu": p["shibu"],
            "age": p["age"], "wt": f'{p["wt"]:.1f}', "fl": f'F{p["F"]} L{p["L"]}',
            "st": f'.{int(round(p["st"]*100)):02d}', "pst": f'.{int(round(p["st"]*100)):02d}',
            "zk": f'{p["zk"]:.2f}', "z2": f'{p["z2"]:.1f}', "z3": f'{p["z3"]:.1f}',
            "tk": f'{p["tk"]:.2f}', "t2": f'{p["t2"]:.1f}', "t3": f'{p["t3"]:.1f}',
            "mn": p["mn"], "m2": f'{p["m2"]:.1f}', "m3": f'{p["m3"]:.1f}',
            "mrank": mo[str(w)]["rank"], "mz": mo[str(w)]["z"],
            "bn": p["bn"], "b2": f'{p["b2"]:.1f}', "b3": f'{p["b3"]:.1f}',
            "ex": f'{t["ex"]:.2f}' if t.get("ex") else "—",
            "tilt": f'{t["tilt"]:+.1f}'.replace("+0.0", "0.0") if t.get("tilt") is not None else "—",
            "entry": course_of.get(w, w), "est": est_disp,
            "prob": int(round(hp.get(str(w), 0) * 100)),
            "main": w == main_lane, "hi": hi, "ai": ai,
        })
    return out, marks

def auto_lane_text(p, m, score, hp):
    bits = []
    bits.append(f'選手力{score:.0f}点・全国{p["zk"]:.2f}')
    if m["upper"]: bits.append(f'モーター2連率<b>{p["m2"]}%</b>は上端シグナル')
    elif m["lower"]: bits.append(f'モーター2連率{p["m2"]}%は下端＝機力に不安')
    else: bits.append(f'モーター{m["rank"]}（2連率{p["m2"]}%）')
    bits.append(f'AIの1着確率<b>{hp*100:.0f}%</b>')
    return "。".join(bits) + "。"

# ---------------- AILOG（ターミナル演出・実数から生成） ----------------
def build_ailog(race, analysis):
    m = race["meta"]
    cb = analysis["course_base"]
    ps, mo, ar = analysis["player_scores"], analysis["motors"], analysis["areness"]
    hp, ev, sc = analysis["head_probs"], analysis["ev"], analysis["scores"]
    names = {p["w"]: p["name"].split(" ")[0].split("　")[0] for p in race["players"]}
    K = "①②③④⑤⑥"
    def kj(w): return K[int(w)-1]
    top = max(hp, key=hp.get)
    order = sorted(ps, key=ps.get, reverse=True)
    L = []
    A = L.append
    A({"h": f'<span class="tl-p">$</span> funayomi analyze --race <b>{m["place"]}{m["rno"]}R</b> --engine v1.0 --hd {m["hd"]}', "cls": "cmd", "d": 150})
    A({"h": '▸ boatrace.jp 実測データ取得 .............. <span class="tl-ok">OK</span>', "d": 550})
    A({"h": '  ├ 出走表6艇・級別/勝率/フライング歴 ....... <span class="tl-ok">6/6</span>', "cls": "sub", "d": 120})
    w = race["before"]["weather"]
    A({"h": f'  ├ 直前情報取得（展示T・チルト・スタ展）／気象: {w.get("sky","—")}・風{w.get("wind_ms","?")}m・波{w.get("wave_cm","?")}cm', "cls": "sub", "d": 120})
    A({"h": f'  └ 3連単オッズ {len(race["odds"]["odds3t"])}点 取得 <span class="tl-dim">{race["odds"].get("odds_time") or ""}</span>', "cls": "sub", "d": 130})
    A({"h": '<span class="tl-dim">────────────────────────────</span>', "d": 280})
    A({"h": f'▸ コース分析 ................ <b class="tl-p">{sc["course"]}</b> <span class="tl-ok">✓</span>', "rev": "ag0", "d": 400})
    A({"h": f'  ├ {cb["place"]}1コース1着率 <b>{cb["p1"]}%</b>・性格ランク{cb["rank"]}（台帳実数）', "cls": "sub", "d": 120})
    A({"h": f'  └ ①{names.get(1,"")} 頭確率 <b>{float(hp.get("1",0))*100:.0f}%</b>（場基礎値×選手×相対ST）', "cls": "sub", "d": 130})
    A({"h": f'▸ 選手力分析 ............... <b class="tl-p">{sc["player"]}</b> <span class="tl-ok">✓</span>', "rev": "ag1", "d": 400})
    for wnum in order[:3]:
        A({"h": f'  ├ {kj(wnum)}{names.get(int(wnum),"")} 選手力<b>{ps[wnum]:.0f}</b>点', "cls": "sub", "d": 110})
    A({"h": f'▸ モーター分析 ............. <b class="tl-p">{sc["motor"]}</b>', "rev": "ag2", "d": 400})
    ups = [k for k, v in mo.items() if v["upper"]]; lows = [k for k, v in mo.items() if v["lower"]]
    A({"h": f'  ├ 上端シグナル: {("・".join(kj(u)+names.get(int(u),"") for u in ups)) or "なし"}／下端: {("・".join(kj(u) for u in lows)) or "なし"}', "cls": "sub", "d": 120})
    A({"h": '  └ 両端のみ補正採用（中間は棄却＝統計上の説明力最弱）', "cls": "sub", "d": 130})
    A({"h": f'▸ スジ分析 ................. <b class="tl-p">{sc["suji"]}</b> <span class="tl-ok">✓</span>', "rev": "ag3", "d": 400})
    A({"h": f'▸ 直前観察 ................. <b class="tl-p">{sc["tenji"]}</b> <span class="tl-ok">✓</span>', "rev": "ag4", "d": 400})
    A({"h": f'▸ 荒れ分析 ................. <b class="tl-p">{sc["are"]}</b> <span class="tl-dim">★{ar["star"]} {ar["decision"]}</span>', "rev": "ag5", "d": 400})
    for f in ar["flags"][:3]:
        A({"h": f'  ├ {f}', "cls": "sub", "d": 110})
    A({"h": '<span class="tl-dim">────────────────────────────</span>', "d": 300})
    A({"h": f'▸ 展開シミュレーション 1マーク ... <span class="tl-ok">{kj(top)}先マイ {float(hp[top])*100:.0f}%</span>', "rev": "ab-simu", "d": 450})
    A({"h": f'▸ 期待値ゲート照合 {len(ev["all"])}点走査 ....... <span class="tl-ok">{len(ev["passed"])}点通過</span> <span class="tl-dim">(期待値1超)</span>', "rev": "ab-odds", "d": 450})
    for r in ev["all"][:5]:
        mark = '<span class="tl-ok">✓通過</span>' if r["ev"] >= 1.0 else '<span class="tl-dim">✗棄却</span>'
        A({"h": f'  ├ {r["c"]} {r["odds"]}倍 × 推定{r["p"]*100:.1f}% ＝ 期待値<b>{r["ev"]:.2f}</b> {mark}', "cls": "sub", "d": 120})
    bets = " / ".join(r["c"] for r in ev["passed"]) or "なし"
    A({"h": f'▸ 推奨買い目を生成 ......... <b class="tl-p">{bets}</b>', "rev": "ab-bets", "d": 450})
    A({"h": '▸ 統合判断コンパイル ... <span class="tl-dim">6機能スコア加重＋期待値ゲート統合</span>', "rev": "ab-judge", "d": 450})
    A({"h": f'<span class="tl-fin">◎ 総合評価 {sc["total"]}/100 — 判定「{sc["verdict"]}」</span>', "rev": "verdict", "d": 700})
    A({"h": '<span class="tl-ok">✓ 分析完了</span> <span class="tl-dim">— フナヨミAI（boatrace.jp実測）</span>', "d": 420})
    A({"h": '<span class="tl-p">$</span> <span class="tcaret"></span>', "d": 380})
    return L

# ---------------- 自動テキスト（texts.json欠損時のフォールバック） ----------------
VERDICT_LINE = {
    "買い":     lambda n, syn: f'期待値1超が{n}点そろい合成{syn}倍＝根拠十分の「買い」。',
    "小口勝負": lambda n, syn: f'期待値1超は{n}点あるが決め手はやや弱い＝張るなら少額の「小口勝負」。',
    "見送り推奨": lambda n, syn: f'期待値はギリギリ立つが総合スコアが低く根拠が弱い＝「見送り推奨」。',
    "見送り":   lambda n, syn: ('荒れ度がNO-GO水準＝参加自体を見送るレース。' if n == 0 else f'期待値1超の買い目が実質なく「見送り」。'),
}

def auto_texts(race, analysis):
    m, cb = race["meta"], analysis["course_base"]
    sc, ar, ev = analysis["scores"], analysis["areness"], analysis["ev"]
    hp = analysis["head_probs"]; top = max(hp, key=hp.get)
    names = {p["w"]: p["name"].split(" ")[0].split("　")[0] for p in race["players"]}
    vline = VERDICT_LINE.get(sc["verdict"], VERDICT_LINE["見送り"])(len(ev["passed"]), ev["synth_odds"])
    return {
        "summary": f'{cb["place"]}1C{cb["p1"]}%（ランク{cb["rank"]}）。荒れ度★{ar["star"]}。{vline}',
        "sumnote": f'<b>{cb["place"]}・1C1着率{cb["p1"]}%（台帳実数）</b>。◎{top}号 {names.get(int(top),"")}を軸に期待値で買い目を絞る。行をタップで選手カルテが開く。',
        "simu": f'AIモデルの1マーク想定：{top}号の先マイ確率{float(hp[top])*100:.0f}%。荒れ度★{ar["star"]}（{ar["decision"]}）。検出フラグ＝{("／".join(ar["flags"])) or "特になし"}。',
        "judge": f'コース基礎値{cb["p1"]}%を土台に選手力・相対ST・モーター両端で補正。市場ブレンド後の期待値1超のみ通過（{len(ev["passed"])}点・合成{ev["synth_odds"]}倍）＝判定「{sc["verdict"]}」。',
        "agents": {}, "odds_note": None, "before_note": None, "venue_note": None,
        "per_lane_ai": {},
    }

# ---------------- hydrate（DOM書き換えスクリプト） ----------------
HYDRATE = r"""
<script>
(function(){
const D=window.DATA;if(!D)return;const M=D.meta,SC=D.scores;
const q=(s)=>document.querySelector(s);const qa=(s)=>document.querySelectorAll(s);
document.title=`フナヨミAI — ${M.place}${M.rno}R 分析ターミナル`;
const tt=q('.term-title');if(tt)tt.textContent=`funayomi@lab — 詳細分析ターミナル / ${M.place} ${M.rno}R`;
const bsec=q('#ab-bets .dw-sec');if(bsec)bsec.textContent=`推奨買い目 — 3連単 ${D.bets.length}点`;
// 判定4段階の見た目（文字数が伸びても崩れないようサイズ・色を動的に決める）
// 買い=強気の紫／小口勝負=控えめだが前向き／見送り推奨=warning寄り／見送り=グレーで明確に格下げ
const VERDICT_STYLE={
  '買い':      {color:'var(--acc-ink)', size1:46, size2:58},
  '小口勝負':   {color:'var(--acc-ink)', size1:32, size2:40},
  '見送り推奨': {color:'#C98A2E',        size1:28, size2:34},
  '見送り':    {color:'var(--ink3)',    size1:34, size2:42},
};
const vst=VERDICT_STYLE[SC.verdict]||VERDICT_STYLE['見送り'];
function setVerdict(el,size){if(!el)return;el.textContent=SC.verdict;el.style.fontSize=size+'px';el.style.color=vst.color;}
// topbar
q('.rhead .rt').innerHTML=`${M.place} <span class="rn">${M.rno}R</span>`;
q('.rhead .rs').textContent=`${M.title||''}｜締切 ${M.deadline||'—'}`;
// racestrip
q('.rs-big').innerHTML=`${M.place} <span class="viotext">${M.rno}R</span>`;
q('.rs-sub').textContent=`${M.title||''} ${M.distance||''}m｜${M.date}${M.deadline?' 締切 '+M.deadline:''}`;
const badges=q('.rs-name > div:last-child');
badges.innerHTML=M.badges.map((b,i)=>`<span class="badge${i==0?' acc':''}">${b}</span>`).join('');
const kv=qa('.rs-kv > div');const KV=D.kv;
const kvPairs=[['天候/風',KV.weather],['波高',KV.wave],['水面',KV.water],['直前情報',KV.before,1],['オッズ',KV.odds,1],['エンジン',KV.engine]];
kvPairs.forEach((p,i)=>{if(kv[i])kv[i].innerHTML=`<span class="k">${p[0]}</span><span class="v${p[2]?' ok':''}${p[0]=='エンジン'?' mono':''}">${p[1]}</span>`});
// sumnote
q('.sumnote').innerHTML=D.texts.sumnote;
// aicard hero
const ring=q('.aicard .hs-ring');if(ring){ring.style.setProperty('--target',SC.total+'%');}
setVerdict(q('.hero-side .w'),vst.size1);
q('.hero-side .s').innerHTML=D.texts.summary.replace(/。/g,'。<br>').replace(/<br>$/,'');
// scorebars
const sbData=[['コース',SC.course],['選手',SC.player],['モーター',SC.motor],['スジ',SC.suji],['展示',SC.tenji],['荒れ度',SC.are]];
qa('.aicard .sb').forEach((el,i)=>{if(!sbData[i])return;el.querySelector('.sbk').textContent=sbData[i][0];el.querySelector('.sbf').style.setProperty('--sw',sbData[i][1]+'%');el.querySelector('.sbv').textContent=sbData[i][1];});
// yomirow
const MKC={'◎':'mk-o','◯':'mk-w','▲':'mk-s','△':'mk-c'};
q('.yomirow').innerHTML=Object.entries(D.marks).map(([w,mk])=>`<span class="yomi"><b class="mk ${MKC[mk]}">${mk}</b><span class="wk wk${w} sm">${w}</span>${D.names[w]}</span>`).join('')+(D.kill.length?`<span class="yomi mute"><b class="mk">消</b>${D.kill.join('・')}</span>`:'');
// 推奨買い目
q('.subh').innerHTML=`推奨買い目 — 3連単 ${D.bets.length}点<span style="color:var(--ink4);font-weight:400;margin-left:6px">タップで買い目メモに追加/解除</span>`;
q('.reclist').innerHTML=D.bets.map((b,i)=>`<div class="rec${i==0?' top':''}" data-combo="${b.c}" data-odds="${b.odds}" onclick="event.stopPropagation();toggleBet(this)"><span class="rc">${b.c}</span><span class="hlbl2 ${i==0?'h3':'h2'}">${i==0?'PERFECT':'GOOD'}</span><span class="rodds">${b.odds}<small>倍</small></span><span class="rev"><small>期待値</small>${b.ev.toFixed(2)}</span></div>`).join('')||'<div class="memo-empty">期待値1超の買い目なし＝このレースは見送り</div>';
// モーダル
q('#ab-verdict .hs-in .n').textContent='0';
setVerdict(q('#ab-verdict div:last-child > div:first-child'),vst.size2);
q('#ab-verdict div:last-child > div:last-child').textContent=D.texts.summary;
const ring2=q('#ab-verdict .hs-ring');if(ring2)ring2.style.background=`conic-gradient(var(--acc) ${SC.total}%,var(--track) 0)`;
q('#ab-verdict .hs-in .c').textContent='/ 100';
const agentTexts=D.texts.agents||{};
const agDefs=[['コース分析',SC.course,agentTexts.course||`${D.course_base.place}1C ${D.course_base.p1}%・性格ランク${D.course_base.rank}（台帳実数）。`],
 ['選手力分析',SC.player,agentTexts.player||D.autoAgents.player],
 ['モーター分析'+(D.autoAgents.motorMark||''),SC.motor,agentTexts.motor||D.autoAgents.motor],
 ['スジ分析',SC.suji,agentTexts.suji||D.autoAgents.suji],
 ['直前観察',SC.tenji,agentTexts.tenji||D.autoAgents.tenji],
 ['荒れ分析',SC.are,agentTexts.are||D.autoAgents.are]];
qa('#ab-agents .al').forEach((el,i)=>{const d=agDefs[i];if(!d)return;el.querySelector('.aln').textContent=d[0];el.querySelector('.als').textContent=d[1];el.querySelector('.alt').innerHTML=d[2];});
q('#ab-simu .simu p').innerHTML=D.texts.simu;
q('#ab-odds > div:last-child').innerHTML=D.texts.odds_note;
q('#ab-bets .betstack').innerHTML=D.bets.map((b,i)=>`<div class="bet ${i==0?'perfect fire':'good'}" style="padding:${i==0?14:13}px 20px"><span class="combo" style="font-size:${i==0?27:24}px">${b.c}</span><span class="hlbl">${i==0?'PERFECT':'GOOD'}</span><span class="ev" style="font-size:12px">期待値${b.ev.toFixed(2)}｜${b.odds}倍</span></div>`).join('');
q('#ab-judge .dw-ai').innerHTML='<b>統合判断：</b>'+D.texts.judge;
// メモsub
const msub=document.getElementById('msub');if(msub)msub.textContent=`${M.place} ${M.rno}R｜3連単 ${D.bets.length}点`;
// 出走表タブ（venue note）
const vnote=q('#p-card .simu p');if(vnote&&D.texts.venue_note)vnote.innerHTML=D.texts.venue_note;
// 展開タブ
if(D.tabs&&D.tabs.tenkai){const T=D.tabs.tenkai;
 const eb=q('#p-dev .expbar');if(eb){eb.innerHTML=`<i style="width:${T.nige}%">逃げ ${T.nige}%</i><span class="avg" style="left:${T.nige_avg}%"></span><span class="lbl">場平均 ${T.nige_avg}%</span>`;}
 const ebn=q('#p-dev .expbar + div');if(ebn)ebn.innerHTML=T.note;
 const t1=qa('#p-dev .panel')[0].querySelector('.dt');
 if(t1)t1.innerHTML=`<tr><th class="l">枠</th><th>1着率</th><th>選手力</th><th>モーター</th><th>相対ST</th></tr>`+T.rows.map(r=>`<tr class="rw${r.hot?' hot':''}"><td class="l"><span class="wk wk${r.w} sm">${r.w}</span> ${r.name}</td><td class="num${r.hot?' hi':''}"><b>${r.p1}%</b></td><td class="num">${r.score}</td><td class="num">${r.mrank}</td><td class="num">${r.dst}</td></tr>`).join('');
 const p2=qa('#p-dev .panel')[1];
 p2.querySelector('.sec-h .label').textContent=`先頭艇別2着率 — スジ読み（AIモデル）`;
 p2.querySelector('.sec-h .cnt').textContent=`${T.top}号 ${T.topname} 先頭時`;
 const t2=p2.querySelectorAll('.dt')[0];
 t2.innerHTML=`<tr><th class="l">枠</th><th class="l" style="padding-left:14px">2着率 →</th><th>2着率</th></tr>`+T.suji.map((s,i)=>`<tr class="rw${i==0?' hot':''}"><td class="l"><span class="wk wk${s.w} sm">${s.w}</span> ${s.name}</td><td class="l" style="padding-left:14px"><span class="wr-bar" style="max-width:150px"><i class="wr-${i<2?i+1:(i<3?3:'o')}" style="width:${Math.min(100,s.p*2.2)}%">${s.p}%</i></span></td><td class="num${i==0?' hi':''}">${s.p}%</td></tr>`).join('');
 const note2=p2.querySelector('.dt + div');if(note2)note2.innerHTML=T.suji_note;
 const t3=p2.querySelectorAll('.dt')[1];
 if(t3)t3.innerHTML=`<tr><th class="l">枠</th><th>平均ST</th><th>展示ST</th></tr>`+T.strows.map(r=>`<tr class="rw"><td class="l"><span class="wk wk${r.w} sm">${r.w}</span> ${r.name}</td><td class="num${r.hi?' hi':''}">${r.st}</td><td class="num">${r.est}</td></tr>`).join('');
}
// モーターtabの注記
const mnote=q('#p-motor .panel > div:last-child');if(mnote&&D.texts.motor_note)mnote.innerHTML=D.texts.motor_note;
// 直前情報タブ
const bnote=q('#p-before .panel:first-child > div:last-child');if(bnote&&D.texts.before_note)bnote.innerHTML=D.texts.before_note;
if(D.tabs&&D.tabs.before){const B=D.tabs.before;
 const kvw=qa('#p-before .kv2 > div');const wp=[['天候',B.sky],['気温',B.temp],['水温',B.wtemp],['風速',B.wind],['波高',B.wave],['水質',B.water]];
 wp.forEach((p,i)=>{if(kvw[i])kvw[i].innerHTML=`<span class="k">${p[0]}</span><span class="v">${p[1]}</span>`});
 const bars=qa('#p-before .dw-bar');
 B.bars.forEach((b,i)=>{if(!bars[i])return;bars[i].querySelector('.bk').textContent=b.k;bars[i].querySelector('.bf').style.width=b.w+'%';bars[i].querySelector('.bv').textContent=b.v;});
 const tot=q('#p-before .panel:last-child [style*="font-family"]');
 const totEl=qa('#p-before span.mono, #p-before span')[0];
 const starEl=q('#p-before div[style*="justify-content:space-between"] span:last-child');
 if(starEl)starEl.innerHTML=`★${B.star} <span style="font-size:13px;color:var(--ink3)">/ ${B.score}</span>`;
 const anote=q('#p-before .panel:last-child > div:last-child');if(anote)anote.innerHTML=B.note;
}
// オッズタブ
if(D.tabs&&D.tabs.odds){const O=D.tabs.odds;
 const t1=qa('#p-odds .panel')[0].querySelector('.dt');
 t1.innerHTML=`<tr><th class="l">枠</th><th>市場支持率</th><th>AI予測</th><th>期待値</th></tr>`+O.lanes.map(r=>`<tr class="rw${r.under?' hot':''}"><td class="l"><span class="wk wk${r.w} sm">${r.w}</span> ${r.name}</td><td class="num">${r.mkt}%</td><td class="num${r.under?' hi':''}">${r.ai}%</td><td><span class="tag2 ${r.under?'tag-under':'tag-over'}">${r.under?'過小=買い':'過大'}</span></td></tr>`).join('');
 const n1=qa('#p-odds .panel')[0].querySelector('.dt + div');if(n1)n1.innerHTML=O.note1;
 const t2=qa('#p-odds .panel')[1].querySelector('.dt');
 t2.innerHTML=`<tr><th class="l">順位</th><th class="l">組番</th><th>オッズ</th><th>期待値</th></tr>`+O.pop.map((r,i)=>`<tr class="rw${r.pass?' hot':''}" data-combo="${r.c}" data-odds="${r.odds}" onclick="toggleBet(this)"><td class="l num">${i+1}</td><td class="l num" style="font-weight:700">${r.c}</td><td class="num">${r.odds}</td><td class="num${r.pass?' hi':''}">${r.ev.toFixed(2)}</td></tr>`).join('');
 const n2=qa('#p-odds .panel')[1].querySelector('.dt + div');if(n2)n2.innerHTML=O.note2;
}
// footer
q('.footer span:first-child').textContent=`KYOTEI AI LAB engine v1.0｜${M.place}${M.rno}R｜データ: boatrace.jp ${M.date} 実測`;
// 買い目メモ同期＆テーブル選択状態
if(typeof renderMemo==='function'){renderMemo();}
if(typeof syncRows==='function'){syncRows();}
if(typeof updateFab==='function'){updateFab();}
})();
</script>
"""

def build_tabs(race, analysis):
    ps, mo, hp = analysis["player_scores"], analysis["motors"], analysis["head_probs"]
    ar, ev, cb = analysis["areness"], analysis["ev"], analysis["course_base"]
    names = {p["w"]: p["name"].split(" ")[0].split("　")[0] for p in race["players"]}
    est = race["before"].get("est", {})
    st1 = next(p["st"] for p in race["players"] if p["w"] == 1)
    top = int(max(hp, key=hp.get))
    # スジ2着率（engine側と同じ重みを簡易再現）
    SUJI2 = {1:{2:32,3:18,4:22,5:17,6:9},2:{1:42,3:22,4:14,5:12,6:10},3:{4:30,5:24,1:22,2:14,6:10},
             4:{5:32,3:20,1:20,2:14,6:14},5:{4:30,6:22,1:20,3:16,2:12},6:{5:28,4:24,1:20,2:14,3:14}}
    strength = {w: 0.5 + ps[str(w)] / 100 for w in range(1, 7)}
    w2 = {j: SUJI2[top][j] * strength[j] for j in range(1, 7) if j != top}
    s2 = sum(w2.values())
    suji = sorted(({"w": j, "name": names[j], "p": round(w2[j]/s2*100, 1)} for j in w2), key=lambda x: -x["p"])
    rows = []
    for p in sorted(race["players"], key=lambda x: -hp.get(str(x["w"]), 0)):
        w = p["w"]
        d = st1 - p["st"]
        rows.append({"w": w, "name": names[w], "p1": round(hp[str(w)]*100),
                     "score": f'{ps[str(w)]:.0f}', "mrank": mo[str(w)]["rank"],
                     "dst": (f'{d:+.2f}' if w != 1 else '基準'), "hot": w == top})
    strows = sorted(({"w": p["w"], "name": names[p["w"]],
                      "st": f'.{int(round(p["st"]*100)):02d}',
                      "est": (est.get(str(p["w"])) or est.get(p["w"]) or {}).get("raw") or "—",
                      "hi": p["st"] == min(x["st"] for x in race["players"])}
                     for p in race["players"]), key=lambda r: r["st"])
    # オッズタブ: 市場の頭支持率 vs AI
    odds = race["odds"]["odds3t"]
    mkt = {w: 0.0 for w in range(1, 7)}
    for c, o in odds.items():
        if o > 0: mkt[int(c.split("-")[0])] += 0.75 / o
    smkt = sum(mkt.values()) or 1
    lanes = []
    for w in sorted(mkt, key=mkt.get, reverse=True):
        mv, av = mkt[w]/smkt*100, hp.get(str(w), 0)*100
        lanes.append({"w": w, "name": names[w], "mkt": round(mv, 1), "ai": round(av, 1),
                      "under": av > mv * 1.1})
    passed_set = {r["c"] for r in ev["passed"]}
    pop = sorted(({"c": c, "odds": o} for c, o in odds.items()), key=lambda r: r["odds"])[:6]
    evmap = {r["c"]: r["ev"] for r in ev["all"]}
    for r in pop:
        r["ev"] = evmap.get(r["c"], 0)
        r["pass"] = r["c"] in passed_set
    w = race["before"]["weather"]
    are_bars = [
        {"k": f'風（{w.get("wind_ms","?")}m）', "w": min(100, (w.get("wind_ms") or 0)*16), "v": f'+{2.2 if (w.get("wind_ms") or 0)>=5 else (1.2 if (w.get("wind_ms") or 0)>=4 else 0.1):.1f}'},
        {"k": f'波（{w.get("wave_cm","?")}cm）', "w": min(100, (w.get("wave_cm") or 0)*10), "v": f'+{0.8 if (w.get("wave_cm") or 0)>=5 else 0.0:.1f}'},
        {"k": "攻撃筋", "w": 40 if ar["attack_lanes"] else 0, "v": f'+{2.0 if ar["attack_lanes"] else 0.0:.1f}'},
        {"k": "進入・級別", "w": min(100, max(0, (ar["score"]-2)*18)), "v": f'{ar["score"]:.1f}計'},
    ]
    return {
        "tenkai": {"nige": round(hp[str(top)]*100 if top == 1 else hp["1"]*100),
                   "nige_avg": round(cb["p1"]),
                   "note": f'AIの{top}号先頭確率と場平均（台帳{cb["p1"]}%）の比較。フラグ: {"／".join(ar["flags"][:3]) or "なし"}',
                   "rows": rows, "top": top, "topname": names[top], "suji": suji,
                   "suji_note": f'{top}号先頭時の2着率モデル（スジ重み×選手力）。上位スジ＝<b>{top}-{suji[0]["w"]}</b>・{top}-{suji[1]["w"]}。',
                   "strows": strows},
        "before": {"sky": w.get("sky", "—"), "temp": f'{w.get("temp","—")}℃', "wtemp": f'{w.get("water_temp","—")}℃',
                   "wind": f'{w.get("wind_ms","—")}m', "wave": f'{w.get("wave_cm","—")}cm', "water": "—",
                   "bars": are_bars, "star": ar["star"], "score": f'{ar["score"]:.1f}',
                   "note": f'荒れ度★{ar["star"]}（{ar["decision"]}）。{("・".join(ar["flags"])) or "荒れ要素は薄い"}。'},
        "odds": {"lanes": lanes,
                 "note1": '市場支持率＝3連単オッズから逆算した頭の人気。AI予測が上回る艇＝<b style="color:var(--pos)">過小評価（買い）</b>。',
                 "pop": pop,
                 "note2": f'<b style="color:var(--ink3)">行をタップで買い目メモに追加/解除。</b>期待値1超のみ通過（{len(ev["passed"])}点・合成{ev["synth_odds"]}倍）。'},
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--race", default=str(ROOT / "work" / "race.json"))
    ap.add_argument("--analysis", default=str(ROOT / "work" / "analysis.json"))
    ap.add_argument("--texts", default=str(ROOT / "work" / "texts.json"))
    ap.add_argument("--out", default="auto")
    a = ap.parse_args()
    race = jload(a.race)
    analysis = jload(a.analysis)
    texts = jload(a.texts, {}) or {}
    base = auto_texts(race, analysis)
    for k, v in base.items():
        texts.setdefault(k, v)
    m, sc, ar, ev, cb = race["meta"], analysis["scores"], analysis["areness"], analysis["ev"], analysis["course_base"]

    players, marks = build_players(race, analysis, texts)
    names = {str(p["w"]): p["name"].split(" ")[0].split("　")[0] for p in race["players"]}
    bets = [{"c": r["c"], "odds": r["odds"], "ev": r["ev"], "p": r["p"]} for r in ev["passed"]]
    date_s = f'{m["hd"][:4]}-{m["hd"][4:6]}-{m["hd"][6:]}'
    all_cls = {p["cls"] for p in race["players"]}
    badges = [f'{cb["rank"]}ランク（1C{cb["p1"]}%）']
    badges.append("全員A1" if all_cls == {"A1"} else "混合戦")
    badges.append(f'荒れ度★{ar["star"]}')
    ups = [k for k, v in analysis["motors"].items() if v["upper"]]
    auto_agents = {
        "player": "選手力上位: " + "・".join(f'{w}号{names[w]}({analysis["player_scores"][w]:.0f})' for w in sorted(analysis["player_scores"], key=analysis["player_scores"].get, reverse=True)[:3]) + "。",
        "motor": (f'上端シグナル: {"・".join(w+"号" for w in ups)}（2連率40%超）。両端のみ補正採用。' if ups else "上端・下端シグナルなし＝モーター差は無視できる帯。"),
        "motorMark": " ▲" if ups else "",
        "suji": f'{max(analysis["head_probs"], key=analysis["head_probs"].get)}号先頭時のスジをモデル化。2着候補は展開タブ参照。',
        "tenji": ("展示T・スタ展取得済み。" if race["before"]["published"] else "直前情報未公開のまま生成（参考値）。"),
        "are": f'★{ar["star"]}（{ar["decision"]}）。' + ("／".join(ar["flags"][:2]) if ar["flags"] else "荒れ要素薄い。"),
    }
    if not texts.get("odds_note"):
        texts["odds_note"] = f'市場ブレンド後の期待値1超は<b class="mono" style="color:var(--acc-ink)">{"・".join(b["c"] for b in bets) or "なし"}</b>（合成{ev["synth_odds"]}倍）。<br><span style="font-size:10.5px;color:var(--ink4)">※オッズ{race["odds"].get("odds_time") or ""}取得。締切まで変動（パリミュチュエル方式）。</span>'
    if not texts.get("motor_note"):
        texts["motor_note"] = auto_agents["motor"]
    data = {
        "meta": {"place": m["place"], "rno": m["rno"], "title": m["title"], "distance": m["distance"],
                 "deadline": m["deadline"], "date": date_s, "badges": badges},
        "kv": {"weather": f'{race["before"]["weather"].get("sky","—")}・{race["before"]["weather"].get("wind_ms","?")}m',
               "wave": f'{race["before"]["weather"].get("wave_cm","?")}cm',
               "water": f'水温{race["before"]["weather"].get("water_temp","?")}℃',
               "before": ("✓ 取得済" if race["before"]["published"] else "未公開"),
               "odds": f'✓ {race["odds"].get("odds_time") or "取得済"}', "engine": "v1.0"},
        "names": names, "marks": marks, "kill": analysis["kill"],
        "scores": sc, "bets": bets, "course_base": cb,
        "texts": texts, "autoAgents": auto_agents,
        "tabs": build_tabs(race, analysis),
    }

    tpl = (ROOT / "template" / "report.html").read_text(encoding="utf-8")
    # ① P配列
    tpl = re.sub(r"const P=\[.*?\n\];", "const P=" + js(players) + ";", tpl, count=1, flags=re.S)
    # ② AILOG
    ailog = build_ailog(race, analysis)
    tpl = re.sub(r"const AILOG=\[.*?\n\];", "const AILOG=" + js(ailog) + ";", tpl, count=1, flags=re.S)
    # ③ 買い目メモ初期選択
    seed = js([{"c": b["c"], "o": b["odds"]} for b in bets])
    tpl = re.sub(r"\[\{c:'1-6-2',o:54\.1\},\{c:'1-5-2',o:21\.4\},\{c:'1-4-2',o:20\.4\}\]",
                 seed, tpl, count=1)
    # ④⑤ スコアカウンタの82をtotalへ
    tpl = tpl.replace("c>=82){c=82", f"c>={sc['total']}){{c={sc['total']}", )
    # ⑥ DATA＋hydrate注入
    inject = f'<script>window.DATA={js(data)};</script>\n{HYDRATE}\n</body>'
    tpl = tpl.replace("</body>", inject, 1)

    if a.out == "auto":
        outp = ROOT / "races" / f'{date_s}_{m["place"]}{m["rno"]}R.html'
    else:
        outp = Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(tpl, encoding="utf-8")
    print(f'✅ {outp}  総合{sc["total"]}[{sc["verdict"]}] 買い目{len(bets)}点')

if __name__ == "__main__":
    main()
