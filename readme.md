# lecture-booking — 讲座自动报名

讲座系统（微信小程序端）多账号自动报名工具：抓 Token → 配账号 → 一键报名，结果推送微信 + 导出报告。

## 使用方法（按场景选一种）

### 场景一：战斗日卡点抢票（讲座即将开放时，推荐）

```powershell
# 11:50 前后：抓 token（切微信账号，全部抓完 Ctrl+C 退出）
powershell -ExecutionPolicy Bypass -File launcher.ps1

# 11:55~11:59：启动卡点模式（开放前 10 分钟内启动）
powershell -ExecutionPolicy Bypass -File battle.ps1
```

battle.ps1 = token 新鲜度检查 + 预热卡点模式（`main.py --prewarm`）：
预热连接 → 每 10s 心跳保活（自动剔除慢/死连接）→ **开放时刻 + 0.1s 多账号齐射**，不用掐表。
距开放超过 10 分钟启动则自动按普通模式跑（防止空等）。

### 场景二：日常查看 / 捡漏

```bash
python main.py            # 扫描 + 抢当前可报名的 + 导出已报名报告
python main.py --prewarm  # 同上；若有 10 分钟内开放的场会自动转卡点模式
```

token 过期会红字提示「请运行 launcher.ps1 重抓 Token」。

### 场景三：一键连跑（抓 token → 立即普通模式报名）

```powershell
powershell -ExecutionPolicy Bypass -File launcher.ps1
```

抓完 token 按 Ctrl+C，自动接跑 `main.py`。适合临时抢非整点开放的讲座。

### 其他

```bash
python main.py --cancel 账号 chairId    # 取消指定报名（测试后清理用）
python get_token_v1.py                  # 单独抓 token（battle 场景已包含）
```

## 环境准备（仅一次）

```bash
pip install requests urllib3
```

安装 [mitmproxy](https://mitmproxy.org/downloads/)（Windows 版解压即可）：

1. 运行一次 `mitmdump`
2. 浏览器访问 `http://mitm.it`，安装证书到「受信任的根证书颁发机构」
3. 把 `mitmdump.exe` 路径填入 `config.py` 的 `MITMDUMP_PATH`

## 配置账号

编辑 `accounts.csv`（每行一个账号，可带密码列：`账号,密码` 或 `账号 密码`，密码可省略；`#` 注释。文件不存在或留空 = 运行 `auth/` 下全部账号）：

```
2513131,mypassword
2513132
```

> `accounts.csv` 已被 gitignore 不会入库；仓库里的 `accounts.example.csv` 是模板。
> 密码当前主流程不用（Token 走微信授权链路），仅存着备用。
> 首次跑建议把 `config.py` 的 `DRY_RUN = True` 模拟确认，无误后改 `False`。

## 架构

```
launcher.ps1      ① 一键：抓 Token → 报名
battle.ps1        ① 卡点抢票：token 检查 + 预热等待模式
get_token_v1.py   ② 捕获 Token（mitmdump 抓微信小程序登录，自动拉起小程序）
config.py         ③ 账号列表 + 并发参数 + 推送配置
main.py           ④ 主程序：扫描 → 过滤 → 分组调度报名 → 推送/导出（--prewarm 卡点模式）
```

支撑模块：`auth.py`（认证/代理）、`lecture.py`（扫描/过滤/分组调度）、`display.py`（输出）。

## 抢票逻辑（调度器）

- **冲突分组**：时间上冲突的讲座自动归入同一通道；跨通道**并行**抢，通道内**串行**——首选失败（含重试）才转下一备选
- **通道内优先级**：① 一号院优先（地址含「一号院/1号院」或房间号 `1xx-`，如 `103-402`）② 讲座时长短优先
- **自动屏蔽**：标题以「南京校区」「合肥校区」开头的讲座一律不抢（过滤统计行显示数量，不整表输出）；测试脚本时可在 config 设 `INCLUDE_BLOCKED_CAMPUS = True` 放开练手
- **报名重试**：单发 2s 超时 × 5 发（重发安全，服务端按"已报名/重复"去重）
- **Token 自愈**：遇 token 过期（A0230）先用 id_token 自动刷新，救不回则账号级红字报"请重抓"

## config.py 常用配置

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `accounts.csv` | — | 要运行的账号列表（根目录，每行一个；留空 = 全部；已 gitignore） |
| `DRY_RUN` | `False` | `True` = 模拟运行，不实际报名 |
| `MULTI_ACCOUNT_MODE` | `True` | 多账号并发；`False` = 单账号交互选择 |
| `INCLUDE_BLOCKED_CAMPUS` | `False` | `True` = 南京/合肥讲座不屏蔽（测试用） |
| `CONCURRENT_ACCOUNTS` | `10` | 并发账号数 |
| `CONCURRENT_SIGNUP_THREADS` | `15` | 单账号最大并发报名数 |
| `SERVERCHAN_ENABLED` | `True` | 报名完成后是否微信推送 |
| `SERVERCHAN_KEY` | `""` | Server酱 SendKey（[sct.ftqq.com](https://sct.ftqq.com) 获取；也可设环境变量 `SERVERCHAN_KEY`） |
| `MITMDUMP_PATH` | — | mitmdump.exe 路径 |
| `HEARTBEAT_INTERVAL` | `10` | 卡点模式心跳间隔（秒） |
| `PREWARM_MAX_WAIT` | `600` | 卡点等待上限：距开放超过它（秒）就按普通模式跑 |

## 输出在哪

| 位置 | 内容 |
|------|------|
| 终端 | 各账号分类表（含地点列）+ 通道抢票过程 + 汇总 |
| `results/{账号}.json` | 当前已报名讲座报告（每次运行后更新） |
| 微信（Server酱） | 报名结果推送（需配置 Key） |
| `cache/log/run_日期_时间.log` | 运行日志（按次保留，自动清理 7 天前） |

## 常见问题

- **Token 过期 / 报名报 401、403**：程序会先用缓存自动刷新，刷新也失败就重跑 `launcher.ps1` 重新捕获（红字会提示）。
- **抢票日 Token 一定要提前抓**（建议 11:55 前），别等整点后才抓；token 实测寿命仅数小时。
- **battle.ps1 提示 token 超过 4 小时**：先跑 `launcher.ps1` 重抓。
- **想先模拟跑一遍**：`DRY_RUN = True`，推送标题会带 `[模拟]` 标记。
- **出现 403/429**：服务端限流信号，把 `CONCURRENT_SIGNUP_THREADS` 调小。
- **抓不到 Token**：确认 mitmproxy 证书已安装、微信用的是 PC 端、`MITMDUMP_PATH` 路径正确。
