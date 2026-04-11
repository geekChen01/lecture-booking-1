# Chair_Register

---

## 4. 更新日志

### v2.0 - 2026-04-11 

**核心特性**：
- ✅ **模块化重构**：拆分 ChairManager 上帝类为 4 个独立模块（config/auth/display/lecture）
- ✅ **配置统一**：所有配置常量集中到 config.py，消除重复定义
- ✅ **删除 ADDON 模式**：简化 get_token_v1.py，仅保留独立脚本模式
- ✅ **分层输出**：账号输出独立缓冲，避免并发交叉
- ✅ **并发抢票**：5 线程并发报名，20 秒完成 10 个讲座

---


### v1.1 - 2026-04-09

**核心特性**：
- ✅ **mitmdump 自动化**：自动启动 mitmdump + 代理 + 捕获
- ✅ **系统代理管理**：自动开关 Windows 系统代理
- ✅ **资源自动清理**：上下文管理器确保代理和进程清理

**重要变更**：
1. 新增 `get_token_v1.py`（集成 mitmdump + 代理）
2. 信号处理（Ctrl+C 优雅退出）
3. 代理状态检测与恢复

---

### v1.0 - 2026-04-04 

**核心特性**：
- ✅ 基础讲座扫描
- ✅ 单个账号报名
- ✅ 简单 Token 缓存
- ✅ 彩色终端输出

**已知问题**：
- ❌ 串行速度慢
- ❌ 无多账号支持


---
## 1. 项目结构

```
Chair_register\
├── config.py              # 统一配置（路径 + 常量 + API 端点）
├── auth.py                # 认证模块（TokenManager + ProxyManager）
├── display.py             # 展示层（彩色输出 + 表格 + 输出缓冲）
├── lecture.py             # 讲座业务（扫描 + 过滤 + 报名）
├── main.py                # 入口：讲座管理（编排层）
├── get_token_v1.py        # 入口：Token 捕获（独立脚本模式）
├── auth/                  # Token 缓存目录
│   ├── auth_cache_*.json  # 按账号隔离的 Token 缓存
├── cache/                 # 数据缓存目录
│   ├── lecture_list_cache_*.json      # 讲座列表缓存
│   ├── user_chairs_cache_*.json       # 已报名讲座缓存
│   ├── multi_account_report.json      # 多账号报名汇总
│   └── run_*.log                      # 运行日志
└── log/                   # 系统日志目录
    └── mitmdump_*.log            # mitmdump 自动启动日志
```

### 核心模块说明

| 模块 | 职责 | 关键类/函数 |
|------|------|------------|
| `config.py` | 统一配置管理 | 所有常量（CACHE_EXPIRE_HOURS, PROXY_PORT 等） |
| `auth.py` | 认证 + 代理管理 | TokenManager, ProxyManager |
| `display.py` | 终端输出展示 | Colors, AccountOutputBuffer, print_lecture_table |
| `lecture.py` | 讲座业务逻辑 | LectureScanner, LectureFilter, LectureSignup |
| `main.py` | 讲座管理入口 | main(), _run_single_account, _run_concurrent_accounts |
| `get_token_v1.py` | Token 捕获入口 | MitmdumpManager, main() |

---

## 2. mitmproxy/mitmdump  下载

### 独立安装（推荐）

1. **下载安装包**
   - 访问官网：https://mitmproxy.org/downloads/
   - 选择 Windows 版本（mitmproxy-*-windows-x86_64.zip）
   - 解压到任意目录（如 `E:\Tools\mitmproxy-12.2.1-windows-x86_64\`）

2. **验证安装**
   ```bash
   mitmdump --version
   ```

3. **安装证书**
   - 首次运行：`mitmdump`
   - 浏览器访问：`http://mitm.it`
   - 下载并安装对应系统的证书
   - Windows：导入到"受信任的根证书颁发机构"

---

## 3. 使用方法

### 3.1-安装mitmproxy,会用到里面的mitmdump 

### 3.2-捕获 Token

```bash
python get_token_v1.py
```

**自动化流程**：
1. 启动 mitmdump（监听 8891 端口）
2. 自动开启系统代理
3. **等待微信小程序登录**
4. 自动捕获 Token 并写入 `auth/auth_cache_*.json`
5. 按 Ctrl+C 自动关闭代理和 mitmdump

**预期输出**：
```
2026-04-10 10:00:00 [INFO] 启动 mitmdump: ...
2026-04-10 10:00:03 [INFO] 系统代理已开启：127.0.0.1:8891
2026-04-10 10:00:05 [INFO] 新增 Token: 账号=2xxxxx | 文件=auth/auth_cache_2xxxxx.json
```

### 3.3-运行主脚本

```bash
python main.py
```

**单账号模式**：
- 修改 `config.py`：`MULTI_ACCOUNT_MODE = False`
- 运行后自动扫描讲座、过滤、报名

**多账号并发模式**：
- 修改 `config.py`：`MULTI_ACCOUNT_MODE = True`
- 自动并发执行 3 个账号的报名任务

**预期输出**：
```
讲座管理脚本 - 分层输出版
DRY_RUN=False  MULTI_ACCOUNT=True  DEBUG=True
日志文件：cache/run_20260310_120255.log
--------------------------------------------------
✅ 发现 3 个账号缓存:
   🔹 22xxxx: ✅ 有效 (ok) | Token: absdadsad1nrGBh4FG2hH...
   🔹 23xxxx: ✅ 有效 (ok) | Token: adsafasnrGBh4FG2hH...
   🔹 24xxxx: ✅ 有效 (ok) | Token: adsadaserGBh4FG2hH...

🚀 启动多账号并发模式 (最大并发:3)
...
```

### 高级配置

#### 常用配置（按需修改）

```python
# config.py - ## CHANGE HERE

TARGET_ACCOUNTS = ["2212345"]  # 指定账号；[] = 运行所有
FILTER_NANJING = True         # True = 屏蔽南京讲座
DRY_RUN = True                # True = 模拟运行（不实际报名）
```

| 配置项 | 说明 |
|--------|------|
| `TARGET_ACCOUNTS` | 指定运行账号，空列表 `[]` 运行所有 |
| `FILTER_NANJING` | 是否屏蔽南京校区讲座 |
| `DRY_RUN` | 模拟运行模式，仅输出不报名 |

#### 调试模式

```python
# config.py
DEBUG_MODE = True  # 输出详细调试日志
```

#### 自定义并发数

```python
# config.py
CONCURRENT_SIGNUP_THREADS = 5  # 并发抢票线程数
CONCURRENT_ACCOUNTS = 3        # 并发账号数
```

#### 缓存机制

| 缓存文件 | 配置项 | 默认值 | 用途 |
|---------|--------|--------|------|
| `auth/auth_cache_*.json` | `CACHE_EXPIRE_HOURS` | 12h | Token 缓存，超时需刷新 |
| `cache/lecture_list_cache_*.json` | `LIST_CACHE_TTL` | 3s | 讲座列表缓存，减少 API 请求 |
| `cache/user_chairs_cache_*.json` | 硬编码 | 5s | 已报名讲座缓存，用于验证报名结果 |

**缓存策略**：请求时检查时间戳，未过期直接返回缓存数据，过期则重新请求 API。

---

**文档版本**：v2.0  
**最后更新**：2026-04-10
