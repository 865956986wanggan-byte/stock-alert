# -*- coding: utf-8 -*-
"""后台调度器：每个交易日 09:25 / 15:05 自动运行选股并推送到微信。

由 Windows 开机启动（Startup 文件夹）拉起，pythonw 无窗口静默运行。
周末/节假日自动跳过推送（daily 模式按数据日期去重，不会重复打扰）。
"""
import ctypes
import datetime
import json
import os
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_PY = os.path.join(BASE_DIR, "ma_alert", "main.py")
LOG_FILE = os.path.join(BASE_DIR, "outputs", "scheduler.log")
STATE_FILE = os.path.join(BASE_DIR, "outputs", "scheduler_state.json")
RUN_TIMES = ("09:25", "15:05")
CHECK_INTERVAL = 20  # 秒


def _single_instance():
    """利用 Windows 命名互斥体保证只有一个调度器实例。"""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, "Global\\A股均线粘合提醒调度器")
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


def log(msg):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def already_ran(date, slot):
    return _state().get("last") == f"{date}|{slot}"


def mark_ran(date, slot):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last": f"{date}|{slot}"}, f, ensure_ascii=False)
    except OSError:
        pass


def run_daily():
    log("触发每日推送 ...")
    try:
        subprocess.run([sys.executable, MAIN_PY, "daily"], timeout=900, cwd=BASE_DIR)
        log("每日推送完成")
    except Exception as e:  # noqa: BLE001
        log(f"每日推送出错: {e}")


def main():
    if not _single_instance():
        print("调度器已在运行，退出。")
        return
    log("后台调度器已启动（交易日 09:25 / 15:05 自动推送）")
    while True:
        try:
            now = datetime.datetime.now()
            if now.weekday() < 5:  # 周一 ~ 周五
                hm = now.strftime("%H:%M")
                if hm in RUN_TIMES and not already_ran(now.date().isoformat(), hm):
                    run_daily()
                    mark_ran(now.date().isoformat(), hm)
        except Exception as e:  # noqa: BLE001
            log(f"调度循环出错: {e}")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
