# -*- coding: utf-8 -*-
"""A 股行情数据获取（多数据源自动切换，仅用标准库）。

数据来源：
  - 实时快照（全市场列表）: 东方财富 clist（push2 / push2delay 自动切换，分页拉取）
  - 日 K 线（前复权）     : 东方财富 kline -> 失败自动切换到腾讯行情
"""
import datetime
import json
import math
import os
import time
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

# 沪深 A 股：深主板 + 创业板 + 沪主板 + 科创板
FS_A = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"

CLIST_HOSTS = [
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://push2delay.eastmoney.com/api/qt/clist/get",
]
KLINE_EM = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
KLINE_TX_HOSTS = [
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
]
KLINE_SINA = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"


def _get_json(url, retries=3, timeout=20):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.6 * (i + 1))
    raise RuntimeError(f"请求失败: {url} -> {last}")


# ----------------------------------------------------------------------
# 全市场实时快照
# ----------------------------------------------------------------------
def _parse_spot(d):
    return {
        "code": str(d.get("f12", "")),
        "name": d.get("f14", ""),
        "price": d.get("f2"),
        "pct_chg": d.get("f3"),
        "volume": d.get("f5"),          # 手
        "amount": d.get("f6"),          # 元
        "turnover": d.get("f8"),        # %
        "vol_ratio": d.get("f10"),      # 量比
        "total_mv": d.get("f20"),       # 元
        "float_mv": d.get("f21"),       # 元
        "pb": d.get("f23"),
    }


def _clist_page(host, pn, pz=100):
    params = {
        "pn": pn, "pz": pz, "po": 1, "np": 1, "fltt": 2, "invt": 2,
        "fid": "f12", "fs": FS_A,
        "fields": "f12,f14,f2,f3,f5,f6,f8,f10,f20,f21,f23",
    }
    url = host + "?" + urllib.parse.urlencode(params)
    return _get_json(url)


def fetch_spot_all(pz=100, max_pages=80, workers=6, retries=2):
    """获取全市场实时行情快照（自动切换主机 + 并发分页拉取）。

    容错：单个分页失败自动跳过不中断；超过 30% 分页失败整体重试 retries 次。
    字段: code, name, price, pct_chg, volume(手), amount(元),
          turnover(%), vol_ratio(量比), total_mv(元), float_mv(元), pb
    """
    last_err = None
    for attempt in range(max(1, retries)):
        try:
            return _fetch_spot_all_impl(pz, max_pages, workers)
        except RuntimeError as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
    raise last_err if last_err else RuntimeError("行情列表获取失败")


def _fetch_spot_all_impl(pz=100, max_pages=80, workers=6):
    """实际拉取逻辑（单次尝试）。"""
    stocks, host, total = [], None, None
    for h in CLIST_HOSTS:
        try:
            d = _clist_page(h, 1, pz=pz)
            data = d.get("data") or {}
            total = data.get("total")
            stocks = [_parse_spot(x) for x in (data.get("diff") or []) if x]
            host = h
            break
        except Exception:  # noqa: BLE001
            continue
    if host is None:
        raise RuntimeError("东方财富行情列表接口全部不可用")

    pages = list(range(2, min(max_pages, math.ceil((total or len(stocks)) / pz)) + 1))
    if pages:
        def _safe_page(pn):
            try:
                return _clist_page(host, pn, pz=pz)
            except Exception:  # noqa: BLE001
                return None

        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_safe_page, pages))
        got = 0
        for d in results:
            if not d:
                continue
            try:
                diff = (d.get("data") or {}).get("diff") or []
                stocks += [_parse_spot(x) for x in diff if x]
                got += 1
            except Exception:  # noqa: BLE001
                continue
        if got < max(1, int(len(pages) * 0.7)):
            raise RuntimeError(f"行情分页获取失败过多（成功 {got}/{len(pages)}），可能被限流")
    return stocks


# ----------------------------------------------------------------------
# 日 K 线
# ----------------------------------------------------------------------
def _tx_market(code):
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith(("4", "8")):
        return "bj"
    return "sz"


def _kline_eastmoney(code, lmt=130, klt=101, fqt=1):
    params = {
        "secid": secid_of(code),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": klt, "fqt": fqt, "beg": 0, "end": 20500101, "lmt": lmt,
    }
    url = KLINE_EM + "?" + urllib.parse.urlencode(params)
    data = _get_json(url, retries=1, timeout=8)
    d = (data or {}).get("data") or {}
    bars = []
    for line in d.get("klines") or []:
        p = line.split(",")
        if len(p) < 11:
            continue
        bars.append({
            "date": p[0],
            "open": float(p[1]), "close": float(p[2]),
            "high": float(p[3]), "low": float(p[4]),
            "volume": float(p[5]), "amount": float(p[6]),
            "amplitude": float(p[7]), "pct_chg": float(p[8]),
            "chg": float(p[9]), "turnover": float(p[10]),
        })
    return d.get("name", ""), bars


def _kline_tencent(code, lmt=320):
    """腾讯行情日 K 线（前复权）。多主机自动切换。"""
    mkt = _tx_market(code)
    end = datetime.date.today().isoformat()
    raw, last_err = None, None
    for host in KLINE_TX_HOSTS:
        try:
            url = f"{host}?param={mkt}{code},day,2020-01-01,{end},{lmt},qfq"
            raw = _get_json(url, retries=1, timeout=10)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    if raw is None:
        raise RuntimeError(f"腾讯行情不可用: {last_err}")
    node = ((raw or {}).get("data") or {}).get(f"{mkt}{code}") or {}
    raw_list = node.get("qfqday") or node.get("day") or []
    bars = []
    for p in raw_list:
        if len(p) < 6:
            continue
        try:
            o, c, h, l = float(p[1]), float(p[2]), float(p[3]), float(p[4])
        except (TypeError, ValueError):
            continue
        vol = float(p[5]) if isinstance(p[5], (int, float, str)) else 0.0
        pct = (c / o - 1) * 100 if o else 0.0
        bars.append({
            "date": p[0],
            "open": o, "close": c, "high": h, "low": l,
            "volume": vol, "amount": 0.0,
            "amplitude": (h - l) / l * 100 if l else 0.0,
            "pct_chg": pct, "chg": c - o, "turnover": 0.0,
        })
    # 腾讯此接口返回的名称字段不可靠，统一返回空，由调用方用行情列表名称
    return "", bars


def _kline_sina(code, lmt=400):
    """新浪行情日 K 线（不复权），返回 (name, bars)。volume 单位转为手。"""
    mkt = _tx_market(code)
    url = (f"{KLINE_SINA}?symbol={mkt}{code}&scale=240&ma=no&datalen={lmt}")
    data = _get_json(url, retries=2, timeout=10)
    bars = []
    prev_close = None
    for row in data or []:
        try:
            o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
            vol = float(row.get("volume") or 0) / 100.0  # 股 -> 手
        except (TypeError, ValueError, KeyError):
            continue
        pct = (c / prev_close - 1) * 100 if prev_close else 0.0
        bars.append({
            "date": row["day"],
            "open": o, "close": c, "high": h, "low": l,
            "volume": vol, "amount": 0.0,
            "amplitude": (h - l) / l * 100 if l else 0.0,
            "pct_chg": pct, "chg": c - prev_close if prev_close else 0.0,
            "turnover": 0.0,
        })
        prev_close = c
    return "", bars


def fetch_index_kline(code="sh000001", lmt=100):
    """获取大盘指数日K线（腾讯行情），用于环境过滤。返回 [{date, open, close, high, low, volume}]"""
    end = datetime.date.today().isoformat()
    data, last_err = None, None
    for host in KLINE_TX_HOSTS:
        try:
            url = f"{host}?param={code},day,2025-01-01,{end},{lmt},qfq"
            data = _get_json(url, retries=1, timeout=10)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    if data is None:
        raise RuntimeError(f"指数行情不可用: {last_err}")
    node = ((data or {}).get("data") or {}).get(code) or {}
    raw = node.get("qfqday") or node.get("day") or []
    bars = []
    for p in raw:
        if len(p) < 6:
            continue
        try:
            bars.append({
                "date": p[0], "open": float(p[1]), "close": float(p[2]),
                "high": float(p[3]), "low": float(p[4]), "volume": float(p[5]),
            })
        except (TypeError, ValueError):
            continue
    return bars


def secid_of(code):
    """东方财富 secid: 沪市 1.xxxxxx, 深市/北交所 0.xxxxxx"""
    if code.startswith(("6", "9")):
        return "1." + code
    return "0." + code


def fetch_kline(code, lmt=130, klt=101, fqt=1):
    """获取日 K 线（前复权）。依次尝试：腾讯 -> 东方财富 -> 新浪。

    返回 (name, bars)，bars 按日期升序，每项含 date/open/close/high/low/
    volume(手)/amount/amplitude/pct_chg/chg/turnover。
    """
    errs = []
    for fn in (_kline_tencent, _kline_eastmoney, _kline_sina):
        try:
            if fn is _kline_tencent:
                return fn(code, lmt=max(lmt * 3, 320))
            if fn is _kline_eastmoney:
                return fn(code, lmt=lmt, klt=klt, fqt=fqt)
            return fn(code, lmt=max(lmt * 3, 320))
        except Exception as e:  # noqa: BLE001
            errs.append(f"{fn.__name__}: {e}")
    raise RuntimeError("所有K线数据源均失败: " + "; ".join(errs))


CACHE_DIR = None  # 由 main.py 通过 set_kline_cache_dir 设置


def set_kline_cache_dir(path):
    global CACHE_DIR
    CACHE_DIR = path
    if path:
        os.makedirs(path, exist_ok=True)


def _cache_path(code):
    return os.path.join(CACHE_DIR, f"{code}.json") if CACHE_DIR else None


def _load_cache(code, ttl_min):
    """读取缓存，返回 (name, bars) 或 None；超过 TTL 视为过期。"""
    path = _cache_path(code)
    if not path or not os.path.exists(path):
        return None
    try:
        if ttl_min and time.time() - os.path.getmtime(path) > ttl_min * 60:
            return None
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d.get("name", ""), d.get("bars", [])
    except Exception:  # noqa: BLE001
        return None


def _save_cache(code, name, bars):
    path = _cache_path(code)
    if not path:
        return
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"name": name, "bars": bars}, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        pass


def fetch_klines_batch(codes, workers=8, lmt=130, ttl_min=60, request_delay=0.08):
    """并发批量获取日 K 线（带本地磁盘缓存 + 请求限速），返回 {code: (name, bars)}。

    - 缓存未过期直接复用，避免重复请求被数据源限流；
    - 请求之间加微小间隔，降低触发风控的概率；
    - 拉取失败时回退使用缓存（哪怕已过期），保证筛选仍可运行。
    """
    out = {}

    def one(code):
        cached = _load_cache(code, ttl_min)
        if cached and cached[1]:
            return code, cached
        try:
            name, bars = fetch_kline(code, lmt=lmt)
            if request_delay:
                time.sleep(request_delay)  # 仅实际请求后限速，降低风控风险
            if bars:
                _save_cache(code, name, bars)
                return code, (name, bars)
        except Exception:  # noqa: BLE001
            pass
        stale = _load_cache(code, None)
        return code, stale if stale else ("", [])

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, c) for c in codes]
        for f in futs:
            code, res = f.result()
            out[code] = res
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    spots = fetch_spot_all()
    print("全市场股票数:", len(spots))
    for s in spots[:5]:
        print(s)
    name, bars = fetch_kline("600519", lmt=5)
    print("\n示例 K 线:", name, len(bars))
    for b in bars[-3:]:
        print(b)
