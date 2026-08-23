# -*- coding: utf-8 -*-
"""生成筛选结果报告：CSV / JSON / HTML。"""
import csv
import html
import json
import os
import time


FIELDS = [
    ("code", "代码"), ("name", "名称"), ("score", "评分"),
    ("price", "现价"), ("pct_chg", "涨跌幅%"), ("breakout_date", "突破日"),
    ("days_ago", "距今天数"), ("breakout_gain", "突破涨幅%"),
    ("cluster_pct", "粘合度%"), ("converge_days", "粘合天数"),
    ("vol_ratio", "量比"), ("turnover", "换手率%"),
    ("amount_yi", "成交额(亿)"), ("total_mv_yi", "总市值(亿)"),
    ("ma5", "MA5"), ("ma10", "MA10"), ("ma20", "MA20"),
    ("ma20_slope", "MA20斜率%"), ("first_break", "首次突破"),
]


def quote_url(code):
    if code.startswith(("6", "9")):
        return f"https://quote.eastmoney.com/sh{code}.html"
    return f"https://quote.eastmoney.com/sz{code}.html"


def save_reports(out_dir, hits, meta):
    os.makedirs(out_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(out_dir, f"report_{ts}.csv")
    json_path = os.path.join(out_dir, f"report_{ts}.json")
    html_path = os.path.join(out_dir, "latest_report.html")
    latest_csv = os.path.join(out_dir, "latest.csv")
    latest_json = os.path.join(out_dir, "latest.json")

    # CSV
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([zh for _, zh in FIELDS])
        for h in hits:
            row = []
            for k, _ in FIELDS:
                v = h.get(k, "")
                if k == "first_break":
                    v = "是" if v else "否"
                row.append(v)
            w.writerow(row)

    # JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "hits": hits}, f, ensure_ascii=False, indent=2)

    # HTML
    rows = ""
    if hits:
        for h in hits:
            fb = "是" if h.get("first_break") else "否"
            rows += (
                "<tr>"
                f"<td>{h['name']}</td>"
                f"<td><a href='{quote_url(h['code'])}' target='_blank'>{h['code']}</a></td>"
                f"<td class='score'>{h['score']}</td>"
                f"<td>{h['price']}</td>"
                f"<td class='{'up' if h['pct_chg'] >= 0 else 'down'}'>{h['pct_chg']:+.2f}%</td>"
                f"<td>{h['breakout_date']}（{h['days_ago']}天前）</td>"
                f"<td class='up'>{h['breakout_gain']:+.2f}%</td>"
                f"<td>{h['cluster_pct']}%</td>"
                f"<td>{h['converge_days']}</td>"
                f"<td>{h['vol_ratio']}</td>"
                f"<td>{h['turnover']}%</td>"
                f"<td>{h.get('total_mv_yi', '')}</td>"
                f"<td>{fb}</td>"
                "</tr>"
            )
    else:
        rows = "<tr><td colspan='13' style='text-align:center;color:#888'>暂无符合条件的股票</td></tr>"

    title = meta.get("title", "均线粘合向上变盘")
    updated = meta.get("updated", "")
    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  body {{ font-family: "Microsoft YaHei", sans-serif; margin: 24px; background: #f7f8fa; color: #222; }}
  h1 {{ font-size: 22px; }}
  .meta {{ color: #888; font-size: 13px; margin-bottom: 16px; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  th, td {{ border: 1px solid #e8e8e8; padding: 8px 10px; font-size: 14px; text-align: center; }}
  th {{ background: #f0f2f5; }}
  tr:nth-child(even) td {{ background: #fafbfc; }}
  .score {{ font-weight: bold; color: #e64545; }}
  .up {{ color: #e64545; }} .down {{ color: #2e9e5b; }}
  a {{ color: #1677ff; text-decoration: none; }}
  .note {{ font-size: 12px; color: #999; margin-top: 12px; }}
</style></head><body>
<h1>📈 {html.escape(title)}</h1>
<div class="meta">{html.escape(updated)} ｜ 共 {len(hits)} 只</div>
<table><thead><tr>
<th>名称</th><th>代码</th><th>评分</th><th>现价</th><th>最新涨跌</th>
<th>突破日</th><th>突破涨幅</th><th>粘合度</th><th>粘合天数</th><th>量比</th><th>换手率</th><th>总市值(亿)</th><th>首次突破</th>
</tr></thead><tbody>{rows}</tbody></table>
<div class="note">点击代码可查看东方财富行情。评分 = 粘合度(30) + 量能(25) + 突破涨幅(25) + 均线多头(10) + MA20斜率(10) + 新鲜度(10) + 首次突破(5)。</div>
</body></html>"""
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_doc)

    for src, dst in ((csv_path, latest_csv), (json_path, latest_json)):
        try:
            with open(src, "rb") as sf, open(dst, "wb") as df:
                df.write(sf.read())
        except OSError:
            pass
    return {"csv": csv_path, "json": json_path, "html": html_path}
