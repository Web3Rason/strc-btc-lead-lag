# 5037 STRC → BTC 領先指標研究

研究假設：**STRC 能不能當作 BTC 價格的「領先指標」？**

STRC = Strategy（前身 MicroStrategy）2025/7 發行的 *Stretch* 永續特別股（Nasdaq:STRC），
公司用月配息機制刻意把股價穩定在 $100 面值附近、抽掉波動，募資去買比特幣。
因為波動被刻意壓低，**原型價格幾乎不可能領先 BTC**（理論上反而是 BTC 領先 STRC）；
真正可能洩漏領先資訊的是 STRC 的**壓力訊號**（折溢價、異常成交量）。本工具如實驗證，
跑出來很可能是「無領先／落後」這個誠實但有價值的結論。

## 快速啟動

```
雙擊 start.bat       （背景啟動，無視窗）
或：cd backend && python app.py
```

首次需安裝依賴：`pip install -r backend/requirements.txt`

啟動後開瀏覽器：**http://localhost:5037**

## 技術架構

| 層級 | 技術 |
|------|------|
| 後端 | Python Flask（`backend/app.py`，port 5037，同時 serve 前端） |
| 統計 | numpy / pandas / scipy（CCF、OLS 迴歸） |
| 前端 | 單頁 HTML（`templates/index.html`）+ Chart.js（CDN） |
| 資料 | STRC → Yahoo Finance 日線；BTC → Binance 現貨 15m K 線（本地檔案快取 + 增量更新） |

## 目錄結構

```
5037_STRC-BTC-Lead-Lag/
├── backend/
│   ├── app.py            # Flask：/ 回前端、/api/analysis 回指標
│   ├── data.py           # 抓 STRC(Yahoo) + BTC(Binance) 並做錨點對齊
│   ├── analysis.py       # 折溢價 / 量z / CCF / 迴歸 / 結論判定
│   ├── cache/            # BTC 15m K 本地快取（btc_15m.pkl，不進 git）
│   └── requirements.txt
├── templates/
│   └── index.html        # 結論卡 + 5 張圖
├── start.bat
└── README.md
```

## 方法論

**時間對齊（關鍵）**：STRC 只在美股時段交易、BTC 24 小時，兩者時間軸對不齊。
採「錨點對齊」而非 forward-fill：
- STRC 收盤 16:00 ET → 取同時點 BTC 價（`btc_close`）
- STRC 開盤 09:30 ET → 取同時點 BTC 價（`btc_open`）
- 以 `America/New_York` 時區處理夏令時。

**核心指標**（刻意只取 4+1 個以控制多重比較）：
1. **折溢價時序** `(收盤−100)/100`
2. **成交量異常 z-score**（60 日滾動，|z|≥2 標記為異常）
3. **Lead-Lag CCF**（日報酬，lag −5~+5；正 lag=STRC 領先、負 lag=BTC 領先）
4. **隔夜領先迴歸**：STRC 收盤折價 → BTC 隔夜報酬
5. **健全性檢查**：BTC 前夜報酬 → STRC 開盤跳空（已知落後關係，**應顯著**；
   若不顯著代表對齊壞了，其餘結論不可信）

**結論判定**：依 CCF 峰值落在正/負 lag、隔夜迴歸是否顯著，自動點亮紅綠燈。

## 統計限制（務必閱讀）

- 樣本僅 2025/7 至今約一年（≈ 200 多個交易日），**結論強度有限**。
- 已防多重比較（固定 4 個假設、門檻用經濟邏輯定死，不做 grid search）。
- 報告效應大小與 95% 信賴區間，不只看 p 值。
- 要下穩健結論至少需 **2~3 年**資料。本工具定位為**探索性研究，非投資建議**。

## API

| Method | 路徑 | 說明 |
|--------|------|------|
| GET | `/` | 前端頁面 |
| GET | `/api/analysis` | 回傳所有指標與結論（記憶體快取 30 分鐘；BTC K 線另有本地檔案快取 + 增量更新） |
| GET | `/api/analysis?refresh=1` | 強制重算（仍走 BTC 增量快取；要全量重抓刪 `backend/cache/btc_15m.pkl`） |

## 未來工作

- 加 Granger 因果檢定（需 statsmodels）。
- 加盤中（1m/5m）版本（Yahoo 限近 7/60 天）做更細的領先時延。
- 控制變數偏相關（SPX / MSTR / DXY）排除共同驅動造成的假相關。
