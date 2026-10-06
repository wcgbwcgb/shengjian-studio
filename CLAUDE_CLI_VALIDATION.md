> 历史实现记录。当前流程、素材归属、连接快照及暂停字幕的行为以 README.md 和 CONSISTENCY_VALIDATION.md 为准。

# 本地 Claude Code CLI 视频制作

实现与本地验证日期：2026-10-05。

## 已实现的用户流程

首页输入自然语言要求，点击「直接制作视频」，创建项目并启动 `cli_video` 后台任务。也可以从「从已有素材开始」上传视频、音频和图片，再到视频页输入制作要求。原有研究、文案、分镜与本地组装流程保留。

网页提供 Prompt、任务阶段与制作记录、取消与重试、视频预览、MP4/ZIP 下载、历史版本。继续修改时，引用当前视频版本，通过 `--resume <session_id> --fork-session` 生成新的 CLI 会话和独立输出；旧视频、原始素材与旧会话保留。

本机安装和登录检测只调用 `claude --version`、`claude auth status`，不启动生成请求。工作台保存版本、登录是否有效及认证方式，不保存账号邮箱或 CLI 凭证。CLI 配置与研究流程的 API 配置分别管理。

## 执行方式

Python 后端使用 `subprocess.Popen` 直接启动用户安装的原版 CLI。原生程序直接执行；npm 版的 `.cmd` 安装入口解析到 Node.js 与 `cli.js`。Prompt 经 UTF-8 stdin 传递，不拼接进 shell 命令。

调用参数包括：

```text
-p --output-format stream-json --verbose
--model opus --max-turns 50
--permission-mode dontAsk --restricted
--tools Read,Write,Edit,Glob,Grep,Bash
--settings <本次工具钩子配置>
--strict-mcp-config --mcp-config <空 MCP 配置>
```

模型、超时和轮数由网页设置；提交时冻结，重试沿用原配置。默认超时 30 分钟。使用 CLI 自身的登录状态，移除工作台进程继承的 API Key、API 代理及临时 OAuth 覆盖变量，保留 CLI 自己的配置目录。CLI 返回的用量和费用估算单独保存，不混入工作台 API 预算。

每个项目固定工作目录 `data/projects/<项目ID>/agent/`。每个任务使用 `tasks/<任务ID>/inputs`、`work`、`output`，复制输入素材及上版成片，写入冻结的 `project.json`、制作要求、工具绝对路径及独立媒体助手。媒体助手支持 ffprobe、抽帧、可选 faster-whisper 转写；Claude 可自行编写 Python 剪辑与图形脚本，调用 FFmpeg，不受现有时间线渲染器限制。

后端解析 JSON 事件，将文件读取、编写脚本、转写、渲染等动作转成网页阶段信息，不向网页展示模型内部思考。Windows 使用 Job Object 管理进程树；取消、超时及工作台退出会停止任务的子进程。大素材复制也会检查取消标记。重启后中断任务保留中间文件，可从已记录的 CLI 会话重试。

参数及行为参考 [Claude Code 官方 CLI 文档](https://code.claude.com/docs/en/cli-reference) 与 [程序化运行说明](https://code.claude.com/docs/en/headless)。`--restricted` 要求 2.1.248 或以上。钩子使用 `command` + `args` 直接启动 Python，避免 Windows 路径被 Bash 重新解析，参考 [官方 Hooks 文档](https://code.claude.com/docs/en/hooks)。本版 Windows 制作入口只启用 Bash，需要 Git for Windows。

## 交付与校验

Claude 必须生成 `output/delivery.json`，声明视频、摘要、可选字幕/封面、素材使用记录、审片图片及可选时间线。

- 仅接受本次输出目录内的实际 MP4；拒绝越界路径、缺失文件和无效清单。
- 通过真实 ffprobe 验证视频流、有限时长、H.264/yuv420p 和 AAC；通过 FFmpeg 完整解码。
- 要求审片图片实际存在、能够解码，并在 CLI 事件中有成功的 `Read` 工具结果。
- 校验素材引用、源时间与成片时间，按清单要求受保护音乐完整覆盖、保持增益且成片有音频。
- 可选直切时间线经过现有校验器检查，其时长必须与实际视频一致。复杂效果保留制作脚本，不展示无效的时间线编辑器。
- 打包 MP4、交付清单、可选 SRT/封面以及制作脚本。生成新的 edit 版本，并记录父版本、会话、模型和任务用量。

任务期间没有上游变化时，验证成功后自动采用并展示成片；项目变化时保留新结果及旧成果，标记新结果需要确认。预览与导出直接使用经过检查的 CLI 成片，不再交给本地时间线渲染器重做。

## 权限边界与当前限制

使用 restricted 模式及 PreToolUse 钩子限制文件工具：读取限定当前 agent 目录，写入限定本次 work/output；上下文、输入副本和钩子配置不允许通过文件工具改写。Bash 入口只接受配置的 Python、FFmpeg、ffprobe，拒绝命令串联、重定向、任意 shell 和 `python -c`。Python 必须先保存为本次 work 下的脚本。

**这不是操作系统沙箱。** 获准执行的 Python 脚本具有当前用户权限，可再调用其他系统功能；FFmpeg 的复杂滤镜也不构成隔离环境。因此目前适合本人本机的可信项目，不适合多租户服务。原件复制和工具限制减少误操作，但不提供对任意脚本的系统级隔离。

音乐校验依据 Claude 提供的使用清单，不能证明最终波形的实际增益或内容；审片图片读取不能证明构图和字幕一定正确。复杂自由剪辑的来源映射目前按原速片段记录。实际混音、字幕、画面和事实引用需要播放及人工检查。

本机渲染不等于全程离线：Prompt、读取的文本与图片会发送给 Claude 模型。CLI 账号决定模型权限、限额和计费。未接入自动配音、商业素材库或生成视频服务。未安装 faster-whisper 时不伪造转写；首次使用本地转写模型可能需要下载。

## 实际验证结果

| 验证 | 结果 | 实际依赖 |
| --- | --- | --- |
| Python 全量测试 | 57 项通过，无跳过 | SQLite/FastAPI、测试 CLI 子进程、真实 FFmpeg |
| 原有 V2 浏览器流程 | 12 组通过 | 隔离 FastAPI、固定 AI 响应、真实媒体渲染 |
| CLI 专用浏览器流程 | 6 组通过 | 测试 CLI 子进程、真实 H.264/AAC MP4、Chrome 实际播放 |
| 前端语法检查 | 通过 | Node.js |
| 真实 Claude 登录与生成 | 未验证 | 当前机器未检测到真实 CLI |

CLI 测试涵盖：无 API Key 创建视频、Prompt 的引号及 shell 文本原样传递、生成钩子的真实子进程执行、范围限制、读图要求、完整解码、ZIP 文件访问、连续修改与会话分叉、拒绝缺失/损坏/越界产物、保护音乐、无输出时取消、进程树超时、失败用量保存、冻结配置重试、跨项目版本拒绝、大素材复制取消。

浏览器测试涵盖：设置中检测 CLI、首页直接制作且不启动研究、实际播放超过 0.3 秒、连续修改保存父版本、发布包下载、高级版本页、重载保存 Prompt 与成片、390px 页面无横向溢出。两组浏览器均无未捕获 JavaScript 错误。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node scripts/check-ui.mjs
node scripts/check-cli-ui.mjs
```

浏览器夹具只写入 `artifacts/`，不修改正式项目或机器的 CLI 配置。结果与截图在 `artifacts/cli-browser-results.json`、`artifacts/cli-video.png`、`artifacts/cli-settings.png`。真实账号最后验收步骤：安装并登录 CLI，重启工作台，在设置中检测，上传实际素材，执行制作和一次修改，播放回听、核对字幕和保护音乐，再下载成片。
