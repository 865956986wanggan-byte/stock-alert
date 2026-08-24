# -*- coding: utf-8 -*-
"""A 股均线粘合向上变盘筛选提醒工具 主入口。

用法：
  python main.py once     # 立即筛选一次并提醒（默认）
  python main.py watch    # 盘中定时监控：每 N 分钟跑一次，只提醒新出现的信号
  python main.py report   # 只生成报告，不推送
  python main.py config   # 打印当前配置
"""
import json
import os
import sys
import time

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
sys.path.insert(0, PROJECT_DIR)

from ma_alert import data_source, strategy, alerts, report  # noqa: E402

CONFIG_PATH = os.path.join(PROJECT_DIR, "config.json")
OUT_DIR = os.path.join(PROJECT_DIR, "outputs")
STATE_PATH = os.path.join(OUT_DIR, "seen.json")


def load_config():
    path = CONFIG_PATH if os.path.exists(CONFIG_PATH) else os.path.join(PROJECT_DIR, "config.example.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # 支持环境变量覆盖（云端部署时密钥通过环境变量传入，避免写进代码仓库）
    sk = os.environ.get("SERVERCHAN_SENDKEY")
    if sk:
        cfg["alerts"]["serverchan"] = {"enabled": True, "send_key": sk}
    return cfg


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            pass
    return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


SPOT_CACHE_PATH = os.path.join(OUT_DIR, "spot_cache.json")


def _save_spot_cache(cands):
    try:
        os.makedirs(os.path.dirname(SPOT_CACHE_PATH), exist_ok=True)
        slim = [{
            "code": s.get("code"), "name": s.get("name", ""),
            "price": s.get("price"), "pct_chg": s.get("pct_chg"),
            "turnover": s.get("turnover"), "total_mv": s.get("total_mv"),
        } for s in cands]
        with open(SPOT_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(slim, f, ensure_ascii=False)
    except OSError:
        pass


def _load_spot_cache():
    try:
        with open(SPOT_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def run_once(cfg, do_notify=True, prev_seen=None, cache_ttl=None):
    st = strategy.MaBreakoutStrategy(cfg)
    t0 = time.time()
    data_source.set_kline_cache_dir(os.path.join(OUT_DIR, "kline_cache"))
    print("==> 获取全市场实时快照 ...")
    try:
        spots = data_source.fetch_spot_all()
        print(f"    全市场 {len(spots)} 只，粗筛中 ...")
        cands = st.filter_candidates(spots)
        _save_spot_cache(cands)
    except Exception as e:  # noqa: BLE001
        print(f"    行情列表获取失败（{e}），改用上次成功保存的候选列表 ...")
        cands = _load_spot_cache()
        if not cands:
            raise RuntimeError("行情列表获取失败且无本地缓存可用") from e
    print(f"    粗筛后 {len(cands)} 只，开始拉取日K线（带缓存）...")
    codes = [s["code"] for s in cands]
    ttl = cfg.get("cache_ttl_min", 60) if cache_ttl is None else cache_ttl
    klines = data_source.fetch_klines_batch(
        codes, workers=cfg.get("fetch_workers", 8),
        ttl_min=ttl, request_delay=cfg.get("request_delay", 0.08))
    ok = sum(1 for _, b in klines.values() if b)
    print(f"    K线获取完成 {ok}/{len(codes)}，耗时 {time.time() - t0:.1f}s")

    hits = st.run(cands, klines)
    report_dates = [b[-1]["date"] for _, b in klines.values() if b]
    report_date = max(report_dates) if report_dates else time.strftime("%Y-%m-%d")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n== 筛选完成：{len(hits)} 只命中（{now}）")

    meta = {
        "title": "A股均线粘合向上变盘提醒",
        "updated": now,
        "total": len(spots),
        "scanned": len(cands),
        "params": {k: cfg.get(k) for k in
                   ("ma_periods", "cluster_pct", "converge_min_days", "converge_max_days",
                    "breakout_lookback", "volume_ratio", "min_breakout_gain")},
    }
    files = report.save_reports(OUT_DIR, hits, meta)
    print("报告：", files["html"])

    if do_notify:
        if prev_seen is not None:
            # watch 模式：只提醒新信号
            old = set(prev_seen.get("signals", []))
            new_hits = [h for h in hits if f"{h['code']}|{h['breakout_date']}" not in old]
            if new_hits:
                alerts.notify(cfg, new_hits)
            else:
                print("（无新信号，不重复提醒）")
        else:
            alerts.notify(cfg, hits)
    return hits, report_date


def run_daily(cfg):
    """每日自动推送模式（供计划任务使用）。

    - 只在数据出现新交易日时推送一次：周末/节假日数据不变 -> 不重复打扰；
    - 同一天重复运行 -> 不重复推送；
    - 有信号推信号，无信号按 notify_when_empty 决定是否推"今日无信号"。
    """
    hits, report_date = run_once(cfg, do_notify=False)  # 先出报告，再决定是否推送
    state = load_state()
    last = state.get("last_report_date")
    if last == report_date:
        print(f"== 数据日期 {report_date} 已推送过，跳过（避免重复/周末重复推送）")
        return
    if hits:
        results = alerts.notify(cfg, hits)
    elif cfg.get("notify_when_empty", True):
        results = alerts.notify(cfg, [])  # 推送"今日暂无符合条件的股票"
    else:
        print("== 今日无信号，且 notify_when_empty=false，不推送")
        return
    pushed = any(v for k, v in results.items() if k != "console" and v)
    if pushed:
        state["last_report_date"] = report_date
        save_state(state)
        print(f"== 已推送数据日期：{report_date}")
    else:
        print("== 所有提醒渠道都失败了，未记录状态，下次运行会自动重试")


def watch_loop(cfg):
    interval = int(cfg.get("watch_interval_min", 5))
    state = load_state()
    seen = set(state.get("signals", []))
    print(f"== 盘中监控模式：每 {interval} 分钟扫描一次，仅提醒新信号（Ctrl+C 退出）")
    while True:
        try:
            hits, _ = run_once(cfg, do_notify=True, prev_seen=seen,
                              cache_ttl=cfg.get("watch_cache_ttl_min", 10))
            for h in hits:
                seen.add(f"{h['code']}|{h['breakout_date']}")
            save_state({"signals": sorted(seen)})
        except KeyboardInterrupt:
            print("\n退出监控。")
            break
        except Exception as e:  # noqa: BLE001
            print("扫描出错：", e)
        time.sleep(interval * 60)


def test_push(cfg):
    """发送测试消息，验证提醒渠道是否配置正确。"""
    a = cfg.get("alerts", {})
    demo = ("【测试】均线粘合向上变盘提醒已配置成功！\n"
            "如果收到这条消息，说明微信提醒已生效。\n"
            "示例命中：海尔生物(688139) 评分73.2 现价29.54 量比3.84")
    print("正在测试以下渠道：")
    results = []
    if a.get("windows_toast", True):
        ok = alerts.windows_toast("均线粘合提醒测试", demo)
        results.append(("Windows通知", ok))
        print("  Windows通知:", "OK" if ok else "失败")
    if a.get("serverchan", {}).get("enabled"):
        try:
            alerts.serverchan(a["serverchan"]["send_key"], "均线粘合提醒测试", demo)
            results.append(("Server酱", True))
            print("  Server酱: OK")
        except Exception as e:  # noqa: BLE001
            results.append(("Server酱", False))
            print("  Server酱: 失败 ->", e)
    if a.get("pushplus", {}).get("enabled"):
        try:
            alerts.pushplus(a["pushplus"]["token"], "均线粘合提醒测试", demo)
            results.append(("PushPlus", True))
            print("  PushPlus: OK")
        except Exception as e:  # noqa: BLE001
            results.append(("PushPlus", False))
            print("  PushPlus: 失败 ->", e)
    if a.get("dingtalk", {}).get("enabled"):
        try:
            alerts.dingtalk(a["dingtalk"]["webhook"], "均线粘合提醒测试", demo)
            results.append(("钉钉", True))
            print("  钉钉: OK")
        except Exception as e:  # noqa: BLE001
            results.append(("钉钉", False))
            print("  钉钉: 失败 ->", e)
    if a.get("wecom", {}).get("enabled"):
        try:
            alerts.wecom(a["wecom"]["webhook"], "均线粘合提醒测试", demo)
            results.append(("企业微信", True))
            print("  企业微信: OK")
        except Exception as e:  # noqa: BLE001
            results.append(("企业微信", False))
            print("  企业微信: 失败 ->", e)
    if a.get("email", {}).get("enabled"):
        try:
            alerts.send_email(a["email"], "均线粘合提醒测试", demo)
            results.append(("邮件", True))
            print("  邮件: OK")
        except Exception as e:  # noqa: BLE001
            results.append(("邮件", False))
            print("  邮件: 失败 ->", e)
    ok_count = sum(1 for _, ok in results if ok)
    print(f"\n结果：{ok_count}/{len(results) or 0} 个渠道成功" if results else "没有启用任何提醒渠道")
    return 0 if results and all(ok for _, ok in results) else 1


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    cfg = load_config()
    if mode == "config":
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
        return
    if mode == "testpush":
        sys.exit(test_push(cfg))
    if mode == "daily":
        run_daily(cfg)
        return
    if mode == "watch":
        watch_loop(cfg)
        return
    if mode == "report":
        run_once(cfg, do_notify=False)
        return
    run_once(cfg, do_notify=True)


if __name__ == "__main__":
    main()
