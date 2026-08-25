"""資料抓取層。

STRC：Strategy 的 Stretch 永續特別股（Nasdaq:STRC），走 Yahoo Finance 日線。
BTC ：走 Binance 現貨 15m K 線（本地檔案快取 + 增量更新），重建「對齊 STRC 交易時點」的價格錨點。

兩邊都用免金鑰的公開 endpoint（Yahoo chart v8 / Binance klines），
以 requests 直連，方便後續用 numpy/scipy 做統計。
"""
import os
import time
import requests
import pandas as pd

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; STRC-Lead/1.0)"}

ET = "America/New_York"
PAR = 100.0  # STRC 面值

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
BTC_CACHE = os.path.join(CACHE_DIR, "btc_15m.pkl")


def fetch_strc_daily(rng="2y"):
    """抓 STRC 日線 OHLCV，索引用『美東交易日期』(tz-naive)。"""
    url = YAHOO_CHART.format(sym="STRC")
    r = requests.get(url, params={"range": rng, "interval": "1d"},
                     headers=HEADERS, timeout=20)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    rows = []
    for i, t in enumerate(ts):
        o, c, v = q["open"][i], q["close"][i], q["volume"][i]
        if o is None or c is None:
            continue
        # Yahoo ts 為 UTC 秒，轉 ET 取交易日期
        et = pd.Timestamp(t, unit="s", tz="UTC").tz_convert(ET)
        rows.append({
            "date": et.normalize().tz_localize(None),
            "open": float(o),
            "close": float(c),
            "volume": float(v or 0),
        })
    df = (pd.DataFrame(rows)
          .drop_duplicates("date")
          .set_index("date")
          .sort_index())
    return df


STEP_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}


def fetch_btc_klines(start_ms, end_ms, interval="15m"):
    """分頁抓 Binance BTCUSDT K 線收盤價。

    索引用 ET 的『收盤時間 closeTime』而非開盤時間，這樣 series.asof(t) 取到的是
    『t 當下最近一根已收 K 的收盤價』＝該時點的價格（避免抓到尚未走完的未來那根）。
    interval 用 15m：09:30 / 16:00 ET 恰好落在 15m K 邊界，錨點對齊精準到秒。
    """
    step = STEP_MS[interval]
    out = []
    cur = start_ms
    while cur < end_ms:
        r = requests.get(BINANCE_KLINES, params={
            "symbol": "BTCUSDT", "interval": interval,
            "startTime": cur, "limit": 1000,
        }, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        out += data
        cur = data[-1][0] + step
        if len(data) < 1000:
            break
        time.sleep(0.2)  # 禮貌性節流，避免觸發 Binance 限流
    # k = [openTime, o, h, l, c, v, closeTime, ...]；用 closeTime 當索引
    idx = (pd.to_datetime([k[6] for k in out], unit="ms", utc=True)
           .tz_convert(ET))
    s = pd.Series([float(k[4]) for k in out], index=idx)
    s = s[~s.index.duplicated()].sort_index()
    return s


def get_btc_cached(start_ms, end_ms, interval="15m"):
    """BTC K 線：本地檔案快取 + 增量更新。

    第一次沒有快取檔時全量抓（~32 趟，較慢）；之後啟動只讀檔，
    再從『上次最後一根』補抓到現在（通常 1~2 趟），打開就快。
    快取存 backend/cache/btc_15m.pkl（pickle 保留 tz 索引、無額外依賴）。
    """
    cached = None
    if os.path.exists(BTC_CACHE):
        try:
            cached = pd.read_pickle(BTC_CACHE)
        except Exception:
            cached = None  # 快取壞了就當沒有，重抓

    if cached is not None and len(cached):
        last_ms = int(cached.index[-1].tz_convert("UTC").timestamp() * 1000)
        # 從最後一根稍前開始補抓，確保銜接無縫
        fresh = fetch_btc_klines(last_ms - STEP_MS[interval], end_ms, interval)
        s = pd.concat([cached, fresh])
        s = s[~s.index.duplicated(keep="last")].sort_index()
    else:
        s = fetch_btc_klines(start_ms, end_ms, interval)

    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        s.to_pickle(BTC_CACHE)
    except Exception:
        pass  # 寫快取失敗不影響本次回傳
    return s


def _asof(series, t):
    """回傳 series 中 <= t 的最近一筆值（時點對齊用）。"""
    if series.empty:
        return float("nan")
    v = series.asof(t)
    return float(v) if pd.notna(v) else float("nan")


def build_dataset(rng="2y"):
    """組出主資料表：STRC OHLCV + 對齊到 STRC 收/開盤時點的 BTC 價。

    錨點對齊（非 forward-fill）：
      - btc_close = STRC 收盤 16:00 ET 當下的 BTC 價
      - btc_open  = STRC 開盤 09:30 ET 當下的 BTC 價
    """
    strc = fetch_strc_daily(rng)
    if strc.empty:
        raise RuntimeError("STRC 無資料")

    start_et = strc.index[0].tz_localize(ET)
    start_ms = int(start_et.tz_convert("UTC").timestamp() * 1000) - 2 * 86_400_000
    end_ms = int(time.time() * 1000)
    btc = get_btc_cached(start_ms, end_ms)

    close_anchor, open_anchor = {}, {}
    for d in strc.index:
        t_close = (d + pd.Timedelta(hours=16)).tz_localize(ET)
        t_open = (d + pd.Timedelta(hours=9, minutes=30)).tz_localize(ET)
        close_anchor[d] = _asof(btc, t_close)
        open_anchor[d] = _asof(btc, t_open)

    strc["btc_close"] = pd.Series(close_anchor)
    strc["btc_open"] = pd.Series(open_anchor)
    return strc
