# -*- coding: utf-8 -*-
"""提醒渠道：控制台 / Windows 通知 / Server酱(微信) / 钉钉 / 企业微信 / 邮件。

所有渠道都在 config.json 的 "alerts" 中配置，未启用或失败都不会影响主流程。
"""
import html
import json
import smtplib
import subprocess
import urllib.parse
import urllib.request
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr


def _post_form(url, payload, timeout=15):
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _post_raw(url, body, headers=None, timeout=15):
    req = urllib.request.Request(url, data=body, headers=headers or {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def build_text(hits, market=None):
    """把命中结果拼成可读文本（用于控制台/推送）。"""
    if market:
        head = [market.get("text", ""), "=" * 46]
    else:
        head = ["=" * 46]
    if not hits:
        return "\n".join(head + ["均线粘合向上变盘：暂无符合条件的股票。"])
    lines = head + [f"均线粘合向上变盘提醒（{len(hits)} 只，按评分排序）", "=" * 46]
    for i, h in enumerate(hits, 1):
        lines.append(
            f"{i}. {h['name']}({h['code']}) 评分{h['score']} 现价{h['price']} "
            f"{h['pct_chg']:+.2f}%"
        )
        lines.append(
            f"   突破日{h['breakout_date']}({h['days_ago']}天前) 涨幅{h['breakout_gain']:+.2f}% "
            f"粘合度{h['cluster_pct']}% 粘合{h['converge_days']}天 量比{h['vol_ratio']:.2f}"
        )
        if h.get("checklist"):
            chk = h["checklist"]
            passed = sum(1 for v in chk.values() if v[0] is True)
            total = sum(1 for v in chk.values() if v[0] is not None)
            fails = [k for k, v in chk.items() if v[0] is False]
            lines.append(f"   技术面 ✅{passed}/{total} 通过" + (("，❌ " + "、".join(fails)) if fails else "，全部通过"))
        if h.get("core_fail"):
            lines.append("   ⚠️ 核心条件未全满足：" + "、".join(h["core_fail"]))
    return "\n".join(lines)


# ----------------------------------------------------------------------
def windows_toast(title, message):
    """Windows 10/11 系统通知（免安装，通过 PowerShell 调用 WinRT）。"""
    t = html.escape(title).replace("&quot;", '"')
    m = html.escape(message).replace("&quot;", '"')
    script = f"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text>{t}</text><text>{m}</text></binding></visual></toast>')
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('A股均线粘合提醒').Show($toast)
"""
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       capture_output=True, timeout=30)
        return True
    except Exception:  # noqa: BLE001
        return False


def serverchan(send_key, title, desp):
    url = f"https://sctapi.ftqq.com/{send_key}.send"
    _post_form(url, {"title": title, "desp": desp})
    return True


def dingtalk(webhook, title, desp):
    body = ('{"msgtype":"markdown","markdown":{"title":"%s","text":"%s"}}'
            % (title.replace('"', '\\"'), desp.replace('"', '\\"')))
    _post_raw(webhook, body.encode("utf-8"))
    return True


def wecom(webhook, title, desp):
    body = ('{"msgtype":"text","text":{"content":"%s\\n%s"}}'
            % (title.replace('"', '\\"'), desp.replace('"', '\\"')))
    _post_raw(webhook, body.encode("utf-8"))
    return True


def pushplus(token, title, content):
    """PushPlus 微信推送（公众号推送，免费额度高）。"""
    payload = {
        "token": token,
        "title": title,
        "content": content,
        "template": "txt",
    }
    req = urllib.request.Request(
        "https://www.pushplus.plus/send",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read().decode("utf-8", "ignore"))
    if resp.get("code") != 200:
        raise RuntimeError(f"PushPlus 返回: {resp.get('msg')}")
    return True


def send_email(cfg, title, content):
    host = cfg["smtp_host"]
    port = int(cfg.get("smtp_port", 465))
    user = cfg["user"]
    pwd = cfg["password"]
    to = cfg["to"]
    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = formataddr(("A股均线粘合提醒", user))
    msg["To"] = to
    if port == 465:
        s = smtplib.SMTP_SSL(host, port, timeout=15)
    else:
        s = smtplib.SMTP(host, port, timeout=15)
        s.starttls()
    try:
        s.login(user, pwd)
        s.sendmail(user, [to], msg.as_string())
    finally:
        s.quit()
    return True


def notify(cfg, hits, market=None):
    """按配置依次触发所有提醒渠道，返回各渠道是否成功。"""
    text = build_text(hits, market=market)
    if hits:
        title = f"🎯 均线粘合向上变盘 {len(hits)} 只"
    else:
        title = "均线粘合向上变盘：今日无"
    results = {"console": True}
    print("\n" + text)

    a = cfg.get("alerts", {})
    if a.get("windows_toast", True) and hits:
        results["toast"] = windows_toast(title, text[:600])

    if a.get("serverchan", {}).get("enabled"):
        try:
            serverchan(a["serverchan"]["send_key"], title, text)
            results["serverchan"] = True
        except Exception as e:  # noqa: BLE001
            print("  [提醒] Server酱推送失败:", e)

    if a.get("pushplus", {}).get("enabled"):
        try:
            pushplus(a["pushplus"]["token"], title, text)
            results["pushplus"] = True
        except Exception as e:  # noqa: BLE001
            print("  [提醒] PushPlus推送失败:", e)

    if a.get("dingtalk", {}).get("enabled"):
        try:
            dingtalk(a["dingtalk"]["webhook"], title, text.replace("\n", "\n\n"))
            results["dingtalk"] = True
        except Exception as e:  # noqa: BLE001
            print("  [提醒] 钉钉推送失败:", e)

    if a.get("wecom", {}).get("enabled"):
        try:
            wecom(a["wecom"]["webhook"], title, text)
            results["wecom"] = True
        except Exception as e:  # noqa: BLE001
            print("  [提醒] 企业微信推送失败:", e)

    if a.get("email", {}).get("enabled"):
        try:
            send_email(a["email"], title, text)
            results["email"] = True
        except Exception as e:  # noqa: BLE001
            print("  [提醒] 邮件发送失败:", e)
    return results


def send_text(cfg, title, text):
    """把自定义文本推送到所有已启用渠道（不做信号格式化）。"""
    a = cfg.get("alerts", {})
    results = {"console": True}
    if a.get("serverchan", {}).get("enabled"):
        try:
            serverchan(a["serverchan"]["send_key"], title, text)
            results["serverchan"] = True
        except Exception as e:  # noqa: BLE001
            print("  [提醒] Server酱推送失败:", e)
            results["serverchan"] = False
    if a.get("pushplus", {}).get("enabled"):
        try:
            pushplus(a["pushplus"]["token"], title, text)
            results["pushplus"] = True
        except Exception as e:  # noqa: BLE001
            print("  [提醒] PushPlus推送失败:", e)
            results["pushplus"] = False
    if a.get("dingtalk", {}).get("enabled"):
        try:
            dingtalk(a["dingtalk"]["webhook"], title, text.replace("\n", "\n\n"))
            results["dingtalk"] = True
        except Exception as e:  # noqa: BLE001
            print("  [提醒] 钉钉推送失败:", e)
    if a.get("wecom", {}).get("enabled"):
        try:
            wecom(a["wecom"]["webhook"], title, text)
            results["wecom"] = True
        except Exception as e:  # noqa: BLE001
            print("  [提醒] 企业微信推送失败:", e)
    if a.get("email", {}).get("enabled"):
        try:
            send_email(a["email"], title, text)
            results["email"] = True
        except Exception as e:  # noqa: BLE001
            print("  [提醒] 邮件发送失败:", e)
    return results
