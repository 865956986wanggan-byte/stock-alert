# A 股均线粘合向上变盘 筛选提醒工具

当 A 股个股**均线（MA5/10/20/30/60）粘合**、随后**放量向上变盘**（股价刚上穿均线束、刚开始大涨）时，
自动筛选出来并提醒你。数据来自东方财富免费公开接口，无需注册、无需 token。

## 一、快速开始

1. 安装 Python 3.9+（已装可跳过），并安装依赖：
   ```
   pip install -r requirements.txt
   ```
2. 双击 **`run_screener.bat`**（或命令行执行 `python ma_alert\main.py once`）。
3. 等待约 10~60 秒，控制台会打印命中的股票，同时：
   - 生成 HTML 报告：`outputs\latest_report.html`（双击用浏览器打开，点代码可看行情）
   - 生成 CSV/JSON：`outputs\latest.csv` / `outputs\latest.json`
   - 弹出 **Windows 通知**（无需配置）

> 提示：数据来自最近一个交易日。非交易时段运行会使用上一个交易日收盘数据；
> 交易时段（9:30–15:00）运行会使用**当日盘中实时数据**。

## 二、盘中实时监控（重点）

命令行执行：
```
python ma_alert\main.py watch
```
默认每 5 分钟扫描一次全市场，只对**新出现的信号**提醒（同一信号不重复打扰），
已提醒的信号记录在 `outputs\seen.json`。适合挂机跑一整天。

## 三、微信提醒（重点）

推荐 **Server酱** 或 **PushPlus**，两者都是把消息直接推送到你微信里的免费服务，任选其一即可。

### 方式A：Server酱（推荐，服务号推送）
1. 电脑浏览器打开 https://sct.ftqq.com/ ，用 **GitHub** 账号登录；
2. 登录后页面会显示你的 **SendKey**（形如 `SCTxxxx`），复制它；
3. 用**手机微信**扫描页面上的二维码，关注「方糖」服务号（首次需要绑定）；
4. 编辑 `config.json`：
   ```json
   "alerts": { "serverchan": { "enabled": true, "send_key": "你的SendKey" } }
   ```
5. 测试：命令行运行 `python ma_alert\main.py testpush`，微信收到"测试"消息即成功。

### 方式B：PushPlus（公众号推送，免费额度更高）
1. 打开 https://www.pushplus.plus/ ，**微信扫码**登录；
2. 登录后首页"一对一推送"里复制你的 **token**；
3. 编辑 `config.json`：
   ```json
   "alerts": { "pushplus": { "enabled": true, "token": "你的token" } }
   ```
4. 测试：`python ma_alert\main.py testpush`，微信收到即成功。

> 说明：两个渠道可以同时开启（互为备份）。Server酱免费版每天有推送条数限制，盘中监控建议用 PushPlus
> 或调大 `watch_interval_min`。钉钉/企业微信机器人、邮件同理，填好 `config.json` 对应项即可。
> `testpush` 会向所有已启用渠道各发一条测试消息，方便一次性验证；也可以直接双击 **`test_push.bat`**。

## 四、每天自动发到微信（Windows 计划任务）

以**管理员身份**双击 **`install_task.bat`**，会自动创建两个计划任务（均已内置 Python 路径探测）：
- `A股均线粘合提醒-收盘后`：每天 **15:05** 用当天收盘数据筛选并**自动推送到微信**
- `A股均线粘合提醒-盘前`：每天 **09:25** 用昨日数据预筛（若收盘后任务已推过则自动跳过，不重复）

自动推送规则（`daily` 模式）：
- **每个交易日只推送一次**：有信号推信号列表，无信号按配置推「今日无信号」；
- **周末/节假日自动不打扰**：数据日期没变就不会再推；
- 同一任务被多次触发也不会重复推送。

> 前提：电脑在设定时间处于开机状态（锁屏没关系）。关机时任务会错过，可设置 BIOS/系统开机启动后补跑。
> 计划任务默认"仅在用户登录时运行"；如需关机也运行需在任务计划程序中设置"不管用户是否登录都要运行"并填入密码。

盘中想更及时，可另建任务每 5~10 分钟运行 `python ma_alert\main.py watch`（只推新信号），或直接开着窗口跑 watch。

### 如果不想用计划任务：开机自启调度器（无需管理员）

本工具自带一个**后台调度器** `auto_scheduler.py`（已写入 Windows 开机启动项，开机自动运行、无窗口）：
- 每个交易日 **09:25** 和 **15:05** 自动运行 daily 推送，周末自动跳过；
- 单实例保护，不会重复启动；运行日志见 `outputs\scheduler.log`；
- 手动停止：任务管理器结束 `pythonw.exe` 的 `auto_scheduler.py` 进程，或删除开机启动文件夹里的 `A股均线粘合提醒-自动调度.vbs`。


## 五、参数调优（config.json）

| 参数 | 默认 | 含义 |
| --- | --- | --- |
| `cluster_pct` | 4.0 | 均线粘合度阈值（%）：最大MA与最小MA的差/最小MA |
| `converge_min_days` | 3 | 粘合持续最少天数 |
| `converge_max_days` | 60 | 粘合持续最多天数（超过=很久的老横盘，默认排除） |
| `base_range_pct` | 18 | 粘合期内股价最大波动幅度（%），排除"慢牛爬升"假粘合 |
| `breakout_lookback` | 3 | 突破必须发生在最近 N 个交易日内（"刚刚"变盘） |
| `volume_ratio` | 1.5 | 突破日量能 ≥ 前 5 日均量的倍数 |
| `min_breakout_gain` | 1.0 | 突破日最小涨幅（%） |
| `min_score` | 60 | 入选最低评分（0-100） |
| `min_market_cap_yi` | 30 | 总市值下限（亿） |
| `exclude_st` | true | 排除 ST/退市股 |
| `cache_ttl_min` | 60 | K线本地缓存有效期（分钟），避免重复请求被限流 |
| `watch_cache_ttl_min` | 10 | 监控模式缓存有效期（分钟） |
| `request_delay` | 0.08 | 每次行情请求后的间隔秒数（越小越快，越大越不容易被风控） |

**调松**：`cluster_pct` 调大到 5~6、`min_score` 调低到 50，候选更多；
**调严**：`cluster_pct` 调小到 3、`min_score` 调到 75，候选更精。

## 六、评分说明（满分 100）

粘合度 30 + 量能 25 + 突破涨幅 25 + 均线多头 10 + MA20 斜率 10 + 新鲜度 10 + 首次突破 5（封顶 100）。

## 七、数据与网络说明

- 数据来源：东方财富（全市场行情列表）＋ 腾讯行情（日K线，自动切换 ifzq / web.ifzq 主机）＋ 新浪行情（兜底）。
  全部为免费公开接口，无需 token；某个源不可用时自动切换，不影响使用。
- 日K线已做**本地缓存**（`outputs\kline_cache`）：第一次运行约 1 分钟，之后 1 小时内再次运行只需几秒，
  也避免高频请求触发数据源风控。
- 建议每 5~10 分钟运行一次 `watch` 监控即可，不要把频率设得太高。

## 八、其他说明

- **风险提示**：本工具仅为技术形态筛选，不构成投资建议；请结合基本面、消息面自行判断。
- 如需在通达信/同花顺内直接选股，可用附带的 `ma_breakout.tdx` 选股公式（条件选股→新建→粘贴）。
- 项目结构：
  ```
  stock-alert/
  ├─ config.json            # 参数与提醒渠道
  ├─ run_screener.bat       # 双击运行
  ├─ install_task.bat       # 一键安装计划任务
  ├─ ma_breakout.tdx        # 通达信选股公式（可选）
  ├─ outputs/               # 生成的报告
  └─ ma_alert/
     ├─ main.py             # 入口
     ├─ data_source.py      # 数据获取（东方财富）
     ├─ strategy.py         # 均线粘合向上变盘策略
     ├─ alerts.py           # 提醒渠道
     └─ report.py           # 报告生成
  ```
