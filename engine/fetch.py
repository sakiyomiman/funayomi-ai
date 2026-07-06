#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
サキヨミAI — fetch.py
boatrace.jp から 出走表・直前情報・3連単オッズ を取得して race.json に整形する。
標準ライブラリのみ（pip不要・配布前提）。

使い方:
  python3 engine/fetch.py --jcd 12 --rno 12 [--hd 20260704]
出力:
  work/race.json  （直前情報が未公開なら gate:"WAIT" を返して終了コード2）
"""
import argparse, json, re, sys, unicodedata, urllib.request
from datetime import date
from pathlib import Path

BASE = "https://www.boatrace.jp/owpc/pc/race"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) funayomi/1.0"}
ROOT = Path(__file__).resolve().parent.parent

VENUES = {
 "01":"桐生","02":"戸田","03":"江戸川","04":"平和島","05":"多摩川","06":"浜名湖",
 "07":"蒲郡","08":"常滑","09":"津","10":"三国","11":"びわこ","12":"住之江",
 "13":"尼崎","14":"鳴門","15":"丸亀","16":"児島","17":"宮島","18":"徳山",
 "19":"下関","20":"若松","21":"芦屋","22":"福岡","23":"唐津","24":"大村"}

def get(page, jcd, rno, hd):
    url = f"{BASE}/{page}?rno={rno}&jcd={jcd}&hd={hd}"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")

def strip(html_seg):
    t = re.sub(r"<[^>]+>", " ", html_seg)
    t = t.replace("&nbsp;", " ")
    t = unicodedata.normalize("NFKC", t)
    return re.sub(r"\s+", " ", t).strip()

def fnum(s):
    try: return float(s)
    except (TypeError, ValueError): return None

# ---------------- racelist ----------------
def parse_racelist(h):
    out = {"players": [], "title": None, "distance": None, "deadlines": []}
    m = re.search(r'heading2_titleName[^>]*>([^<]+)', h)
    if m: out["title"] = strip(m.group(1))
    m = re.search(r'(\d{3,4})m', h)
    if m: out["distance"] = int(m.group(1))
    tbs = re.findall(r"<tbody.*?</tbody>", h, re.S)
    if tbs:
        out["deadlines"] = re.findall(r"\d{1,2}:\d{2}", strip(tbs[0]))
    for tb in tbs[1:7]:
        t = strip(tb)
        # 例: "1 3435 / A2 寺田 千恵 岡山/福岡 57歳/45.5kg F0 L0 0.15 6.71 46.60 66.99 0.00 0.00 0.00 33 28.07 38.60 35 29.09 49.09 ..."
        m = re.match(
            r"^(\d)\s+(\d{4})\s*/\s*(A1|A2|B1|B2)\s+(\S+(?:\s\S+)?)\s+(\S+?)/(\S+?)\s+"
            r"(\d+)歳/([\d.]+)kg\s+F(\d+)\s+L(\d+)\s+([\d.]+)\s+"
            r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+"
            r"(\d+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)", t)
        if not m:
            continue
        g = m.groups()
        out["players"].append({
            "w": int(g[0]), "regno": g[1], "cls": g[2], "name": g[3],
            "shibu": g[4], "birth": g[5], "age": int(g[6]), "wt": float(g[7]),
            "F": int(g[8]), "L": int(g[9]), "st": float(g[10]),
            "zk": float(g[11]), "z2": float(g[12]), "z3": float(g[13]),
            "tk": float(g[14]), "t2": float(g[15]), "t3": float(g[16]),
            "mn": int(g[17]), "m2": float(g[18]), "m3": float(g[19]),
            "bn": int(g[20]), "b2": float(g[21]), "b3": float(g[22]),
        })
    return out

# ---------------- beforeinfo ----------------
def parse_beforeinfo(h):
    out = {"weather": {}, "tenji": {}, "entry": [], "est": {}, "published": False}
    # 気象
    blk = re.findall(r'<div class="weather1_bodyUnit[^"]*">(.*?)</div>\s*</div>', h, re.S)
    txt = " / ".join(strip(b) for b in blk)
    m = re.search(r"気温\s*([\d.\-]+)", txt);  out["weather"]["temp"] = fnum(m.group(1)) if m else None
    m = re.search(r"風速\s*([\d.]+)", txt);   out["weather"]["wind_ms"] = fnum(m.group(1)) if m else None
    m = re.search(r"水温\s*([\d.]+)", txt);   out["weather"]["water_temp"] = fnum(m.group(1)) if m else None
    m = re.search(r"波高\s*([\d.]+)", txt);   out["weather"]["wave_cm"] = fnum(m.group(1)) if m else None
    for w in ("晴", "曇り", "雨", "雪", "霧"):
        if w in txt: out["weather"]["sky"] = w.rstrip("り"); break
    # 風向（アイコンclass is-windN: 1-16方位。7-11=向かい系/1-3,15-16=追い系 ざっくり）
    m = re.search(r'is-wind(\d+)', h)
    out["weather"]["wind_icon"] = int(m.group(1)) if m else None
    # 展示タイム・チルト（枠別tbody）
    tbs = re.findall(r"<tbody.*?</tbody>", h, re.S)
    for tb in tbs:
        t = strip(tb)
        m = re.match(r"^([1-6])\s+\S+(?:\s\S+)?\s+([\d.]+)kg\s+([\d.]+)?\s*(-?[\d.]+)?", t)
        if m and m.group(3):
            out["tenji"][int(m.group(1))] = {"ex": fnum(m.group(3)), "tilt": fnum(m.group(4))}
    # スタート展示（進入順＋展示ST）
    seg = re.search(r"スタート展示.*?</table>", h, re.S)
    if seg:
        s = seg.group(0)
        nums = re.findall(r"boatImage1Number[^>]*is-type(\d)", s)
        sts  = re.findall(r"boatImage1Time[^>]*>\s*([F.L\-\d]+)", s)
        out["entry"] = [int(n) for n in nums]
        for i, n in enumerate(out["entry"]):
            raw = sts[i] if i < len(sts) else ""
            v = fnum(raw.replace("F", "-").replace("L", "1").lstrip("."))
            if raw.startswith("F"):   st = -fnum(raw[1:].lstrip(".") or "0")/ (100 if "." not in raw[1:] else 1)
            m2 = re.match(r"^(F?)\.?(\d+)$", raw)
            if m2:
                st = int(m2.group(2)) / 100.0
                if m2.group(1) == "F": st = -st
            else:
                st = None
            out["est"][n] = {"raw": raw, "st": st}
    out["published"] = bool(out["tenji"]) and any(v["ex"] for v in out["tenji"].values())
    return out

# ---------------- odds3t ----------------
def parse_odds3t(h):
    """3連単120通り {\"1-2-3\": 13.2, ...}。欠場等で '欠' はスキップ。"""
    odds = {}
    m = re.search(r"<tbody.*?</tbody>\s*</table>", h, re.S)
    tbs = re.findall(r"<tbody.*?</tbody>", h, re.S)
    body = max(tbs, key=len) if tbs else ""
    rows = re.findall(r"<tr.*?</tr>", body, re.S)
    second = [None]*6  # 各列(1着=idx+1)の現在の2着
    for tr in rows:
        tds = re.findall(r"<td[^>]*>.*?</td>", tr, re.S)
        col = 0
        i = 0
        while i < len(tds) and col < 6:
            cell = strip(tds[i])
            if 'rowspan' in tds[i]:
                second[col] = int(cell); i += 1
                third = strip(tds[i]); i += 1
                val = strip(tds[i]); i += 1
            else:
                third = cell; i += 1
                val = strip(tds[i]); i += 1
            v = fnum(val.replace(",", ""))
            if third.isdigit() and second[col] and v is not None:
                odds[f"{col+1}-{second[col]}-{third}"] = v
            col += 1
    # 更新時刻
    upd = None
    m = re.search(r"オッズ更新時間[^0-9]*(\d{1,2}:\d{2})", strip(h))
    if m: upd = m.group(1)
    return {"odds3t": odds, "odds_time": upd}

# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jcd", required=True)
    ap.add_argument("--rno", required=True, type=int)
    ap.add_argument("--hd", default=date.today().strftime("%Y%m%d"))
    ap.add_argument("--out", default=str(ROOT / "work" / "race.json"))
    ap.add_argument("--force", action="store_true", help="直前情報ゲートを無視")
    a = ap.parse_args()
    jcd = a.jcd.zfill(2)

    rl = parse_racelist(get("racelist", jcd, a.rno, a.hd))
    if len(rl["players"]) != 6:
        print(f"NG: 出走表パース失敗（{len(rl['players'])}艇）。URLか日付を確認して", file=sys.stderr)
        sys.exit(1)
    bi = parse_beforeinfo(get("beforeinfo", jcd, a.rno, a.hd))
    od = parse_odds3t(get("odds3t", jcd, a.rno, a.hd))

    race = {
        "meta": {
            "jcd": jcd, "place": VENUES.get(jcd, jcd), "rno": a.rno, "hd": a.hd,
            "title": rl["title"], "distance": rl["distance"],
            "deadline": rl["deadlines"][a.rno-1] if len(rl["deadlines"]) >= a.rno else None,
        },
        "players": rl["players"],
        "before": bi,
        "odds": od,
        "gate": "OK" if bi["published"] else "WAIT",
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(race, ensure_ascii=False, indent=1), encoding="utf-8")
    n_odds = len(od["odds3t"])
    print(f"gate={race['gate']} 6艇OK 展示={'あり' if bi['published'] else 'まだ'} オッズ{n_odds}点 → {a.out}")
    # 会場公式モーターデータを確保（会場×日付でキャッシュ済なら通信なし・失敗しても予想は続行）
    try:
        import motorbank
        mb = motorbank.ensure(jcd, a.hd)
        print(f"会場モーターデータ: {'OK('+str(len(mb))+'基)' if mb else '未対応場→m2/m3のみで続行'}")
    except Exception as e:
        print(f"会場モーターデータ: 取得スキップ({type(e).__name__})", file=sys.stderr)
    if race["gate"] == "WAIT" and not a.force:
        print("⚠️ 直前情報が未公開。発走30〜60分前に再実行して（鉄則ゲート）", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
