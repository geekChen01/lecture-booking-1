# lecture-booking — 讲座自动报名

讲座系统（微信小程序端）多账号自动报名工具：抓 Token → 配账号 → 一键报名，结果推送微信 + 导出报告。

## 架构

```
get_token_v1.py   ① 捕获 Token（mitmdump 抓微信小程序登录）
config.py         ② 账号列表 + 并发参数 + 推送配置
main.py           ③ 主程序：扫描 → 过滤 → 并发报名 → 推送/导出
```

支撑模块：`auth.py`（认证/代理）、`lecture.py`（业务）、`display.py`（输出）。

## 快速开始

### 0. 环境准备（仅一次）

```bash
pip install requests urllib3
```

安装 [mitmproxy](https://mitmproxy.org/downloads/)（Windows 版解压即可）：

1. 运行一次 `mitmdump`
2. 浏览器访问 `http://mitm.it`，安装证书到「受信任的根证书颁发机构」
3. 把 `mitmdump.exe` 路径填入 `config.py` 的 `MITMDUMP_PATH`

### 1. 捕获 Token（每个账号一次）

```bash
python get_token_v1.py
```

- 自动启动 mitmdump（`127.0.0.1:8891`）并开启系统代理
- 打开 PC 微信 → 进入讲座小程序（触发登录）
- 看到 `成功捕获 Token: 账号=xxxxxxx` 即成功，`Ctrl+C` 退出（自动恢复代理）
- 多账号：在微信切换登录后重复触发即可，Token 存于 `auth/auth_cache_{账号}.json`

### 2. 配置账号

编辑 `config.py`：

```python
TARGET_ACCOUNTS = ["2513131", "2513132"]   # 要跑的账号；[] = auth/ 下全部
DRY_RUN = True                              # 首次建议 True 模拟跑，确认无误后改 False
```

### 3. 运行

```bash
python main.py
```

## 输出在哪

| 位置 | 内容 |
|------|------|
| 终端 | 各账号分类表（未到时间/已满/已报名/冲突…）+ 汇总 |
| `results/{账号}.json` | 当前已报名讲座报告（每次运行后更新） |
| 微信（Server酱） | 报名结果推送（需配置 Key，见下） |
| `cache/log/run.log` | 运行日志 |

## config.py 常用配置

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `TARGET_ACCOUNTS` | `[]` | 要运行的账号；空列表 = `auth/` 下全部 |
| `DRY_RUN` | `False` | `True` = 模拟运行，不实际报名 |
| `MULTI_ACCOUNT_MODE` | `True` | 多账号并发；`False` = 单账号交互选择 |
| `FILTER_NANJING` | `True` | 屏蔽南京校区讲座 |
| `CONCURRENT_ACCOUNTS` | `10` | 并发账号数 |
| `CONCURRENT_SIGNUP_THREADS` | `15` | 单账号并发报名线程数 |
| `SERVERCHAN_ENABLED` | `True` | 报名完成后是否微信推送 |
| `SERVERCHAN_KEY` | `""` | Server酱 SendKey（[sct.ftqq.com](https://sct.ftqq.com) 获取；也可设环境变量 `SERVERCHAN_KEY`） |
| `MITMDUMP_PATH` | — | mitmdump.exe 路径 |

## 常见问题

- **Token 过期 / 报名报 401、403**：重跑 `python get_token_v1.py` 重新捕获。程序会先用缓存 Token 自动刷新（`AUTO_REFRESH = True`），刷新失败才需要手动。
- **抓不到 Token**：确认 mitmproxy 证书已安装、微信用的是 PC 端、`MITMDUMP_PATH` 路径正确。
- **想先模拟跑一遍**：`DRY_RUN = True`，推送标题会带 `[模拟]` 标记。
