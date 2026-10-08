#!/bin/bash
# Mac 启动器：双击启动工作台并打开浏览器。关闭这个窗口即停止工作台。
cd "$(dirname "$0")" || exit 1
PORT=8765
URL="http://127.0.0.1:$PORT"

. scripts/mac-env.sh

if ! .venv/bin/python -c 'import uvicorn' 2>/dev/null; then
    echo '还没有安装好（或者文件夹被移动过）。请先双击「安装.command」。'
    read -r -p '按回车键关闭…'
    exit 1
fi

if curl -fs --max-time 2 "$URL/api/environment" | grep -q music-studio; then
    echo "工作台已经在运行：$URL"
    open "$URL"
    exit 0
fi

echo "声间工作台：$URL"
echo '使用期间请保持这个窗口打开；关闭窗口即停止工作台。'
# 服务就绪后再打开浏览器。
(for _ in $(seq 60); do
    curl -fs --max-time 1 "$URL/api/environment" >/dev/null && { open "$URL"; exit; }
    sleep 0.5
done) &
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
read -r -p '工作台已停止。按回车键关闭…'
