#!/usr/bin/env python3
"""銀山溫泉 空房監看 — 第一階段：抓資料、推論開放狀態、產生靜態狀態頁。

用法:
  python3 checker.py                 抓取最新資料，寫入 data/，產生 docs/index.html
  python3 checker.py --render-only   不抓取，用 data/state.json 重新產生頁面
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
STATE_PATH = DATA_DIR / "state.json"
HISTORY_PATH = DATA_DIR / "history.jsonl"
HOTELS_PATH = ROOT / "hotels.json"

# ── 目標條件 ────────────────────────────────────────────────────────────────
TARGET_DATES = ["2027-02-10", "2027-02-11", "2027-02-12"]
PARTY_SIZES = [2, 4]                      # 每室人數：兩人房 / 四人房
TARGET_MONTH = "202702"
HORIZON_MONTHS = ["202703", "202704", "202705"]  # 用來判斷「開放到哪裡」
MIN_TRAILING_CLOSED_DAYS = 14  # 邊界之後的 ※ 區段至少要這麼多天
BOUNDARY_NOISE_DAYS = 3        # 邊界之後容許的孤立非 ※ 天數（休館日等雜訊）

ENGINE = "https://www.nj-yoyaku.net"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ginzan-availability-checker/0.1"
REQUEST_GAP_SEC = 0.4
JST = timezone(timedelta(hours=9))

SYMBOL_MEANING = {
    "○": "有空房",
    "△": "少量空房",
    "×": "滿室或未開放",
    "※": "受付期間外",
    "-": "已過期",
}

# ── 抓取 ────────────────────────────────────────────────────────────────────
def fetch(url: str, referer: str | None = None, retries: int = 2) -> str:
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=25) as resp:
                raw = resp.read()
            for enc in ("utf-8", "shift_jis", "euc-jp"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", "ignore")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"fetch failed: {url}: {last_err}")


CELL_RE = re.compile(r"<td class='cal\w+'[^>]*>(\d+)<br />([^<]*)</td>")
MONTH_RE = re.compile(r"<h5>(\d{4})年(\d{2})月</h5>")


def fetch_month(hotel_id: str, yyyymm: str, nz: int) -> dict[str, str]:
    """回傳 {'2027-02-10': '×', ...}"""
    url = f"{ENGINE}/{hotel_id}/re_calendar.ashx?TDT={yyyymm}01&NZ={nz}"
    referer = f"{ENGINE}/{hotel_id}/plan.aspx?PID=-1"
    body = fetch(url, referer)
    m = MONTH_RE.search(body)
    if not m:
        raise RuntimeError(f"calendar header not found for {hotel_id} {yyyymm}")
    y, mo = m.group(1), m.group(2)
    out: dict[str, str] = {}
    for day, sym in CELL_RE.findall(body):
        sym = sym.strip() or "?"
        out[f"{y}-{mo}-{int(day):02d}"] = sym
    if not out:
        raise RuntimeError(f"no cells parsed for {hotel_id} {yyyymm}")
    time.sleep(REQUEST_GAP_SEC)
    return out


SALES_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日までの販売を開始")


def fetch_shouwakan() -> dict:
    """昭和館：讀官網 NEWS 的「○年○月○日までの販売を開始」。"""
    body = fetch("http://www.shouwakan.net/")
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    m = SALES_RE.search(text)
    if not m:
        return {"sales_until": None, "raw": None}
    y, mo, d = (int(x) for x in m.groups())
    return {"sales_until": f"{y:04d}-{mo:02d}-{d:02d}", "raw": m.group(0)}


# ── 推論 ────────────────────────────────────────────────────────────────────
def find_closed_boundary(horizon: dict[str, str]) -> str | None:
    """找出「受付期間外」真正開始的日期。

    規則：從最早日期往後掃，第一個 ※ 且其後的非 ※ 天數不超過 BOUNDARY_NOISE_DAYS、
    且從該日到抓取範圍末端至少 MIN_TRAILING_CLOSED_DAYS 天者，即為邊界。
    孤立的 ×（如藤屋 3/2、3/9，疑為休館日）視為雜訊；4 月又整月恢復 × 的（如瀧見館）不算邊界。
    """
    days = sorted(horizon)
    n = len(days)
    non_closed_after = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        non_closed_after[i] = non_closed_after[i + 1] + (0 if horizon[days[i]] == "※" else 1)
    for i, d in enumerate(days):
        if horizon[d] == "※" and non_closed_after[i + 1] <= BOUNDARY_NOISE_DAYS and (n - i) >= MIN_TRAILING_CLOSED_DAYS:
            return d
    return None


def infer_status(cells: dict[str, dict[str, str]], horizon: dict[str, str]) -> tuple[str, str]:
    """cells: {date: {'2': sym, '4': sym}}; horizon: {date: sym} (NZ=0, 2–5 月)
    逐日判斷後彙總，回傳 (狀態代碼, 說明)。"""
    boundary = find_closed_boundary(horizon)
    per_day: dict[str, str] = {}
    for d in TARGET_DATES:
        syms = set(cells.get(d, {}).values())
        if syms & {"○", "△"}:
            per_day[d] = "available"
        elif syms and syms <= {"※"}:
            per_day[d] = "closed"
        elif boundary and d < boundary:
            per_day[d] = "full"          # 已開放（邊界之前）但全 ×
        else:
            per_day[d] = "unknown"
    def days(state: str) -> str:
        return "、".join(d[5:].replace("-", "/") for d, v in per_day.items() if v == state)
    states = set(per_day.values())
    if "available" in states:
        return "available", f"有空房：{days('available')}"
    parts = []
    if "full" in states:
        parts.append(f"{days('full')} 已開放・滿室")
    if "closed" in states:
        parts.append(f"{days('closed')} 尚未開放")
    if "unknown" in states:
        parts.append(f"{days('unknown')} 無法判斷")
    note = "；".join(parts)
    if boundary:
        note += f"（{boundary[5:].replace('-', '/')} 起為受付期間外）"
    elif "unknown" in states:
        sporadic = sum(1 for v in horizon.values() if v == "※")
        note += f"（3–5 月有 {sporadic} 天 ※，疑為休館日）" if sporadic else "（2–5 月符號皆為 ×）"
    if states == {"full"}:
        return "full", note
    if states == {"closed"}:
        return "closed", note
    if "full" in states or "closed" in states:
        return "partial", note
    return "unknown", note


def infer_shouwakan(info: dict) -> tuple[str, str]:
    su = info.get("sales_until")
    if not su:
        return "unknown", "官網未找到販售公告"
    label = su[5:].replace("-", "/")
    covered = [d for d in TARGET_DATES if d <= su]
    if len(covered) == len(TARGET_DATES):
        return "opened", f"官網公告販售至 {label}，已涵蓋全部目標日期，請至官網系統查空房"
    if covered:
        cov = "、".join(d[5:].replace("-", "/") for d in covered)
        return "opened", f"官網公告販售至 {label}，已涵蓋 {cov}，請至官網系統查空房"
    return "closed", f"官網公告販售至 {label}，尚未涵蓋目標日期"


# ── 主流程 ──────────────────────────────────────────────────────────────────
def load_hotels() -> list[dict]:
    return json.loads(HOTELS_PATH.read_text(encoding="utf-8"))


def collect() -> dict:
    hotels = load_hotels()
    now = datetime.now(JST)
    result = {"generated_at": now.isoformat(timespec="seconds"), "hotels": {}}
    for h in hotels:
        hid = h["id"]
        entry: dict = {"errors": []}
        try:
            if h["engine"] == "njy":
                cells: dict[str, dict[str, str]] = {d: {} for d in TARGET_DATES}
                for nz in PARTY_SIZES:
                    month = fetch_month(hid, TARGET_MONTH, nz)
                    for d in TARGET_DATES:
                        cells[d][str(nz)] = month.get(d, "?")
                horizon: dict[str, str] = {}
                for mm in [TARGET_MONTH] + HORIZON_MONTHS:
                    horizon.update(fetch_month(hid, mm, 0))
                status, note = infer_status(cells, horizon)
                # 開放邊界：受付期間外連續區段起點的前一天；找不到邊界則為 None
                boundary = find_closed_boundary(horizon)
                last_open = None
                if boundary:
                    last_open = (date.fromisoformat(boundary) - timedelta(days=1)).isoformat()
                entry.update({
                    "cells": cells,
                    "status": status,
                    "status_note": note,
                    "closed_boundary": boundary,
                    "horizon_last_open": last_open,
                    "horizon_summary": summarize_horizon(horizon),
                })
            elif h["engine"] == "shouwakan":
                info = fetch_shouwakan()
                status, note = infer_shouwakan(info)
                entry.update({"cells": {}, "status": status, "status_note": note, "shouwakan": info})
            else:
                entry.update({"cells": {}, "status": "n/a", "status_note": "不在共用引擎上，需另查"})
        except Exception as e:  # 單一旅館失敗不影響其他
            entry["errors"].append(str(e))
            entry.update({"cells": entry.get("cells", {}), "status": "error", "status_note": "抓取失敗"})
            print(f"[warn] {hid}: {e}", file=sys.stderr)
        result["hotels"][hid] = entry
        print(f"[ok] {hid}: {entry['status']} — {entry['status_note']}", file=sys.stderr)
    return result


def summarize_horizon(horizon: dict[str, str]) -> list[dict]:
    """每月符號統計，給頁面顯示用。"""
    by_month: dict[str, dict[str, int]] = {}
    for d, s in horizon.items():
        by_month.setdefault(d[:7], {}).setdefault(s, 0)
        by_month[d[:7]][s] += 1
    return [{"month": m, "counts": c} for m, c in sorted(by_month.items())]


def diff_and_log(prev: dict | None, cur: dict) -> list[dict]:
    """比對上次狀態，回傳變化清單並附加到 history.jsonl。"""
    changes: list[dict] = []
    if not prev:
        return changes
    ts = cur["generated_at"]
    for hid, ce in cur["hotels"].items():
        pe = (prev.get("hotels") or {}).get(hid) or {}
        for d in TARGET_DATES:
            for nz in PARTY_SIZES:
                a = (pe.get("cells") or {}).get(d, {}).get(str(nz))
                b = (ce.get("cells") or {}).get(d, {}).get(str(nz))
                if a is not None and b is not None and a != b:
                    changes.append({"ts": ts, "hotel": hid, "date": d, "nz": nz, "from": a, "to": b})
        if pe.get("status") and pe.get("status") != ce.get("status"):
            changes.append({"ts": ts, "hotel": hid, "date": None, "nz": None,
                            "from": pe.get("status_note"), "to": ce.get("status_note")})
    if changes:
        with HISTORY_PATH.open("a", encoding="utf-8") as f:
            for c in changes:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return changes


def load_history(limit: int = 40) -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines[-limit:]][::-1]


# ── 頁面 ────────────────────────────────────────────────────────────────────
def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def fmt_date(d: str) -> str:
    dt = date.fromisoformat(d)
    wd = "日月火水木金土"[(dt.weekday() + 1) % 7]
    return f"{dt.month}/{dt.day}（{wd}）"


def cell_html(hotel_id: str, d: str, nz: int, sym: str | None) -> str:
    if sym is None:
        return "<td class='cell na'>—</td>"
    cls = {"○": "ok", "△": "few", "×": "full", "※": "closed", "-": "past"}.get(sym, "na")
    title = SYMBOL_MEANING.get(sym, "")
    if sym in ("○", "△"):
        url = f"{ENGINE}/{hotel_id}/plan.aspx?TDT={d.replace('-', '')}&NZ={nz}"
        return f"<td class='cell {cls}'><a href='{url}' target='_blank' rel='noopener' title='{esc(title)}'>{sym}</a></td>"
    return f"<td class='cell {cls}' title='{esc(title)}'>{sym}</td>"


STATUS_LABEL = {
    "available": ("有空房", "s-ok"),
    "full": ("已開放・滿室", "s-full"),
    "closed": ("未開放", "s-closed"),
    "partial": ("部分已開放", "s-partial"),
    "opened": ("已開賣（另系統）", "s-other"),
    "unknown": ("無法判斷", "s-unknown"),
    "n/a": ("不在引擎上", "s-na"),
    "error": ("抓取失敗", "s-err"),
}


def render(state: dict, history: list[dict]) -> tuple[str, str]:
    """回傳 (head_inner, body_inner)。"""
    hotels = load_hotels()
    hmap = {h["id"]: h for h in hotels}
    gen = datetime.fromisoformat(state["generated_at"]).astimezone(JST)
    gen_str = gen.strftime("%Y/%m/%d %H:%M JST")

    counts = {"available": 0, "full": 0, "closed": 0, "unknown": 0}
    for hid, e in state["hotels"].items():
        st = "full" if e.get("status") == "partial" else e.get("status")
        if st in counts:
            counts[st] += 1

    rows = []
    for h in hotels:
        e = state["hotels"].get(h["id"], {})
        label, scls = STATUS_LABEL.get(e.get("status", "error"), STATUS_LABEL["error"])
        cap = h.get("max_capacity")
        cap_txt = f"最多 {cap} 人" if cap else "不明"
        cap_cls = "cap-ok" if (cap or 0) >= 4 else "cap-low"
        cells = ""
        for d in TARGET_DATES:
            for nz in PARTY_SIZES:
                sym = (e.get("cells") or {}).get(d, {}).get(str(nz))
                if h["engine"] != "njy":
                    cells += "<td class='cell na'>—</td>"
                else:
                    cells += cell_html(h["id"], d, nz, sym)
        horizon_txt = ""
        if e.get("status") in ("closed", "full", "partial") and e.get("horizon_last_open"):
            horizon_txt = f"目前開放至：{fmt_date(e['horizon_last_open'])}"
        link = h.get("booking_url") or (f"{ENGINE}/{h['id']}/plan.aspx?PID=-1" if h["engine"] == "njy" else h["official"])
        rows.append(f"""
        <tr class='{scls}'>
          <th scope='row' class='hotel'>
            <a class='hname' href='{esc(link)}' target='_blank' rel='noopener'>{esc(h['name'])}</a>
            <span class='meta'><span class='{cap_cls}'>{esc(cap_txt)}</span> · <a href='{esc(h['official'])}' target='_blank' rel='noopener'>官網</a> · {esc(h['tel'])}</span>
          </th>
          <td class='status'>
            <span class='pill {scls}'>{esc(label)}</span>
            <span class='note'>{esc(e.get('status_note', ''))}</span>
            {f"<span class='note dim'>{esc(horizon_txt)}</span>" if horizon_txt else ""}
            <span class='note dim'>開放規則：{esc(h.get('open_rule', ''))}</span>
          </td>
          {cells}
        </tr>""")

    if history:
        hist_items = []
        for c in history:
            hn = hmap.get(c["hotel"], {}).get("name", c["hotel"])
            ts = datetime.fromisoformat(c["ts"]).astimezone(JST).strftime("%m/%d %H:%M")
            if c.get("date"):
                hist_items.append(
                    f"<li><span class='ts'>{ts}</span> <b>{esc(hn)}</b> {fmt_date(c['date'])} {c['nz']}人 "
                    f"<span class='chg'>{esc(c['from'])} → {esc(c['to'])}</span></li>")
            else:
                hist_items.append(
                    f"<li><span class='ts'>{ts}</span> <b>{esc(hn)}</b> 狀態 "
                    f"<span class='chg'>{esc(c['from'])} → {esc(c['to'])}</span></li>")
        hist_html = "<ul class='hist'>" + "".join(hist_items) + "</ul>"
    else:
        hist_html = "<p class='empty'>尚無變化紀錄。腳本每次執行都會與上一次比對，任何格子的符號改變都會列在這裡。</p>"

    date_heads = "".join(
        f"<th colspan='{len(PARTY_SIZES)}' class='dhead'>{fmt_date(d)}</th>" for d in TARGET_DATES)
    nz_heads = "".join(
        f"<th class='nzhead'>{nz}人</th>" for _ in TARGET_DATES for nz in PARTY_SIZES)

    head_inner = f"""<title>銀山溫泉 二月空房</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@500;700&family=Noto+Sans+JP:wght@400;500;700&family=IBM+Plex+Mono:wght@400;600&display=swap">
<style>
{CSS}
</style>"""

    body_inner = f"""
<main class="wrap">
  <header class="top">
    <div>
      <p class="eyebrow">銀山温泉 · 空室狀態</p>
      <h1>2027 年 2 月 10 – 12 日<br>四人房一間，或兩人房兩間</h1>
    </div>
    <dl class="gen">
      <div><dt>最後更新</dt><dd>{esc(gen_str)}</dd></div>
      <div><dt>資料來源</dt><dd>共用預約引擎 nj-yoyaku.net 月曆、昭和館官網公告</dd></div>
    </dl>
  </header>

  <section class="summary" aria-label="摘要">
    <div class="stat s-ok"><span class="n">{counts['available']}</span><span class="l">間有空房</span></div>
    <div class="stat s-full"><span class="n">{counts['full']}</span><span class="l">間已開放（全部或部分日期）・滿室</span></div>
    <div class="stat s-closed"><span class="n">{counts['closed']}</span><span class="l">間尚未開放</span></div>
    <div class="stat s-unknown"><span class="n">{counts['unknown']}</span><span class="l">間無法判斷</span></div>
  </section>

  <section class="tablewrap">
    <table class="grid">
      <thead>
        <tr>
          <th rowspan="2" class="hhead">旅館</th>
          <th rowspan="2" class="shead">開放狀態</th>
          {date_heads}
        </tr>
        <tr>{nz_heads}</tr>
      </thead>
      <tbody>{''.join(rows)}
      </tbody>
    </table>
  </section>

  <section class="legend">
    <h2>符號</h2>
    <ul>
      <li><span class="sym ok">○</span>有空房，點格子直達該日訂房頁</li>
      <li><span class="sym few">△</span>少量空房，可能只剩一間</li>
      <li><span class="sym full">×</span>滿室，<em>或</em>尚未開放預約。引擎用同一符號，無法區分</li>
      <li><span class="sym closed">※</span>受付期間外，可確定尚未開放</li>
      <li><span class="sym na">—</span>此旅館不在共用引擎上，或無此人數選項</li>
    </ul>
    <p class="fine">「開放狀態」是腳本的推論：目標日期為 × 且 ※ 從某日起連續到 5 月底（容許少數孤立雜訊日），判為「已開放・滿室」；零星或之後又恢復 × 的 ※ 視為休館日，不算邊界；目標日期本身為 ※ 判為「未開放」；2–5 月全為 × 則無法判斷，請對照該旅館的開放規則。引擎不顯示剩餘間數，兩人房 × 2 需在出現 ○ 或 △ 後人工至訂房頁確認。</p>
  </section>

  <section class="changes">
    <h2>最近變化</h2>
    {hist_html}
  </section>

  <footer class="foot">
    <p>每 2 人 / 4 人欄位對應引擎的「一室当りのご利用人数」篩選。各旅館開放規則除永澤平八官網明載外，皆為部落格整理、未經官方驗證。</p>
    <p>官網旅館列表 <a href="https://www.ginzanonsen.jp/yado/" target="_blank" rel="noopener">ginzanonsen.jp/yado</a> · 預約入口 <a href="https://www.nj-yoyaku.net/portal/main" target="_blank" rel="noopener">nj-yoyaku.net/portal</a></p>
  </footer>
</main>
"""
    return head_inner, body_inner


CSS = """
:root{
  --bg:#f3f5f8; --panel:#ffffff; --line:#d9dee5; --ink:#1c2229; --muted:#6a737d; --dim:#98a1ab;
  --accent:#3b5480; --accent-ink:#2b4066;
  --ok:#2a7a5a; --ok-bg:#e3f2ea; --few:#b96d12; --few-bg:#fbeedc; --full:#8a9099; --full-bg:transparent;
  --closed:#aab1ba; --closed-bg:#f0f2f5; --err:#a33a3a; --accent-bg:#e8edf6;
  --shadow:0 1px 2px rgba(20,30,45,.06),0 6px 20px -12px rgba(20,30,45,.18);
  --serif:"Shippori Mincho","Hiragino Mincho ProN","Yu Mincho",serif;
  --sans:"Noto Sans JP","Hiragino Sans","PingFang TC",system-ui,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#131920; --panel:#1b232c; --line:#2b3540; --ink:#e7ebef; --muted:#a2acb6; --dim:#6f7a86;
    --accent:#8fb0e6; --accent-ink:#b4cbf0;
    --ok:#6fd2a3; --ok-bg:#183327; --few:#f0b35a; --few-bg:#3a2a12; --full:#6b7580;
    --closed:#5a6570; --closed-bg:#1f2830; --err:#e07a7a; --accent-bg:#1f2b3d;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -14px rgba(0,0,0,.6);
  }
}
:root[data-theme="dark"]{
  --bg:#131920; --panel:#1b232c; --line:#2b3540; --ink:#e7ebef; --muted:#a2acb6; --dim:#6f7a86;
  --accent:#8fb0e6; --accent-ink:#b4cbf0;
  --ok:#6fd2a3; --ok-bg:#183327; --few:#f0b35a; --few-bg:#3a2a12; --full:#6b7580;
  --closed:#5a6570; --closed-bg:#1f2830; --err:#e07a7a;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -14px rgba(0,0,0,.6);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:14px;line-height:1.6;-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}
a:hover,a:focus-visible{text-decoration:underline;text-underline-offset:2px}
a:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:2px}
.wrap{max-width:1180px;margin:0 auto;padding:40px 24px 64px;display:grid;gap:28px}
.top{display:flex;justify-content:space-between;align-items:flex-end;gap:24px;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:20px}
.eyebrow{margin:0 0 6px;font-family:var(--mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
h1{font-family:var(--serif);font-weight:700;font-size:clamp(22px,2.6vw,30px);line-height:1.3;margin:0;text-wrap:balance;letter-spacing:.01em}
.gen{margin:0;display:grid;gap:4px;font-size:12px;color:var(--muted)}
.gen div{display:flex;gap:10px}
.gen dt{font-family:var(--mono);letter-spacing:.06em;color:var(--dim);min-width:64px}
.gen dd{margin:0;font-variant-numeric:tabular-nums}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:14px 16px;display:flex;align-items:baseline;gap:10px;box-shadow:var(--shadow)}
.stat .n{font-family:var(--mono);font-size:28px;font-weight:600;line-height:1;font-variant-numeric:tabular-nums}
.stat .l{color:var(--muted);font-size:13px}
.stat.s-ok .n{color:var(--ok)} .stat.s-full .n{color:var(--muted)} .stat.s-closed .n{color:var(--closed)} .stat.s-unknown .n{color:var(--few)}
.tablewrap{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:6px;box-shadow:var(--shadow)}
table.grid{border-collapse:separate;border-spacing:0;width:100%;min-width:900px}
.grid thead th{position:sticky;top:0;background:var(--panel);z-index:1;font-weight:500;color:var(--muted);font-size:12px;text-align:left;padding:10px 12px;border-bottom:1px solid var(--line)}
.grid thead .dhead{text-align:center;font-family:var(--serif);font-size:14px;color:var(--ink);border-left:1px solid var(--line);font-weight:700}
.grid thead .nzhead{text-align:center;font-family:var(--mono);font-size:11px;letter-spacing:.04em;min-width:56px}
.grid thead .nzhead:nth-child(odd){border-left:1px solid var(--line)}
.grid tbody th,.grid tbody td{padding:12px;border-bottom:1px solid var(--line);vertical-align:top}
.grid tbody tr:last-child th,.grid tbody tr:last-child td{border-bottom:0}
.hotel{text-align:left;font-weight:400;min-width:200px}
.hname{font-family:var(--serif);font-size:15px;font-weight:700;color:var(--ink);display:block;line-height:1.35}
.hname:hover{color:var(--accent)}
.meta{display:block;margin-top:4px;font-size:11.5px;color:var(--muted);font-variant-numeric:tabular-nums}
.cap-ok{color:var(--ok);font-weight:500} .cap-low{color:var(--dim)}
.status{min-width:260px;max-width:340px}
.pill{display:inline-block;font-size:11.5px;font-weight:600;padding:2px 8px;border-radius:999px;border:1px solid var(--line);background:var(--bg);color:var(--muted);letter-spacing:.02em}
.pill.s-ok{color:var(--ok);background:var(--ok-bg);border-color:transparent}
.pill.s-closed{color:var(--muted);background:var(--closed-bg)}
.pill.s-partial{color:var(--accent-ink);background:var(--accent-bg,var(--bg))}
.pill.s-other{color:var(--accent-ink);background:var(--bg)}
.pill.s-unknown{color:var(--few);background:var(--few-bg);border-color:transparent}
.pill.s-err{color:var(--err)}
.note{display:block;margin-top:5px;font-size:12px;line-height:1.5;color:var(--ink)}
.note.dim{color:var(--dim);font-size:11.5px}
td.cell{text-align:center;font-family:var(--mono);font-size:18px;font-weight:600;line-height:1;padding-top:16px}
td.cell:nth-child(odd){border-left:1px solid var(--line)}
td.cell.full{color:var(--full)}
td.cell.closed{color:var(--closed);background:var(--closed-bg)}
td.cell.past,td.cell.na{color:var(--dim);font-weight:400}
td.cell.few{background:var(--few-bg)} td.cell.few a{color:var(--few)}
td.cell.ok{background:var(--ok-bg)} td.cell.ok a{color:var(--ok)}
td.cell a{display:inline-block;padding:2px 8px;border-radius:4px;text-decoration:none}
td.cell a:hover{outline:2px solid currentColor}
tr.s-ok .hname{color:var(--ok)}
.legend,.changes{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:18px 20px;box-shadow:var(--shadow)}
h2{font-family:var(--serif);font-size:16px;margin:0 0 10px;font-weight:700}
.legend ul{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:6px 20px;font-size:13px}
.legend li{display:flex;align-items:center;gap:10px}
.sym{font-family:var(--mono);font-size:16px;font-weight:600;width:28px;height:28px;display:inline-grid;place-items:center;border-radius:4px;border:1px solid var(--line)}
.sym.ok{color:var(--ok);background:var(--ok-bg);border-color:transparent} .sym.few{color:var(--few);background:var(--few-bg);border-color:transparent}
.sym.full{color:var(--full)} .sym.closed{color:var(--closed);background:var(--closed-bg)} .sym.na{color:var(--dim)}
.fine{margin:14px 0 0;font-size:12px;color:var(--muted);max-width:72ch;line-height:1.65}
.hist{list-style:none;margin:0;padding:0;display:grid;gap:6px;font-size:13px}
.hist .ts{font-family:var(--mono);color:var(--muted);font-size:12px;margin-right:6px}
.hist .chg{font-family:var(--mono);color:var(--accent-ink);margin-left:6px}
.empty{margin:0;color:var(--muted);font-size:13px;max-width:72ch}
.foot{font-size:12px;color:var(--dim);display:grid;gap:4px;max-width:80ch}
.foot p{margin:0}
@media (max-width:640px){.wrap{padding:24px 14px 48px}.status{min-width:220px}}
@media (prefers-reduced-motion: reduce){*{transition:none!important}}
"""


def write_outputs(state: dict, history: list[dict]) -> None:
    DOCS_DIR.mkdir(exist_ok=True)
    head, body = render(state, history)
    full = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
{head}
</head>
<body>{body}</body>
</html>
"""
    (DOCS_DIR / "index.html").write_text(full, encoding="utf-8")
    # 供 Artifact 等只接受片段的宿主使用
    (DOCS_DIR / "fragment.html").write_text(head + "\n" + body, encoding="utf-8")


def _pad(text: str, width: int) -> str:
    import unicodedata
    w = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    return text + " " * max(0, width - w)


def print_terminal(state: dict) -> None:
    hotels = load_hotels()
    print(f"\n銀山溫泉 2027/02/10–12  更新 {state['generated_at']}")
    print(_pad("旅館", 24) + _pad("狀態", 16) + "".join(_pad(d[5:], 10) for d in TARGET_DATES))
    print(" " * 40 + "".join(_pad("2人", 5) + _pad("4人", 5) for _ in TARGET_DATES))
    for h in hotels:
        e = state["hotels"].get(h["id"], {})
        label = STATUS_LABEL.get(e.get("status", "error"), ("?", ""))[0]
        line = _pad(h["name"][-11:], 24) + _pad(label, 16)
        for d in TARGET_DATES:
            for nz in PARTY_SIZES:
                line += _pad((e.get("cells") or {}).get(d, {}).get(str(nz), "—"), 5)
        print(line)


def main(argv: list[str]) -> int:
    DATA_DIR.mkdir(exist_ok=True)
    prev = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else None
    if "--render-only" in argv:
        if not prev:
            print("沒有 data/state.json，請先執行一次不帶參數的抓取。", file=sys.stderr)
            return 1
        state = prev
    else:
        state = collect()
        changes = diff_and_log(prev, state)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        if changes:
            print(f"[changes] {len(changes)} 筆變化", file=sys.stderr)
    write_outputs(state, load_history())
    print_terminal(state)
    print(f"\n已寫入 {DOCS_DIR / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
