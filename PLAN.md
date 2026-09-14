# 銀山溫泉 空房監看專案 — 計畫書

目標：在 2027/02/10、02/11、02/12 三個入住日中，找到任一旅館可訂到
「四人房 1 間」或「兩人房 2 間」，並在空房出現時盡快得知。

---

## 1. 調查結論（2026‑09‑08 實測）

### 1.1 其實有「半個 API」可用
官網 https://www.ginzanonsen.jp/yado/ 上 13 間旅館中，**12 間共用同一套預約引擎**
（Well‑com「Web予約システム」，網域 `nj-yoyaku.net`）。該引擎的月曆是用 AJAX 載入的，
端點是純 GET、不需登入、不需 cookie，回傳一小段 HTML：

```
GET https://www.nj-yoyaku.net/{hotel_id}/re_calendar.ashx?TDT=YYYYMM01&NZ={每室人數}
```

回傳格式（每天一格）：

```html
<td class='calday'>10<br />×</td>
<td class='calsat'>14<br />△</td>
```

| 符號 | 目前推測意義 | 依據 |
|---|---|---|
| `-` | 已過去的日期 | 9 月 1–7 日全為 `-` |
| `×` | 滿室 **或** 尚未開放預約（兩者同符號，無法區分） | 藤屋 2 月 1–5 日為 `×`，6 日起為 `※`，剛好對應其「150 天前開放」規則 |
| `△` | 尚有少量空房 | 9/14 能登屋為 `△`，點進去確有 1 個房型可訂 |
| `○` | 有空房（尚未實際觀察到，待驗證） | 一般日本預約系統慣例 |
| `※` | 受付期間外 / 休館 | 藤屋、銀山荘的未開放區間 |

再往下一層，指定日期的可訂房型、定員、每人價格也是純 GET：

```
GET https://www.nj-yoyaku.net/{hotel_id}/plan.aspx?TDT=YYYYMMDD&NZ={人數}
```
頁面內每個房型有 `[定員] 2名 ～ 4名` 與 `2名利用時 / 3名利用時 / 4名利用時` 價格。
**缺點：看不到「剩幾間」**，所以「兩間兩人房」無法從資料直接確認（見 §4）。

官網另有一個 `portal/search` 全區搜尋表單（ASP.NET POST），實測回傳空結果，不建議依賴。

### 1.2 旅館清單與 hotel_id

| 旅館 | hotel_id | 有 ≥4 人房型 | 官網 |
|---|---|---|---|
| 仙峡の宿 銀山荘 | `ginzanso` | ✅（2–6 人多種） | ginzanso.jp |
| 古勢起屋別館 | `kosekiya` | ✅（2–6、1–4） | kosekiya.jp |
| 本館古勢起屋 | `kosekikan` | ❌ 全為 2 人房 | kosekikan.com |
| 瀧見館 | `takimikan` | ❌ 最多 3 人 | takimikan.jp |
| 能登屋旅館 | `notoya` | ✅（2–4、2–6） | notoyaryokan.com |
| 昭和館 | `showakan`（疑似廢棄） | ✅（2–4） | shouwakan.net → 2026/07/02 起改用 Liberty 系統 |
| 旅館 永澤平八 | `nagasawa` | ✅（2–4 ×2 房型） | nagasawa-heihachi.com（另有自家 hpdsp 系統、じゃらん） |
| 旅籠 いとうや | `itouya` | ❌ 最多 3 人 | hatago-itouya.com |
| 御宿 やなだ屋 | `yanadaya` | ✅（1–4） | yanadaya.com |
| 藤屋 | `fujiya` | ❌ 最多 3 人 | fujiya-ginzan.com |
| 旅館 松本 | `matsumoto` | 引擎上目前無房型資料 | ginzan-matsumoto.com |
| 古山閣 | `kozankaku` | ✅（2–8、4–8 等多種） | kozankaku.com |
| クラノバ | （無，為古山閣附屬 Auberge） | — | kozankaku.com/auberge.html |

「四人房」候選：銀山荘、古勢起屋別館、能登屋、昭和館、永澤平八、やなだ屋、古山閣。
「兩間兩人房」候選：以上全部再加 本館古勢起屋、瀧見館、いとうや、藤屋。

### 1.3 各旅館開放預約時間（來自部落格整理，**未經官方驗證**，需自行確認）

| 旅館 | 開放規則 | 2/10–12 入住的預估開放日 |
|---|---|---|
| 藤屋 | 入住日 150 天前 00:00（官網系統） | 2026‑09‑13 ~ 09‑15 ← **最近** |
| 銀山荘 | 每月 1 日開放 5 個月後 | 2026‑09‑01（已開放，月曆全 ×，可能已滿） |
| 古勢起屋（本館/別館） | 每月 1 日開放 5 個月後 | 2026‑09‑01（同上） |
| 能登屋 | 約 6 個月前 | 約 2026‑08 中（同上） |
| 瀧見館 | 6 個月以上前 | 已開放 |
| 古山閣 / クラノバ | 每月 1 日開放 3 個月後 | 2026‑11‑01 |
| 旅館 松本 | 入住月 3 個月前的月初 | 2026‑11‑01 |
| 永澤平八 | 每月 1 日 09:00 開放 3 個月後（官網明載，以電話為主） | 2026‑11‑01 09:00 JST |
| いとうや / やなだ屋 | 不定期，需自行確認 | — |

實測 2027/02 目前 12 間全部為 `×` 或 `※`，尚無任何可訂日期。
因此這個專案的重點不是「查一次」，而是**持續監看 + 在開放日守候 + 追蹤取消釋出**。

---

## 2. 系統設計（力求簡單）

```
[排程器] --每 15–30 分--> [checker.py] --GET x24--> nj-yoyaku.net
                              |
                              +--> state.json（上次狀態）
                              +--> 有 × → △/○ 變化 → 推播通知（Telegram / Slack / Email）
                              +--> 產生 docs/index.html 狀態表（GitHub Pages 顯示）
```

### 2.1 checker.py 核心邏輯
1. 對 12 個 `hotel_id` × `NZ∈{2,4}` 呼叫 `re_calendar.ashx?TDT=20270201`（共 24 個請求，每次 <2 KB）。
2. 用 regex 解析 `<td class='cal…'>(\d+)<br />(.)</td>`，取出 10、11、12 日的符號。
3. 與 `state.json` 比對；任何格子從 `×`/`※` 變成 `△`/`○` 就發通知，附上直達連結：
   `https://www.nj-yoyaku.net/{id}/plan.aspx?TDT=202702DD&NZ=4`
4. 若是 `NZ=4` 出現空房 → 標記為「四人房候選」；若是 `NZ=2` 出現 → 進一步抓 `plan.aspx?TDT=…&NZ=2`
   看有幾個房型可訂，仍需人工確認能否訂到 2 間（見 §4）。
5. 輸出一張 12 旅館 × 3 日期 × {2 人, 4 人} 的表格到 `docs/index.html`。

### 2.2 排程與部署（擇一）
- **GitHub Actions cron**（推薦，免伺服器、免費）：`*/20 * * * *`，把 `state.json` 與 `docs/` commit 回 repo，
  搭配 GitHub Pages 就是「顯示頁面」。注意 GitHub cron 有數分鐘延遲，開放日當天請改用本機 launchd 每 1–2 分跑。
- **本機 macOS launchd** 或 `cron`：最快最穩，但電腦要開著。
- Cloud Run Job + Cloud Scheduler：可行但對這個規模偏重。

### 2.3 通知管道
- Telegram Bot（最簡單，一個 `sendMessage` POST）
- Slack Incoming Webhook（你環境已有 Slack）
- Gmail SMTP 備援
（LINE Notify 已於 2025 年停止服務，勿用。）

### 2.4 禮貌性原則
- 固定 User‑Agent、帶 `Referer: https://www.nj-yoyaku.net/{id}/plan.aspx?PID=-1`。
- 平時 15–30 分一次；只在開放日前後 1 小時縮短到 1 分鐘。
- 對方是小型系統商，24 請求/20 分 屬合理範圍；不要並發轟炸。
- 任何解析失敗（HTML 格式改變）要通知你，而不是靜默失敗。

---

## 3. 階段與里程碑

**第一階段（已完成 2026‑09‑09）— 靜態狀態頁**
- `checker.py` + `hotels.json`：抓 12 間 × {2人, 4人} 的 2 月月曆，加 3–5 月邊界推論開放狀態；
  昭和館改讀官網「販売を開始」公告。輸出 `docs/index.html`、`data/state.json`、`data/history.jsonl`。

**第二階段 — 排程與通知**
- GitHub Actions / launchd 排程，Gmail SMTP 寄信；只在符號變化或昭和館公告日期涵蓋 2/12 時寄。

原里程碑對照：

| # | 內容 | 預估 |
|---|---|---|
| M1 | ✅ `checker.py`：抓 12 間 × 2 人數 的 2 月月曆、印出 10–12 日狀態表 | 完成 |
| M2 | 加 `state.json` diff + Telegram/Slack 通知 | 1 小時 |
| M3 | ✅ 靜態狀態頁（Pages 部署留待第二階段） | 完成 |
| M4 | 開放日「衝刺模式」：09‑13 00:00（藤屋）、11‑01 00:00/09:00（古山閣、松本、永澤平八）改 1 分一次 | 30 分 |
| M5 | 可選：楽天トラベル空室 API（有正式 API）作為第二資料源，涵蓋部分旅館的 OTA 庫存 | 2 小時 |

---

### 3.1 昭和館特別說明（2026‑09‑09 調查）
- 官網 NEWS 2026/08/18：「現在、2027年1月13日までの販売を開始しております」。不是固定規則，而是分批延長販售截止日並公告。
- 2026/07/02 起訂房系統改為 Liberty（site.reservation.liberty-service.com，Nuxt SPA，背後有 `/api/booking/price-calendars`、`/api/booking/search`）。
  nj-yoyaku 上的 showakan 頁僅剩 1 個方案且全年 ×，判定為廢棄，腳本不再讀取。
- 2026‑09‑14 起 checker 直接呼叫 Liberty API（`webapi.site.reservation.liberty-service.com`，標頭 `X-Site-Code` / `X-Facility-Code: facility-…`，
  `POST /api/booking/search` 需 `checkInDate`/`checkOutDate`（yyyymmdd 整數）、`restNumber`、`roomNumber`、`guestsPerRoom[{appDateId, personAgeTypeId, number}]`）。
  回傳每房型每日 `remainNumber`（庫存）與狀態旗標；`GET /api/booking/plans/{p}/rooms/{r}` 有定員與 `bookingReceptionStart`。
- 實測：2 月房型有庫存，但「官方事前決済」方案 `bookingReceptionStart = 2026‑12‑01`，12/1 前系統不受理。官網公告的「販売」與此不一致，以 API 為準。

## 4. 已知限制與風險

1. **`×` 無法區分「滿室」與「未開放」**。對策：不解讀單次快照，只追蹤「變化」；並用 §1.3 的開放日輔助判讀。
2. **看不到剩餘間數**。兩間兩人房的判斷：`○` 多半代表 ≥2 間、`△` 可能只剩 1 間；收到通知後要立刻人工進 `plan.aspx` 分兩次下訂確認。
3. 符號 `○` 尚未實際觀察到，M1 完成後拿其他月份/其他溫泉地（同引擎、`portal/main` 有列出）驗證。
4. 部分旅館電話預約優先（永澤平八），網路庫存可能只是一部分；開放日仍建議同時打電話。
5. 月曆的 `NZ` 是「每室人數」篩選，4 人房查 `NZ=4`；若旅館設定允許 4 人擠 2–3 人定員房的加人方案，`NZ=4` 也會反映。
6. 網站沒有 ToS 明文禁止讀取，但屬灰色地帶；保持低頻、只讀不寫、不自動下訂。

---

## 5. 參考來源
- 官網旅館列表：https://www.ginzanonsen.jp/yado/
- 共用預約入口：https://www.nj-yoyaku.net/portal/main
- 開放時程整理（部落格，未驗證）：
  - https://note.com/keimei_0601/n/nc1bbb3998440
  - https://omiyaselect.com/ginzanonsen-reservation-months-before/
  - https://www.farmhouse-shop.jp/ginzan-onsen-reservation/
  - https://geto-onsen.com/ginzan-yoyaku/
  - https://yunokaori.com/ginzanonsen-kozankaku-yoyaku/
- 永澤平八 官網（電話預約規則）：https://www.nagasawa-heihachi.com/
