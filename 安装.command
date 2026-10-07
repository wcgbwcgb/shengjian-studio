#!/bin/bash
# Mac 一键安装：Homebrew、Python、FFmpeg、Node.js、Claude Code，以及工作台依赖。
# 可以重复运行，已经装好的部分会跳过。
cd "$(dirname "$0")" || exit 1

step() { printf '\n\033[1;32m==> %s\033[0m\n' "$1"; }
fail() {
    printf '\n\033[1;31m安装没有完成：%s\033[0m\n' "$1"
    echo '请把这个窗口截图发给帮你安装的同学。'
    read -r -p '按回车键关闭…'
    exit 1
}
find_brew() {
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        if [ -x "$b" ]; then eval "$("$b" shellenv)"; return 0; fi
    done
    return 1
}

echo '声间 · AI 视频创作空间 — Mac 安装'
echo '整个过程大约 10–30 分钟，取决于网速。期间请不要关闭这个窗口。'

# 解压后的文件带有"来自互联网"标记，去掉后启动器才能直接双击。
xattr -dr com.apple.quarantine . 2>/dev/null
chmod +x 安装.command 启动工作台.command 2>/dev/null

step '1/5 Homebrew（Mac 的软件安装工具）'
if find_brew; then
    echo '已安装，跳过。'
else
    echo '接下来需要输入这台 Mac 的开机密码。'
    echo '输入时屏幕上不会显示任何字符，这是正常的，输完按回车即可。'
    sudo -v || fail '密码不正确，或者当前账户不是管理员。'
    # Homebrew 安装（含 Xcode 命令行工具）可能超过 sudo 的 5 分钟有效期，保持授权直到本脚本结束。
    while true; do sudo -n true; sleep 50; kill -0 "$$" || exit; done 2>/dev/null &
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
        || fail 'Homebrew 安装失败。'
    find_brew || fail '找不到刚安装的 Homebrew。'
fi
# 让以后打开的终端也能找到 brew 安装的程序。
if ! grep -qs 'brew shellenv' "$HOME/.zprofile"; then
    echo "eval \"\$($(command -v brew) shellenv)\"" >> "$HOME/.zprofile"
fi

step '2/5 Python、FFmpeg、Node.js'
brew install python@3.12 ffmpeg node || fail 'Python / FFmpeg / Node.js 安装失败。'
PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
[ -x "$PYTHON" ] || fail '找不到 Python 3.12。'

# 封面和文字卡需要 FFmpeg 的 ass 字幕滤镜；精简版没有时改用完整版。
[ -f .env ] || cp .env.example .env
if ffmpeg -hide_banner -h filter=ass 2>&1 | grep -q 'Unknown filter'; then
    echo '当前 FFmpeg 缺少字幕支持，安装完整版…'
    if brew install ffmpeg-full; then
        FULL="$(brew --prefix ffmpeg-full)/bin"
        sed -i '' '/^FFMPEG_PATH=/d;/^FFPROBE_PATH=/d' .env
        printf 'FFMPEG_PATH=%s/ffmpeg\nFFPROBE_PATH=%s/ffprobe\n' "$FULL" "$FULL" >> .env
    else
        echo '完整版 FFmpeg 安装失败。工作台可以使用，但封面和文字卡可能无法生成。'
    fi
fi

step '3/5 工作台依赖'
[ -x .venv/bin/python ] || "$PYTHON" -m venv .venv || fail '无法创建 Python 环境。'
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements.txt || fail '工作台依赖安装失败。'

step '4/5 Claude Code'
export PATH="$HOME/.local/bin:$PATH"
if command -v claude >/dev/null; then
    echo '已安装，跳过。'
else
    curl -fsSL https://claude.ai/install.sh | bash || fail 'Claude Code 安装失败。'
    command -v claude >/dev/null || fail '找不到刚安装的 Claude Code。'
fi

step '5/5 登录 Claude'
read -r -p '现在登录 Claude 吗？第一次安装请输入 y，已经登录过输入 n，然后按回车：' answer
if [ "$answer" = y ] || [ "$answer" = Y ]; then
    echo
    echo '接下来会打开 Claude Code：'
    echo '  1. 按提示选择（一路按回车即可），选择登录方式时选 Claude 账号（订阅）。'
    echo '  2. 浏览器会打开登录页，登录并点"授权"。'
    echo '  3. 回到这个窗口，看到输入框后，输入 /exit 并按回车，安装会继续。'
    read -r -p '准备好了请按回车…'
    claude
fi

step '安装完成，正在打开工作台'
exec ./启动工作台.command
