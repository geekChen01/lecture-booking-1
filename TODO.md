# 剩余事项

> 2026-10-17 晚：修复清单的 P0 / 调度重构 / P1 / P2 已全部完成并验证
> （编译 + 离线回归 + 真连实测 0.35s；详见 git 提交与 `cache/log/` 运行日志），
> 遗留 worktree 与空分支已清理。以下为仅存事项：

- [x] **真报名验收**（2026-10-18 12:00 已完成）：2/5 成功（天线罩），单发 POST 0.2~0.3s，
      调度与并发全部生效。败因归因见 `cache/log/run_20261018_120023.log`：
      晚 23s 启动 + 双峰拥堵下超时连接作废 + 重试预算 2 发太少（2131129 最早开火反而没抢到）。
- [ ] **提交当前改动**（.gitignore / auth / config / display / lecture / main / readme / get_token_v1
      + 新增 TODO.md、accounts.example.csv、launcher.ps1），可选开分支走 PR。
- [x] **预热模式 `--prewarm`**（2026-10-18 已实现并验证接线）：
      `python main.py --prewarm`（11:59:30 前启动）→ 扫描/分组后将"未到时间"最早一分钟讲座纳入目标 →
      预热 n+1 条连接（逐一验证存活，修复旧版吞异常假报完成）→ 每 10s 心跳保活（>300ms 或失败立即重建）→
      最终验活（开放前 0.5s）→ 开放+0.1s 精确开火，`skip_warmup` 直发。
      已验证：编译、真连预热 2/2 存活 82ms、心跳 22ms（复用生效）、全流程降级路径；待实战检验。
      顺带修复：A0230（token 过期）此前被扫描层静默吞成"未扫描到讲座"，现与 401/403 同样触发自动刷新；
      刷新仍失败则抛 `AuthExpiredError`（穿透页级重试），账号级红字报
      "token 全过期 | 请运行 launcher.ps1 重抓 Token"，不再装正常（2026-10-18 已用全过期 token 实测验证）。
- [x] **报名 POST 改 2s 超时 × 5 发**（SIGNUP_MAX_RETRIES=5，最坏 ≈11s；
      扫描类仍 2 次/(2,4)）。"只重试 1 次"已被 10-18 实战证伪。

## 登录自动化探索（2026-10-18 晚，实验结论）

- [x] **纯 HTTP 认证链路打通（当前微信账号）**：逆向 wxapkg（V1MMWX 解密：PBKDF2-SHA1(AppID,saltiest)
      + AES-CBC 固定 iv + XOR）→ 复刻请求签名（MD5(排序参数+token+随机key+ts+nonce) + SM2 key 头，
      wxCode 参数=字面量占位符）→ **personnelLogin 正确路径 `/chair/h5/api/personnelLogin.do`**：
      satoken → chair-token → chair API 全通（已验证 9 场已报名）。auth_cache_2131178 已用此链路重写。
      工具：`portal_login.py`（签名客户端）+ `cache/wxapkg_src/`（解包源码）。
- [x] GUI 自动化基建打通（PS 脚本 + 视觉识别 + 坐标点击，可复用 cache/shots/fill_login.ps1 模式）
- [ ] **其他账号密码登录：纯 HTTP 已证实不可行**（decryptLogin A00107 / appletBound B00101 均要求
      真实 wx.login auth_code 或微信用户密钥加密信道；原生层流量不走系统代理无法截获）。
      剩余路径：①恢复各账号微信授权态后走"拉起即抓"链（仍需切微信）；②半自动 GUI（自动填+人工验证码）；
      ③解包分析 wx.login 逆向（深度，工程量大）
- [ ] 恢复 2131178 微信授权态（"退出登录"实验破坏了绑定，现在 token 是门户会话级、chair 不认；
      在小程序里重新微信登录即可），然后重验"拉起小程序→秒抓 id_token→直接当 chair_token"闭环
