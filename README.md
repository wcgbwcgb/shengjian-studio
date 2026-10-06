# 声间 · AI 视频创作空间

工作台本身不实现任何创作功能。调研、写稿、剪辑、从零生成视频……每个功能都是**一段发给本机 Claude Code 的提示词**。想改功能，就改提示词；想加功能，就加一段提示词。

原生 HTML / JavaScript 前端，Python / FastAPI 后端，SQLite 保存作品、对话和版本。

## 开始使用

1. 安装 Python 3.11+，双击 `启动工作台.cmd`。首次启动安装依赖。
2. 安装 Claude Code（2.1.248 或以上），在终端运行 `claude auth login` 登录。
3. 配置 FFmpeg / ffprobe（可以在设置中填写路径）。建议安装 Node.js，Claude 可以用 Remotion 或 HTML 动画制作动态图形。
4. 打开 http://127.0.0.1:8765，在设置里点「保存并检测」。更新后重启服务。

开发者启动：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

## 提示词

提示词（`data/prompts/*.md`，在「提示词」页面编辑）是按钮背后的文字。点按钮只会把它填进输入框，你能看到并修改要发送的全部内容；发出去的就是框里的文字，工作台不附加任何规则、默认设置或说明文件。每个提示词有 `scope`，决定它出现在哪里：首页（`home`）、作品的对话页（`project`）、调研页（`research`）、文案页（`script`）或灵感页（`inspiration`）。

默认提示词随程序放在 `app/defaults/prompts/`，首次启动复制到 `data/prompts/`。没改过的默认提示词会随程序更新到新版本；改过的保持你的版本。删除的默认项不会再被补回；编辑后可以「恢复默认」。

## 作品 = 工作文件夹 + 对话

- 每个作品一个工作文件夹：`data/projects/<作品>/claude/`。Claude 在里面自由工作，可以在终端进入这个文件夹直接运行 `claude` 继续。
- 每次发送是一轮对话。默认接着之前的对话（`--resume` + `--fork-session`）；取消「接着之前的对话」则从头开始，文件仍然都在。
- 项目素材按原文件名复制到 `素材/`（只读）。想怎么用直接写在要求里。
- 对话里显示 Claude 的回复、这次写出的文件和查阅过的网页。点文件可以预览；`.md / .txt / .json` 等文本文件可以直接修改，Claude 下次读到的就是修改后的内容。Claude 写的 HTML 在隔离的沙箱里打开。
- 这次运行中最新写出的视频会保存为版本（`videos/<任务>/video.mp4`），不是网页可播放的 H.264 MP4 时自动转换一份。运行中途停止但留下了文件时，保留这些文件并注明。

### 对话 · 调研 · 文案

作品页有三个标签，下面始终是同一个输入框。调研和文案页只负责显示 Claude 写的文件，页面上的按钮也是提示词，只把文字填进输入框。

- **调研**：显示 `research.json`（选题、结论、发生了什么、为什么值得讲、内容空缺、带来源的事实、观点与观众反应、待核实、来源列表）和 `directions.json`（方向卡片）。格式写在「调研与方向」提示词里。点「用这个方向写脚本」会把「写脚本」提示词和这个方向填进输入框。对话记录里没有 Claude 读取过的来源链接标为「未核实」。
- **文案**：把 `脚本.md` 排版显示，可以整篇编辑保存；「更自然」「更有冲击力」「缩短」「按脚本做视频」等按钮是 `script` 位置的提示词。

### 界面读取的文件（约定写在提示词里，不写在代码里）

- `research.json`、`directions.json`、`脚本.md`：见上。文件缺失或格式不对时，对应部分不显示，不会报错。
- **灵感卡片**：灵感页的两个按钮分别使用 `inspire`（不联网）和 `inspire-web`（联网）提示词，在 `data/inspiration/claude/` 运行，Claude 把选题写进 `ideas.json`。你的「不感兴趣」、收藏、反馈和已出现的题目会在运行前写成 `偏好/` 下的文件。Claude 列出但这次运行中没读过的链接标为「未核实」。

## 制作权限

Claude Code 以 `dontAsk` 模式运行全部工具（含联网搜索、读取网页、子代理），由 PreToolUse hook（`app/cli_guard.py`）放行。hook 只拦截：

- 删除、移动或重定向写入工作文件夹、toolbox 和临时目录以外的文件；
- 修改工作文件夹里的 `素材/` 副本；
- 修改系统设置、按进程名结束进程、全局安装依赖、修改工作台自身的 Python 环境；
- 读取凭据和工作台私有数据（`.env`、`machine-config.json`、`workbench.sqlite3`、`~/.ssh` 等）；
- 对外发布（`git push`、`npm publish`、`gh`）。

依赖、浏览器和可复用模板保存在 `.runtime/agent-toolbox`，所有任务共用。hook 检查的是工具调用本身，不检查 Claude 写入脚本后再执行的代码，因此不是操作系统沙箱。默认上限为 300 轮、120 分钟，可在设置中调整。

## 旧版作品

旧的分阶段工作流（研究 → 角度 → 脚本 → 分镜 → 初剪）和 API 服务模式已移除。启动时，旧作品的研究（已选的选题）、方向、脚本和需求卡会写成工作文件夹里的 `research.json`、`directions.json`、`脚本.md` 和 `需求.md`，显示在调研和文案页，也可以在对话里接着用。已经存在的文件不会被覆盖。数据库里的原始记录保留不动。

## 保存与备份

- `data/workbench.sqlite3`：作品、对话（任务）、视频版本、素材元数据、灵感和发布记录。
- `data/prompts/`：你的提示词。
- `data/machine-config.json`：Claude Code 与本地工具配置。
- `data/projects/<作品ID>/`：工作文件夹、项目素材和视频版本。
- `data/library/`：公共素材库的文件。

停止服务后备份整个 `data` 目录。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node scripts/check-ui.mjs
```

测试使用假的 Claude Code 子进程（`tests/fake_claude.py`）和真实 FFmpeg。浏览器检查在 `artifacts/` 下启动隔离的后端和数据库，不写入正式 `data`。真实账号下的创作质量需要实际使用验收。
