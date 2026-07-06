#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
フナヨミAI — motorbank.py（会場公式サイトのモーターデータ取得＋キャッシュ）

設計（2026-07-05 Sakiyomi AI Lab 設計）:
  - 予想時に各会場の公式サイト（C層）からモーターデータを取る
  - 会場×日付でキャッシュ（data/motor/{jcd}_{hd}.json）→ 同日は再利用・翌日は取り直し
  - 取れない場（サイト構造が未対応）は None を返し、engine側は従来のm2/m3のみで動く（安全なフォールバック）

対応パーサ:
  cms      … 共通CMS /modules/datafile/?page=index_mrankdtl（福岡・尼崎ほか多数場が同型）
             → モーター番号/節数/2連対率/勝率/事故率/1-3着/出走/優出/優勝/最高タイム
  gamagori … 蒲郡（通算＋近況5節の勝率・2連率 ＝ 原本番長AIのTrendに相当）
  suminoe  … 住之江（ランキング: 2連対率/勝率/優出/優勝）

使い方:
  python3 engine/motorbank.py --jcd 22            # 福岡の今日分を取得（キャッシュ済ならスキップ）
  python3 engine/motorbank.py --jcd 22 --force    # キャッシュ無視して取り直し
"""
import argparse, json, re, sys, unicodedata, urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "motor"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) funayomi/1.0"}

# 会場ごとの取得先。type=cms は共通CMS（ページ名が同じ）。正本＝設計書§8-10 C層の表。
VENUE_SOURCES = {
    "01": {"type": "cms2", "base": "https://www.kiryu-kyotei.com"},       # ページ名index_motorrank
    "02": {"type": "cms2", "base": "https://www.boatrace-toda.jp"},       # 未確認→cms2試行
    "03": {"type": "edogawa", "base": "https://www.boatrace-edogawa.com"},
    "04": {"type": "heiwajima", "base": "https://www.heiwajima.gr.jp"},
    "05": {"type": "cms", "base": "https://www.boatrace-tamagawa.com"},
    "06": {"type": "cms", "base": "https://www.boatrace-hamanako.jp"},
    "07": {"type": "gamagori", "base": "https://www.gamagori-kyotei.com"},
    "08": {"type": "cms", "base": "https://www.boatrace-tokoname.jp"},
    "09": {"type": "cms2", "base": "https://www.boatrace-tsu.com"},       # ページ名index_motorrank
    "10": {"type": "cms", "base": "https://www.boatrace-mikuni.jp"},
    "11": {"type": "cms2", "base": "https://www.boatrace-biwako.jp"},     # ページ名index_motorrank
    "12": {"type": "suminoe", "base": "https://www.boatrace-suminoe.jp"},
    "13": {"type": "cms", "base": "https://www.boatrace-amagasaki.jp"},
    "14": {"type": "cms", "base": "https://www.n14.jp"},
    "15": {"type": "cms2", "base": "https://www.marugameboat.jp"},        # 未確認→cms2試行
    "16": {"type": "cms2", "base": "https://www.kojimaboat.jp"},          # 未確認→cms2試行
    "17": {"type": "cms2", "base": "https://www.boatrace-miyajima.com"},  # 未確認→cms2試行
    "18": {"type": "cms2", "base": "https://www.boatrace-tokuyama.jp"},   # 未確認→cms2試行
    "19": {"type": "cms", "base": "https://www.boatrace-shimonoseki.jp"},
    "20": {"type": "cms", "base": "https://www.wmb.jp"},
    "21": {"type": "cms", "base": "https://www.boatrace-ashiya.com"},
    "22": {"type": "cms", "base": "https://www.boatrace-fukuoka.com"},
    "23": {"type": "cms2", "base": "https://www.boatrace-karatsu.jp"},    # 未確認→cms2試行
    "24": {"type": "omura", "base": "https://omurakyotei.jp"},
}


def _get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")


def _strip(html):
    t = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("&nbsp;", " ").replace("&yen;", "¥")
    t = unicodedata.normalize("NFKC", t)
    return re.sub(r"\s+", " ", t).strip()


# ---------- パーサ群（返り値: {motor_no: {...正規化スキーマ}}） ----------
def _parse_ranking_text(t):
    """ランキング形式: 順位 [(前節順位)] モーター番号 2連対率 勝率 優出 優勝（尼崎・住之江型）"""
    rows = re.findall(
        r"(?:^|\s)(\d{1,2})\s+(?:\(\d+\)\s+)?(\d{1,3})\s+(\d{1,2}\.\d{1,2})\s+(\d\.\d{2})\s+(\d+)\s+(\d+)(?=\s)", t)
    out = {}
    for _, no, w2, wr, yu, yv in rows:
        n = int(no)
        if 1 <= n <= 999 and float(w2) <= 100:
            out[n] = {"win2": float(w2), "win_rate": float(wr),
                      "starts": None, "top3_rate": None,
                      "yushutsu": int(yu), "yusho": int(yv), "best_time": None,
                      "recent_win2": None}
    return out


def parse_cms(base):
    """共通CMS: 詳細形式（番号/節数/2連対率/勝率/事故率/1-3着/出走/優出/優勝[/最高タイム]）
    → ダメならランキング形式（尼崎型）にフォールバック"""
    t = _strip(_get(f"{base}/modules/datafile/?page=index_mrankdtl"))
    rows = re.findall(
        r"(\d{1,3})\s+(\d{1,2})\s+(\d{1,2}\.\d{2})\s+(\d\.\d{2})\s+(\d\.\d{2})\s+"
        r"(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)(?:\s+(\d'\d{2}\"\d))?", t)
    out = {}
    for g in rows:
        no, setsu, w2, wr, acc, f1, f2, f3, starts, yu, yv, bt = g
        bt = bt or None
        starts_i = int(starts)
        top3 = (int(f1) + int(f2) + int(f3)) / starts_i * 100 if starts_i else None
        out[int(no)] = {"win2": float(w2), "win_rate": float(wr),
                        "starts": starts_i, "top3_rate": round(top3, 1) if top3 else None,
                        "yushutsu": int(yu), "yusho": int(yv), "best_time": bt,
                        "recent_win2": None}
    return out or _parse_ranking_text(t)


def parse_gamagori(base):
    """蒲郡: 順位 No 出走 通算勝率 通算2連率 近況5節勝率 近況5節2連率 優出 優勝（近況=Trend）"""
    top = _get(base)
    m = re.search(r'href="([^"]*01history_motor\d{4}\.htm)"', top)
    if not m:
        return None
    t = _strip(_get(m.group(1) if m.group(1).startswith("http") else base + m.group(1)))
    rows = re.findall(
        r"(\d{1,2})\s+(\d{1,3})\s+(\d{1,3})\s+(\d\.\d{2})\s+(\d{1,2}\.\d)\s+"
        r"(\d\.\d{2})\s+(\d{1,2}\.\d)\s+(\d+)\s+(\d+)", t)
    out = {}
    for g in rows:
        _, no, starts, wr, w2, r_wr, r_w2, yu, yv = g
        out[int(no)] = {"win2": float(w2), "win_rate": float(wr),
                        "starts": int(starts), "top3_rate": None,
                        "yushutsu": int(yu), "yusho": int(yv), "best_time": None,
                        "recent_win2": float(r_w2)}
    return out


def parse_suminoe(base):
    """住之江: 順位 (前回順位)? No 2連対率 勝率 優出 優勝"""
    t = _strip(_get(f"{base}/asp/suminoe/contents/01history/ranking_motor.php"))
    rows = re.findall(
        r"(?:^|\s)(\d{1,2})\s+(?:\(\d+\)\s+)?(\d{1,3})\s+(\d{1,2}\.\d)\s+(\d\.\d{2})\s+(\d+)\s+(\d+)(?=\s)", t)
    out = {}
    for g in rows:
        _, no, w2, wr, yu, yv = g
        out[int(no)] = {"win2": float(w2), "win_rate": float(wr),
                        "starts": None, "top3_rate": None,
                        "yushutsu": int(yu), "yusho": int(yv), "best_time": None,
                        "recent_win2": None}
    return out


def parse_heiwajima(base):
    """平和島: /01motor/01motor.htm
    形式: 順位 機番 今節使用者(登番+名前) 前検タイム 2連率 優出 優勝 [過去使用者の着順成績…]"""
    t = _strip(_get(f"{base}/01motor/01motor.htm"))
    rows = re.findall(
        r"(\d{1,2})\s+(\d{1,3})\s+(\d{4})\s+.{2,20}?\((?:A1|A2|B1|B2)\)\s+(\d\.\d{2})\s+(\d{1,3}\.\d)%\s+(\d+)\s+(\d+)", t)
    out = {}
    for _, no, _, _, w2, yu, yv in rows:
        n = int(no)
        if 1 <= n <= 99 and float(w2) <= 100:
            out[n] = {"win2": float(w2), "win_rate": None,
                      "starts": None, "top3_rate": None,
                      "yushutsu": int(yu), "yusho": int(yv), "best_time": None,
                      "recent_win2": None}
    return out or None


def parse_cms2(base):
    """共通CMS別名: /modules/datafile/?page=index_motorrank（桐生・津・びわこ等）
    形式: [順位 前節比] モーター番号 2連対率 勝率 優出 優勝 → ランキングパーサで吸える"""
    t = _strip(_get(f"{base}/modules/datafile/?page=index_motorrank"))
    return _parse_ranking_text(t) or None


def parse_edogawa(base):
    """江戸川: /modules/kouryaku/motor_seiseki.php
    形式: 番号(3桁) 算出期間 節数 2連対率 勝率 事故率 1〜6着 出走 優出 優勝 最高タイム"""
    t = _strip(_get(f"{base}/modules/kouryaku/motor_seiseki.php"))
    rows = re.findall(
        r"(\d{2,3})\s+[\d.]+~[\d.]+\s+(\d{1,2})\s+(\d{1,2}\.\d{2})\s+(\d\.\d{2})\s+(\d\.\d{2})\s+"
        r"(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", t)
    out = {}
    for g in rows:
        no, setsu, w2, wr, acc, f1, f2, f3, f4, f5, f6, starts, yu, yv = g
        starts_i = int(starts)
        top3 = (int(f1) + int(f2) + int(f3)) / starts_i * 100 if starts_i else None
        out[int(no)] = {"win2": float(w2), "win_rate": float(wr),
                        "starts": starts_i, "top3_rate": round(top3, 1) if top3 else None,
                        "yushutsu": int(yu), "yusho": int(yv), "best_time": None,
                        "recent_win2": None}
    return out or None


def parse_omura(base):
    """大村: /yosou/ranking_motor.php（モーター抽選結果）
    形式: 順位 登番 選手名 モーターNo 2連対率% ボートNo 2連対率% 前検タイム"""
    t = _strip(_get(f"{base}/yosou/ranking_motor.php"))
    rows = re.findall(
        r"(\d{1,2})\s+(\d{4})\s+\S+(?:\s\S+)?\s+(\d{1,3})\s+(\d{1,3}\.\d)%\s+(\d{1,3})\s+(\d{1,3}\.\d)%\s+(\d\.\d{2})", t)
    out = {}
    for _, _, no, w2, _, _, _ in rows:
        out[int(no)] = {"win2": float(w2), "win_rate": None,
                        "starts": None, "top3_rate": None,
                        "yushutsu": None, "yusho": None, "best_time": None,
                        "recent_win2": None}
    return out or None


PARSERS = {"cms": parse_cms, "cms2": parse_cms2, "gamagori": parse_gamagori,
           "suminoe": parse_suminoe, "heiwajima": parse_heiwajima,
           "edogawa": parse_edogawa, "omura": parse_omura}


# ---------- キャッシュAPI（engine/fetchから使う入口） ----------
def cache_path(jcd, hd):
    return CACHE / f"{jcd}_{hd}.json"


def load(jcd, hd):
    """キャッシュがあれば読む（無ければNone）。ネットワークには行かない。"""
    p = cache_path(jcd, hd)
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        return {int(k): v for k, v in d["motors"].items()} if d.get("ok") else None
    return None


def ensure(jcd, hd=None, force=False):
    """キャッシュ優先で会場モーターデータを確保。取得失敗はNone（フォールバック運用）。"""
    jcd = str(jcd).zfill(2)
    hd = hd or date.today().strftime("%Y%m%d")
    p = cache_path(jcd, hd)
    if p.exists() and not force:
        return load(jcd, hd)
    src = VENUE_SOURCES.get(jcd)
    if not src:
        return None
    try:
        motors = PARSERS[src["type"]](src["base"])
    except Exception:
        motors = None
    if not motors:
        return None                      # 失敗はキャッシュしない（次回リトライ）
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"ok": True, "jcd": jcd, "hd": hd, "source": src["base"],
         "type": src["type"], "count": len(motors),
         "motors": {str(k): v for k, v in motors.items()}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return motors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jcd", required=True)
    ap.add_argument("--hd", default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    jcd = a.jcd.zfill(2)
    hd = a.hd or date.today().strftime("%Y%m%d")
    cached = cache_path(jcd, hd).exists() and not a.force
    m = ensure(jcd, hd, force=a.force)
    if m is None:
        print(f"NG: {jcd} の会場モーターデータ取得失敗（engineはm2/m3のみで続行できる）", file=sys.stderr)
        sys.exit(1)
    src = VENUE_SOURCES[jcd]
    print(f"{'キャッシュ再利用' if cached else '新規取得'}: {jcd} {src['base']} ({src['type']}) モーター{len(m)}基 → {cache_path(jcd, hd)}")


if __name__ == "__main__":
    main()
