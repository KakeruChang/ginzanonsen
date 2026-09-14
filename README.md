# 銀山溫泉 空房監看

目標：2027/02/10–12 任一天，在銀山溫泉任一旅館訂到四人房一間或兩人房兩間。
沒有官方 API，這個專案改讀共用預約引擎（nj-yoyaku.net）的月曆片段與昭和館官網公告，
產生一張靜態狀態頁。

## 執行

只需 Python 3.10+，無第三方套件。

```bash
python3 checker.py                # 抓取（約 60 個小請求、1–2 分鐘），寫入 data/ 與 docs/index.html
python3 checker.py --render-only  # 不抓取，用上次資料重新產生頁面
open docs/index.html
```

## 產出

| 路徑 | 內容 |
|---|---|
| `docs/index.html` | 狀態頁（可直接用 GitHub Pages 發佈 `docs/` 目錄） |
| `docs/fragment.html` | 同一頁的片段版，給只接受 `<body>` 內容的宿主 |
| `data/state.json` | 最新一次快照 |
| `data/history.jsonl` | 每次執行與前一次比對出的符號變化，每行一筆 |

## 設定

- `hotels.json`：13 間旅館的 id、名稱、最大定員、官網、電話、開放規則備註。
- `checker.py` 頂部：`TARGET_DATES`、`PARTY_SIZES`、`HORIZON_MONTHS`。

## 自動更新（GitHub Actions + Pages）

`.github/workflows/check.yml` 每 30 分鐘執行一次 `checker.py`，把 `docs/` 發佈到 GitHub Pages；
只有在 10–12 日的符號出現變化時才把 `data/` 提交回 repo，避免每半小時一個 commit。

初次設定：
1. Repo → Settings → Pages → Source 選「GitHub Actions」。
2. Actions 頁面手動跑一次「check availability」，完成後頁面網址會顯示在 workflow 摘要。

`docs/fujiya-copy-form*.html` 含個人資料，已在 `.gitignore` 排除，只存在本機。

## 階段

1. **第一階段（完成）**：抓資料、推論開放狀態、產生靜態頁與變化紀錄。
2. **第二階段（進行中）**：GitHub Actions 排程 + Pages（完成）；Email 通知（待做）。

## 資料來源與限制

詳見 [PLAN.md](PLAN.md)。要點：引擎的 `×` 同時代表「滿室」與「未開放」，
頁面上的「開放狀態」是依 3–5 月是否出現 `※` 推論的；引擎不顯示剩餘間數。
昭和館自 2026/07 起改用另一套系統，這裡只讀其官網「販売を開始」公告。
