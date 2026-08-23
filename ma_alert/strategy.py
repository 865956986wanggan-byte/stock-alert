# -*- coding: utf-8 -*-
"""均线粘合向上变盘策略。

思路：
  1. 均线粘合：MA5/MA10/MA20/MA30/MA60 相互靠拢（最大-最小 除以 最小 <= 阈值），
     说明股价经过长时间横盘整理，市场处于变盘临界点。
  2. 向上变盘：股价放量上穿均线束，且"刚刚"进入粘合状态（粘合天数不太多也不太少），
     配合量能放大、短期均线拐头向上，判断为"刚刚开始大涨"的启动点。
"""
import numpy as np


def sma(values, n):
    """简单移动平均（返回长度 len(values)-n+1）"""
    return np.convolve(values, np.ones(n) / n, mode="valid")


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
        self.min_mv = float(cfg.get("min_market_cap_yi", 30.0))        # 总市值下限（亿）
        self.min_score = float(cfg.get("min_score", 60.0))             # 入选最低评分

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

        # 4) 量能放大
        prev5 = volumes[b - 6:b - 1] if b >= 6 else volumes[:b - 1]
        base_vol = float(prev5.mean()) if len(prev5) else float(volumes[b])
        vol_ratio = float(volumes[b] / base_vol) if base_vol > 0 else 1.0
        if vol_ratio < self.volume_ratio:
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
            to = s.get("turnover")
            if isinstance(to, (int, float)) and not (self.min_turnover <= to <= self.max_turnover):
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
