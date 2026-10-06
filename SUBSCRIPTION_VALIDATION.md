# Claude 订阅调研与文案

当前本机配置已切换为：调研 Sonnet、文案 Sonnet、视频 Opus。前两项是 CLI 模型别名，由已登录的 Claude Code 解析具体可用版本；此前调研和文案默认走 Sonnet 5.5 API，本机尚未配置 API 密钥。

## 实现

- 调研、角度、完整文案、局部重写共用本机 Claude Code 的 claude.ai 登录，不读取或转存订阅 OAuth 凭据。
- 每次调研/文案执行前检查登录方式。非 claude.ai 登录会明确失败，不自动回退到 API。
- 调研开放 WebSearch / WebFetch；文案不开放文件或命令工具。CLI 使用结构化输出，原有来源校验、锁定段落、版本和下游同步继续执行。
- 来源状态由真实工具事件判断：搜索、成功读取或未核实。模型提出的链接不会自动变成已读取。
- 任务冻结执行方式、模型、CLI 路径、超时和轮次上限。切换全局服务、模型或修改 CLI 设置不影响已提交任务和重试。已有 API 任务仍使用原 API 快照。
- CLI 的用量及费用估算保存在任务/版本中，不进入 API 预算。账号实际额度以 Claude 订阅为准。
- 设置新增「调研与文案服务」，可切换订阅/API，分别选择 Sonnet / Opus / Haiku。保存服务不保存其他未确认偏好。

实现入口：app/text_cli.py、app/jobs.py、app/models.py；原有取消、超时和进程树停止逻辑复用 app/claude_cli.py。

CLI 的非交互调用及结构化输出见 [Claude Code 官方说明](https://code.claude.com/docs/en/headless)，订阅登录见 [官方认证文档](https://code.claude.com/docs/en/authentication)。本实现调用官方 CLI，不将订阅凭据当作 API Key。

## 验证

结构化输出 schema 曾有两项接口兼容错误：先缺少顶层 `type`，随后仍使用了 Claude 工具接口禁止的顶层 `anyOf`。第一次只补齐类型的修复不完整。

当前改为普通 `type: object` + `properties`，不使用顶层 `oneOf` / `allOf` / `anyOf`。正常结果、补充信息或建议由应用代码验证，空结果不能保存为有效版本。结构与 [CLI 官方对象 schema 示例](https://code.claude.com/docs/en/headless) 一致。

实际 CLI 参数回归覆盖调研、文案和角度三种模式，测试子进程同时检查根类型和禁止的组合关键字；修改前复现顶层 `anyOf` 的 400，修改后通过。另增加建议、补充信息和空结果的回归，Python 用例数扩展至 77 项；此前 75 / 76 项是先前阶段的本地测试记录，不代表真实服务生成已验收。

Python 回归扩展至 75 项，新增 6 项订阅测试：

1. 实际测试 CLI 子进程完成研究、来源校验、文案与局部重写。
2. 没有 API Key 时创建项目仍自动调研。
3. 全局切换回 API 或改变 CLI 配置后，重试沿用原订阅模型和选项。
4. Console / API 登录被拒绝，无 API 回退。
5. 无效结构化结果和取消不产生错误版本。
6. 订阅模型、模板和设置独立保存及兼容检查。

浏览器订阅流程 6 组通过，覆盖无 API Key 调研、角度写稿、局部重写、设置独立保存、下一任务使用新模型及 390px 布局。原有创作、视频 CLI、连接管理和一致性浏览器回归继续通过。

- [订阅浏览器结果](artifacts/subscription-browser-results.json)
- [真实连接尝试](artifacts/subscription-live-results.json)

本机 Claude Code 2.1.290 已安装，检测到 claude.ai 登录。真实隔离调研请求返回 ECONNREFUSED；直接外网连接探测返回 Windows 权限错误 10013，说明当前执行环境的外网连接受限。真实订阅调研和写稿未完成验收，不以测试夹具结果替代。失败任务和诊断仅保存在 artifacts，没有写入正式项目。

正常启动或重启工作台以加载后端更新。手动真实验收命令（会使用订阅额度）：

```powershell
.\.venv\Scripts\python.exe scripts/check-subscription-live.py
```

测试输出在 artifacts 下，正式项目数据保持独立。字幕仍暂停。
