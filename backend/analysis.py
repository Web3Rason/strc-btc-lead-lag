"""5037 統計分析層。

把 data.build_dataset() 的主資料表轉成前端要的指標與圖資料。
四個核心指標 + 一個健全性檢查，刻意不貪多以控制多重比較問題。

CCF 的 lag 約定（前端會照這個標示）：
  正 lag → BTC 落後 STRC → STRC 領先
  負 lag → STRC 落後 BTC → BTC 領先
"""
import math
import numpy as np
import pandas as pd
from scipy import stats

PAR = 100.0


def _clean(v):
    """把 NaN/inf 轉成 None，讓 jsonify 不會吐 NaN。"""
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _ccf(x, y, max_lag=5):
    """互相關：corr(x_t, y_{t+k})。正 k = x 領先 y。"""
    x, y = pd.Series(x).reset_index(drop=True), pd.Series(y).reset_index(drop=True)
    lags, vals = list(range(-max_lag, max_lag + 1)), []
    for k in lags:
        pair = pd.concat([x, y.shift(-k)], axis=1).dropna()
        if len(pair) > 3:
            vals.append(float(pair.iloc[:, 0].corr(pair.iloc[:, 1])))
        else:
            vals.append(None)
    return lags, vals


def _ols(x, y):
    """單變量 OLS，回傳係數、R²、p 值與 95% 信賴區間。"""
    pair = pd.concat([pd.Series(x), pd.Series(y)], axis=1).dropna()
    pair.columns = ["x", "y"]
    n = len(pair)
    if n < 5:
        return {"n": n, "slope": None, "r2": None, "p": None,
                "ci_low": None, "ci_high": None, "points": []}
    res = stats.linregress(pair["x"], pair["y"])
    half = 1.96 * res.stderr
    return {
        "n": n,
        "slope": _clean(float(res.slope)),
        "intercept": _clean(float(res.intercept)),
        "r2": _clean(float(res.rvalue ** 2)),
        "p": _clean(float(res.pvalue)),
        "ci_low": _clean(float(res.slope - half)),
        "ci_high": _clean(float(res.slope + half)),
        "points": [{"x": _clean(float(a)), "y": _clean(float(b))}
                   for a, b in zip(pair["x"], pair["y"])],
    }


def _verdict(overnight, lags, ccf_vals, n_ccf):
    """依『排除 lag0 後的 CCF 峰值落在正/負 lag』判定紅綠燈結論。

    刻意不用「正/負 lag 群各取 any(>band)」那種未校正的多重比較，
    改看單一峰值位置，避免 lag0 同步相關外溢到 ±1 被誤判成領先/落後。
    隔夜迴歸只在 CCF 無顯著峰值時，當作補充的弱訊號，不單獨升級結論。
    """
    band = 1.96 / math.sqrt(max(n_ccf, 1))  # 單一 lag 的白噪音約略 95% 臨界值
    cand = [(l, v) for l, v in zip(lags, ccf_vals) if l != 0 and v is not None]
    peak_lag, peak_v = (max(cand, key=lambda t: abs(t[1])) if cand else (None, None))
    peak_sig = peak_v is not None and abs(peak_v) > band
    overnight_sig = (overnight["p"] is not None and overnight["p"] < 0.05
                     and overnight["n"] >= 30)

    note = "（樣本僅約一年、未對多重 lag 做校正，請保守解讀）"
    if peak_sig and peak_lag > 0:
        light, level = "yellow", "weak_lead"
        text = f"CCF 峰值落在 lag +{peak_lag}，出現 STRC 對 BTC 的微弱領先跡象，但尚不足以宣稱因果{note}。"
    elif peak_sig and peak_lag < 0:
        light, level = "red", "lag"
        text = f"CCF 峰值落在 lag {peak_lag}，STRC 較可能『落後』BTC，不適合當作領先指標{note}。"
    elif overnight_sig:
        light, level = "yellow", "weak_lead"
        text = f"CCF 無顯著峰值，但隔夜迴歸顯著，僅見到極弱的領先線索{note}。"
    else:
        light, level = "red", "none"
        text = f"CCF 無顯著峰值、隔夜迴歸不顯著，看不到 STRC 領先 BTC 的證據{note}。"
    return {"light": light, "level": level, "text": text, "confidence": "low",
            "conf_band": _clean(band), "peak_lag": peak_lag, "peak_corr": _clean(peak_v)}


def analyze(strc):
    """主分析：回傳完整、可 jsonify 的結果 dict。"""
    df = strc.dropna(subset=["btc_close", "btc_open"]).copy()

    # 1) 折溢價
    df["premium_pct"] = (df["close"] - PAR) / PAR * 100
    # 2) 成交量異常 z-score（60 日滾動）
    m = df["volume"].rolling(60, min_periods=20).mean()
    sd = df["volume"].rolling(60, min_periods=20).std()
    df["vol_z"] = (df["volume"] - m) / sd
    # 報酬（皆對齊 ET 收盤時點）
    df["strc_ret"] = df["close"].pct_change()
    df["btc_ret"] = df["btc_close"].pct_change()
    # 隔夜 BTC 報酬：當日 16:00ET 收 → 次日 09:30ET 開
    df["btc_overnight"] = df["btc_open"].shift(-1) / df["btc_close"] - 1
    # STRC 開盤跳空：今開 / 昨收
    df["strc_gap"] = df["open"] / df["close"].shift(1) - 1
    # 前夜 BTC 報酬（對齊跳空，健全性檢查用）：昨日16:00 → 今日9:30
    df["btc_overnight_prev"] = df["btc_open"] / df["btc_close"].shift(1) - 1

    # 3) Lead-Lag CCF（日報酬）
    ccf_pair = df[["strc_ret", "btc_ret"]].dropna()
    lags, ccf_vals = _ccf(ccf_pair["strc_ret"], ccf_pair["btc_ret"], max_lag=5)

    # 4) 隔夜領先迴歸：STRC 收盤折價 → BTC 隔夜報酬
    overnight = _ols(df["premium_pct"], df["btc_overnight"] * 100)
    # 5) 健全性檢查：BTC 前夜報酬 → STRC 開盤跳空（應顯著正相關）
    sanity = _ols(df["btc_overnight_prev"] * 100, df["strc_gap"] * 100)

    def series(col_y):
        out = []
        for d, row in df.iterrows():
            out.append({
                "date": d.strftime("%Y-%m-%d"),
                "btc_close": _clean(float(row["btc_close"])),
                col_y: _clean(float(row[col_y])) if pd.notna(row[col_y]) else None,
            })
        return out

    vol_series = []
    for d, row in df.iterrows():
        z = row["vol_z"]
        vol_series.append({
            "date": d.strftime("%Y-%m-%d"),
            "volume": _clean(float(row["volume"])),
            "z": _clean(float(z)) if pd.notna(z) else None,
            "anomaly": bool(pd.notna(z) and abs(z) >= 2),
            "btc_close": _clean(float(row["btc_close"])),
        })

    verdict = _verdict(overnight, lags, ccf_vals, len(ccf_pair))

    return {
        "meta": {
            "strc_points": int(len(strc)),
            "aligned_points": int(len(df)),
            "start_date": df.index[0].strftime("%Y-%m-%d") if len(df) else None,
            "end_date": df.index[-1].strftime("%Y-%m-%d") if len(df) else None,
            "latest_strc_close": _clean(float(df["close"].iloc[-1])) if len(df) else None,
            "par": PAR,
        },
        "premium_series": series("premium_pct"),
        "volume_series": vol_series,
        "ccf": {"lags": lags, "values": [_clean(v) for v in ccf_vals],
                "conf_band": _clean(1.96 / math.sqrt(max(len(ccf_pair), 1)))},
        "overnight_reg": overnight,
        "sanity_reg": sanity,
        "conclusion": verdict,
    }
