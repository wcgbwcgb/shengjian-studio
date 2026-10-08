#!/bin/bash
# Mac 一键安装：把 Python、Node.js、FFmpeg 下载到本文件夹的 .runtime 里，再安装 Claude Code 和工作台依赖。
# 不需要 Homebrew，也不需要开机密码。可以重复运行，已经装好的部分会跳过。
cd "$(dirname "$0")" || exit 1

step() { printf '\n\033[1;32m==> %s\033[0m\n' "$1"; }
fail() {
    printf '\n\033[1;31m安装没有完成：%s\033[0m\n' "$1"
    echo '可以直接再双击一次「安装.command」重试；还不行就把这个窗口截图发给帮你安装的同学。'
    read -r -p '按回车键关闭…'
    exit 1
}
# 显示下载进度；网络不稳定时自动重试。
download() { curl -fL --retry 3 --retry-delay 3 --connect-timeout 20 --progress-bar -o "$2" "$1"; }

echo '声间 · AI 视频创作空间 — Mac 安装'
echo '需要下载约 250 MB，大约 5–15 分钟，取决于网速。期间请不要关闭这个窗口。'

# 解压后的文件带有"来自互联网"标记，去掉后启动器才能直接双击。
xattr -dr com.apple.quarantine . 2>/dev/null
chmod +x 安装.command 启动工作台.command 2>/dev/null
. scripts/mac-env.sh
TMP="$(mktemp -d)" && mkdir -p "$RUNTIME/bin" "$RUNTIME/ffmpeg/bin" || fail '无法写入这个文件夹。'
trap 'rm -rf "$TMP"' EXIT

# Apple 芯片（M1/M2/…）和 Intel 芯片下载不同的版本。
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = 1 ]; then
    UV_ARCH=aarch64 NODE_ARCH=arm64 FFMPEG_BUILD=arm64/1789931890_9.0.2
else
    UV_ARCH=x86_64 NODE_ARCH=x64 FFMPEG_BUILD=amd64/1789931006_9.0.2
fi

step '1/6 检查网络'
for host in github.com nodejs.org pypi.org ffmpeg.martin-riedl.de claude.ai; do
    curl -sI --max-time 20 -o /dev/null "https://$host" \
        || fail "连不上 $host。请检查网络；如果在国内，需要先打开代理，再重新运行安装。"
done
echo '网络正常。'

step '2/6 Python'
if [ ! -x "$RUNTIME/bin/uv" ]; then
    download "https://github.com/astral-sh/uv/releases/latest/download/uv-$UV_ARCH-apple-darwin.tar.gz" "$TMP/uv.tar.gz" \
        && tar -xzf "$TMP/uv.tar.gz" -C "$TMP" && mv "$TMP/uv-$UV_ARCH-apple-darwin/uv" "$RUNTIME/bin/uv" \
        || fail 'Python 安装工具（uv）下载失败。'
fi
# 文件夹被移动过、或者旧的安装没完成时，环境会失效，重新创建。
if ! .venv/bin/python -c 'import sys' 2>/dev/null; then
    rm -rf .venv
    uv venv --seed --python 3.12 .venv || fail 'Python 下载失败。'
fi
uv pip install --python .venv/bin/python -r requirements.txt || fail '工作台依赖安装失败。'
[ -f .env ] || cp .env.example .env

step '3/6 Node.js'
if [ ! -x "$RUNTIME/node/bin/node" ]; then
    NODE_DIST=https://nodejs.org/dist/latest-v22.x
    NODE_FILE="$(curl -fsSL --retry 3 "$NODE_DIST/SHASUMS256.txt" | awk -v f="darwin-$NODE_ARCH.tar.gz" '$2 ~ f"$" {print $2}')"
    [ -n "$NODE_FILE" ] || fail '找不到 Node.js 的下载地址。'
    download "$NODE_DIST/$NODE_FILE" "$TMP/node.tar.gz" || fail 'Node.js 下载失败。'
    rm -rf "$RUNTIME/node" && mkdir -p "$RUNTIME/node" \
        && tar -xzf "$TMP/node.tar.gz" -C "$RUNTIME/node" --strip-components 1 || fail 'Node.js 解压失败。'
fi
node --version || fail 'Node.js 无法运行。'

step '4/6 FFmpeg（视频处理）'
# 固定版本的静态 FFmpeg，自带字幕支持，只依赖系统库。
for tool in ffmpeg ffprobe; do
    [ -x "$RUNTIME/ffmpeg/bin/$tool" ] && continue
    download "https://ffmpeg.martin-riedl.de/download/macos/$FFMPEG_BUILD/$tool.zip" "$TMP/$tool.zip" \
        && unzip -oq "$TMP/$tool.zip" -d "$RUNTIME/ffmpeg/bin" || fail "$tool 下载失败。"
done
ffmpeg -version >/dev/null || fail 'FFmpeg 无法运行。'
echo 'FFmpeg 已就绪。'

step '5/6 Claude Code'
if command -v claude >/dev/null; then
    echo '已安装，跳过。'
else
    curl -fsSL https://claude.ai/install.sh | bash || fail 'Claude Code 安装失败。'
    command -v claude >/dev/null || fail '找不到刚安装的 Claude Code。'
fi

step '6/6 登录 Claude'
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
rm -rf "$TMP"
exec ./启动工作台.command
