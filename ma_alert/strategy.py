# -*- coding: utf-8 -*-
"""均线粘合向上变盘策略。

思路：
  1. 均线粘合：MA5/MA10/MA20/MA30/MA60 相互靠拢（最大-最小 除以 最小 <= 阈值），
     说明股价经过长时间横盘整理，市场处于变盘临界点。
  2. 向上变盘：股价放量上穿均线束，且"刚刚"进入粘合状态（粘合天数不太多也不太少），
     配合量能放大、短期均线拐头向上，判断为"刚刚开始大涨"的启动点。
"""
import datetime
import numpy as np


def _beijing_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))


def _is_today(date_str):
    return date_str == _beijing_now().strftime("%Y-%m-%d")


def _intraday_volume_factor(now):
    """A股全天交易 240 分钟（上午 9:30-11:30，下午 13:00-15:00）；
    盘中按已交易分钟数把成交量折算为全天量能。非交易时段返回 1。"""
    t = now.hour * 60 + now.minute
    elapsed = 0
    if 570 <= t <= 690:
        elapsed = t - 570
    elif 780 <= t <= 900:
        elapsed = 120 + (t - 780)
    elif t > 900:
        elapsed = 240
    if elapsed <= 0:
        return 1.0
    return 240.0 / elapsed


def sma(values, n):
    """简单移动平均（返回长度 len(values)-n+1）"""
    return np.convolve(values, np.ones(n) / n, mode="valid")



# 技术面体检清单（把用户的技术面框架量化为可自动判断的检查项）
# ------------------------------------------------------------------
def _ema(values, n):
    """指数移动平均（按完整序列计算，返回与输入等长的数组）"""
    out = np.empty_like(values, dtype=float)
    out[0] = values[0]
    k = 2.0 / (n + 1)
    for i in range(1, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def _macd(closes):
    """MACD(12,26,9)：返回 (dif, dea) 数组"""
    dif = _ema(closes, 12) - _ema(closes, 26)
    dea = _ema(dif, 9)
    return dif, dea


def _rsi(closes, n=14):
    """RSI（Wilder 平滑）"""
    if len(closes) < n + 1:
        return np.full(len(closes), np.nan)
    diffs = np.diff(closes)
    gains = np.where(diffs > 0, diffs, 0.0)
    losses = np.where(diffs < 0, -diffs, 0.0)
    avg_gain = np.convolve(gains, np.ones(n) / n, mode="valid")
    avg_loss = np.convolve(losses, np.ones(n) / n, mode="valid")
    for i in range(1, len(avg_gain)):
        avg_gain[i] = (avg_gain[i - 1] * (n - 1) + gains[i + n - 1]) / n
        avg_loss[i] = (avg_loss[i - 1] * (n - 1) + losses[i + n - 1]) / n
    rs = np.where(avg_loss == 0, np.inf, avg_gain / np.maximum(avg_loss, 1e-10))
    out = np.full(len(closes), np.nan)
    out[n:] = 100.0 - 100.0 / (1.0 + rs)
    return out


def evaluate_technical_checklist(closes, volumes, highs, lows, opens, pcts, mas, b, vol_ratio):
    """按用户技术面框架逐项体检，返回 {label: (ok, detail)}。"""
    n = len(closes)
    now = n - 1
    chk = {}
    ma5, ma10, ma20 = mas[5][now], mas[10][now], mas[20][now]
    ma60 = mas[60][now] if 60 in mas and not np.isnan(mas[60][now]) else None

    if ma60 is not None:
        ma60_prev = mas[60][max(0, now - 10)]
        chk["60日线走平/向上"] = (ma60 >= ma60_prev * 0.995, f"MA60 {ma60:.2f}->{ma60_prev:.2f}")
        chk["站上60日线"] = (closes[now] > ma60, f"收盘{closes[now]:.2f} vs MA60 {ma60:.2f}")
    else:
        chk["60日线向上"] = (None, "上市不足60日")
        chk["站上60日线"] = (None, "上市不足60日")
    chk["站上20日线"] = (closes[now] > ma20, f"收盘{closes[now]:.2f} vs MA20 {ma20:.2f}")
    chk["多头排列"] = (ma5 > ma10 > ma20, f"MA5 {ma5:.2f}/MA10 {ma10:.2f}/MA20 {ma20:.2f}")
    chk["MA5>MA20"] = (ma5 > ma20, f"MA5 {ma5:.2f} vs MA20 {ma20:.2f}")

    if n >= 40:
        rh = max(highs[-20:]); ph = max(highs[-40:-20])
        rl = min(lows[-20:]);  pl = min(lows[-40:-20])
        chk["上升结构"] = (rh > ph * 0.995 and rl > pl * 0.995,
                          f"近高{rh:.2f}/前高{ph:.2f} 近低{rl:.2f}/前低{pl:.2f}")
    else:
        chk["上升结构"] = (None, "数据不足")

    chk["突破放量"] = (vol_ratio >= 1.5, f"量比{vol_ratio:.2f}")
    win_s = max(0, n - 25)
    wv = volumes[win_s:n]; wp = pcts[win_s:n]
    up_v = wv[wp > 0]; down_v = wv[wp <= 0]
    if len(up_v) > 2 and len(down_v) > 2:
        chk["回调缩量"] = (down_v.mean() < up_v.mean(), f"涨均量{up_v.mean():.0f}/跌均量{down_v.mean():.0f}")
    else:
        chk["回调缩量"] = (None, "样本不足")

    if n > 35:
        dif, dea = _macd(closes)
        chk["MACD零轴上方"] = (dif[now] > 0 and dea[now] > 0, f"DIF {dif[now]:.3f}/DEA {dea[now]:.3f}")
        seg_c = closes[-20:]; seg_d = dif[-20:]
        ci = int(np.argmax(seg_c)); di = int(np.argmax(seg_d))
        divergence = ci > di
        chk["MACD无顶背离"] = (not divergence, "顶背离" if divergence else "正常")
    else:
        chk["MACD零轴上方"] = (None, "数据不足")
        chk["MACD无顶背离"] = (None, "数据不足")


    if n > 20:
        r = _rsi(closes, 14)[now]
        if np.isnan(r):
            chk["RSI>50多头"] = (None, "数据不足")
            chk["RSI不超买"] = (None, "数据不足")
        else:
            chk["RSI>50多头"] = (r > 50, f"RSI {r:.1f}")
            chk["RSI不超买"] = (r < 70, f"RSI {r:.1f}")
    else:
        chk["RSI>50多头"] = (None, "数据不足")
        chk["RSI不超买"] = (None, "数据不足")

    bias = (closes[now] - ma20) / ma20 * 100 if ma20 > 0 else 0
    chk["乖离不过大"] = (bias < 15, f"乖离 {bias:.1f}%")

    body = abs(closes[now] - opens[now])
    avg5 = float(volumes[-6:-1].mean()) if n >= 6 else float(volumes[now])
    big_red = pcts[now] < -3 and volumes[now] > 1.8 * avg5
    chk["无放量大阴线"] = (not big_red, "放量大阴线" if big_red else "无")
    up_shadow = highs[now] - max(opens[now], closes[now])
    long_up = up_shadow > 2 * body and body > 0 and highs[now] >= max(highs[-20:]) * 0.97
    chk["无长上影"] = (not long_up, "长上影" if long_up else "无")

    pullback = any(closes[i] < ma20 for i in range(max(0, n - 5), n))
    stop_found = False
    for i in range(max(0, n - 6), n):
        bd = abs(closes[i] - opens[i])
        lower_shadow = min(opens[i], closes[i]) - lows[i]
        if lower_shadow > 2 * bd and bd > 0 and volumes[i] < avg5 * 1.2:
            stop_found = True
            break
    if pullback:
        chk["回踩止跌信号"] = (stop_found, "有止跌K线" if stop_found else "未见止跌K线")
    else:
        chk["回踩止跌信号"] = (None, "近期无回踩")

    # 统一转成 Python bool，避免 np.bool_ 判断问题
    out = {}
    for k, (ok, det) in chk.items():
        out[k] = (bool(ok) if ok is not None else None, det)
    return out


class MaBreakoutStrategy:
    def __init__(self, cfg):
        self.periods = cfg.get("ma_periods", [5, 10, 20, 30, 60])
        self.cluster_pct = float(cfg.get("cluster_pct", 4.0))          # 粘合度阈值 %
        self.converge_min = int(cfg.get("converge_min_days", 3))       # 最少粘合天数
        self.converge_max = int(cfg.get("converge_max_days", 60))      # 最多粘合天数（>60=老横盘很久，默认排除）
        self.base_range_pct = float(cfg.get("base_range_pct", 18.0))   # 粘合期内股价最大波动幅度 %（排除慢牛假粘合）
        self.breakout_lookback = int(cfg.get("breakout_lookback", 3))  # 突破发生在最近 N 天内
        self.volume_ratio = float(cfg.get("volume_ratio", 1.5))        # 突破日量能 / 前5日均量
        self.min_breakout_gain = float(cfg.get("min_breakout_gain", 1.0))  # 突破日最小涨幅 %
        self.min_price = float(cfg.get("min_price", 2.0))
        self.max_price = float(cfg.get("max_price", 600.0))
        self.exclude_st = bool(cfg.get("exclude_st", True))
        self.min_list_days = int(cfg.get("min_list_days", 60))         # 上市至少 N 个交易日
        self.min_turnover = float(cfg.get("min_turnover", 0.5))        # 换手率下限 %
        self.max_turnover = float(cfg.get("max_turnover", 30.0))       # 换手率上限 %
        self.min_amount_yi = float(cfg.get("min_amount_yi", 1.0))       # 成交额下限（亿，人气）
        self.min_amp_pct = float(cfg.get("min_amp_pct", 1.8))           # 近10日日均振幅下限 %（排除织布机）
        self.min_mv = float(cfg.get("min_market_cap_yi", 30.0))        # 总市值下限（亿）
        self.min_score = float(cfg.get("min_score", 60.0))             # 入选最低评分
        self.adjust_intraday_volume = bool(cfg.get("adjust_intraday_volume", True))  # 盘中量能按时间折算
        self.show_checklist = bool(cfg.get("show_checklist", True))          # 推送时附带技术面体检清单
        self.require_core = bool(cfg.get("require_core_conditions", True))  # 核心条件不满足则过滤

    # ------------------------------------------------------------------
    def _build_mas(self, closes):
        n = len(closes)
        mas = {}
        for p in self.periods:
            if n >= p:
                v = sma(closes, p)
                pad = n - len(v)
                mas[p] = np.concatenate([np.full(pad, np.nan), v])
            else:
                mas[p] = np.full(n, np.nan)
        return mas

    def _cluster_at(self, mas, i):
        vals = [mas[p][i] for p in self.periods if not np.isnan(mas[p][i])]
        if len(vals) < len(self.periods) - 1:
            return None
        lo, hi = min(vals), max(vals)
        if lo <= 0:
            return None
        return (hi - lo) / lo * 100.0

    def _max_ma_at(self, mas, i):
        return max(mas[p][i] for p in self.periods if not np.isnan(mas[p][i]))

    # ------------------------------------------------------------------
    def analyze(self, code, name, bars, spot=None):
        """对单只股票执行策略。

        返回命中结果 dict 或 None。
        """
        need = max(self.periods) + self.breakout_lookback + 10
        if len(bars) < need:
            return None
        if self.exclude_st and ("ST" in name.upper() or "退" in name):
            return None
        if name.startswith("N") or name.startswith("C"):  # 上市首日/次新
            return None

        closes = np.array([b["close"] for b in bars], dtype=float)
        volumes = np.array([b["volume"] for b in bars], dtype=float)
        highs = np.array([b["high"] for b in bars], dtype=float)
        lows = np.array([b["low"] for b in bars], dtype=float)
        opens = np.array([b["open"] for b in bars], dtype=float)
        pcts = np.array([b["pct_chg"] for b in bars], dtype=float)
        n = len(closes)
        mas = self._build_mas(closes)

        # 1) 找最近突破日：收盘价上穿均线束
        breakout_day = None
        for b in range(n - self.breakout_lookback, n):
            if b <= 0:
                continue
            try:
                prev_max = self._max_ma_at(mas, b - 1)
                cur_max = self._max_ma_at(mas, b)
            except ValueError:
                continue
            if closes[b] > cur_max and closes[b - 1] <= prev_max:
                breakout_day = b
                break
        if breakout_day is None:
            return None
        b = breakout_day

        # 2) 突破日涨幅要求（"开始大涨"）
        gain = float(pcts[b])
        if gain < self.min_breakout_gain:
            return None

        # 3) 粘合状态：从突破前一天往前数，均线束连续保持粘合的天数
        cnt = 0
        i = b - 1
        while i >= 0:
            cp = self._cluster_at(mas, i)
            if cp is None or cp > self.cluster_pct:
                break
            cnt += 1
            i -= 1
        if cnt < self.converge_min or cnt > self.converge_max:
            return None
        # 粘合期内股价必须窄幅震荡（排除"慢牛爬升"造成的假粘合）
        seg_start = max(0, b - cnt - 3)
        seg = closes[seg_start:b]
        if len(seg) >= 5:
            rng = (float(seg.max()) - float(seg.min())) / float(seg.min()) * 100.0
            if rng > self.base_range_pct:
                return None
        cluster_val = self._cluster_at(mas, b - 1)

        # 4) 量能放大（盘中把今日成交量按已交易时间折算为全天量）
        prev5 = volumes[b - 6:b - 1] if b >= 6 else volumes[:b - 1]
        base_vol = float(prev5.mean()) if len(prev5) else float(volumes[b])
        b_vol = float(volumes[b])
        if self.adjust_intraday_volume and _is_today(bars[b]["date"]):
            b_vol *= _intraday_volume_factor(_beijing_now())
        vol_ratio = float(b_vol / base_vol) if base_vol > 0 else 1.0
        if vol_ratio < self.volume_ratio:
            return None

        # 织布机排除：近10日日均振幅过低 = 股价几乎不动（低人气）
        if self.min_amp_pct > 0 and n >= 10:
            amp_win = (highs[n - 10:n] - lows[n - 10:n]) / np.maximum(closes[n - 10:n], 1e-9) * 100
            if float(np.mean(amp_win)) < self.min_amp_pct:
                return None

        # 5) 短期趋势向上：MA5 > MA10
        ma5, ma10 = float(mas[5][b]), float(mas[10][b])
        ma20 = float(mas[20][b])
        if np.isnan(ma5) or np.isnan(ma10) or not (ma5 > ma10):
            return None
        ma20_ref = float(mas[20][max(0, b - 5)])
        ma20_slope = (ma20 - ma20_ref) / ma20_ref * 100.0 if ma20_ref > 0 else 0.0

        # 6) 突破前 20 日内是否已有过突破（首次突破加分）
        first_break = True
        for i in range(max(0, b - 20), b):
            try:
                if closes[i] > self._max_ma_at(mas, i):
                    first_break = False
                    break
            except ValueError:
                continue

        # ---------------- 评分 ----------------
        tight = max(0.0, 30 * (1 - cluster_val / 8.0))
        vol_score = min(25.0, max(0.0, (vol_ratio - 1) / 2.0 * 25))
        gain_score = min(25.0, max(0.0, (gain - 1) / 6.0 * 25))
        align = 10.0
        slope_score = min(10.0, max(0.0, (ma20_slope - 0.3) * 20))
        fresh = {0: 10, 1: 7, 2: 4}.get(n - 1 - b, 0)
        bonus = 5.0 if first_break else 0.0
        score = round(min(100.0, tight + vol_score + gain_score + align + slope_score + fresh + bonus), 1)

        # 最新一天（可能仍在交易中）的实时/最新信息
        last = bars[-1]
        turnover = float(last["turnover"])
        amount_yi = round(float(last["amount"]) / 1e8, 2)
        if spot:
            if isinstance(spot.get("turnover"), (int, float)):
                turnover = round(float(spot["turnover"]), 2)
            if isinstance(spot.get("amount"), (int, float)):
                amount_yi = round(float(spot["amount"]) / 1e8, 2)
        result = {
            "code": code,
            "name": name,
            "date": last["date"],
            "price": round(float(last["close"]), 2),
            "pct_chg": round(float(pcts[-1]), 2),
            "breakout_date": bars[b]["date"],
            "days_ago": int(n - 1 - b),
            "breakout_gain": round(float(gain), 2),
            "cluster_pct": round(float(cluster_val), 2),
            "converge_days": int(cnt),
            "vol_ratio": round(float(vol_ratio), 2),
            "turnover": turnover,
            "amount_yi": amount_yi,
            "ma5": round(float(ma5), 2),
            "ma10": round(float(ma10), 2),
            "ma20": round(float(ma20), 2),
            "ma20_slope": round(float(ma20_slope), 2),
            "first_break": bool(first_break),
            "score": round(float(score), 1),
        }
        if self.show_checklist:
            try:
                chk = evaluate_technical_checklist(closes, volumes, highs, lows, opens, pcts, mas, b, vol_ratio)
                result["checklist"] = chk
                core_items = ["60日线走平/向上", "站上60日线", "站上20日线", "MA5>MA20"]
                core_fail = [k for k in core_items if chk.get(k) and chk[k][0] is False]
                result["core_fail"] = core_fail
                result["check_pass"] = sum(1 for v in chk.values() if v[0] is True)
                result["check_total"] = sum(1 for v in chk.values() if v[0] is not None)
            except Exception:  # noqa: BLE001
                pass
            if self.require_core and result.get("core_fail"):
                return None
        if spot:
            result["spot_pct"] = spot.get("pct_chg")
            result["spot_vol_ratio"] = spot.get("vol_ratio")
            result["spot_turnover"] = spot.get("turnover")
            mv = spot.get("total_mv")
            result["total_mv_yi"] = round(mv / 1e8, 1) if isinstance(mv, (int, float)) and mv else None
        return result

    # ------------------------------------------------------------------
    def filter_candidates(self, spots):
        """用实时快照做粗筛，减少需要拉取 K 线的数量。"""
        out = []
        for s in spots:
            code, name = s["code"], s["name"]
            if not code or not name:
                continue
            if self.exclude_st and ("ST" in name.upper() or "退" in name):
                continue
            if name.startswith(("N", "C")):
                continue
            price = s.get("price")
            if not isinstance(price, (int, float)) or not (self.min_price <= price <= self.max_price):
                continue
            pct = s.get("pct_chg")
            if not isinstance(pct, (int, float)) or pct < -3.0:  # 大跌的直接排除
                continue
            vr = s.get("vol_ratio")
            if not isinstance(vr, (int, float)) or vr < 1.0:
                continue
            # 人气过滤：换手率/成交额按盘中已交易时间折算为全天口径再判断
            factor = _intraday_volume_factor(_beijing_now())
            to = s.get("turnover")
            if isinstance(to, (int, float)):
                eff_to = to * factor
                if not (self.min_turnover <= eff_to <= self.max_turnover):
                    continue
            amt = s.get("amount")
            if isinstance(amt, (int, float)) and amt > 0:
                if amt * factor / 1e8 < self.min_amount_yi:
                    continue
            mv = s.get("total_mv")
            if isinstance(mv, (int, float)) and mv > 0 and mv / 1e8 < self.min_mv:
                continue
            out.append(s)
        return out

    def run(self, spots, klines):
        """对粗筛后的股票执行完整策略，返回按评分降序的命中列表。"""
        hits = []
        for s in spots:
            code = s["code"]
            name, bars = klines.get(code, ("", []))
            if not bars:
                continue
            if len(bars) < self.min_list_days:
                continue
            r = self.analyze(code, name or s["name"], bars, spot=s)
            if r and r["score"] >= self.min_score:
                hits.append(r)
        hits.sort(key=lambda x: x["score"], reverse=True)
        return hits
