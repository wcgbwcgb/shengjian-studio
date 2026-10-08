# Mac 安装器和启动器共用：使用工作台自带的 Python / Node.js / FFmpeg（都在 .runtime 里），并沿用系统代理。
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="$ROOT/.runtime"
export PATH="$RUNTIME/node/bin:$RUNTIME/ffmpeg/bin:$RUNTIME/bin:$HOME/.local/bin:$PATH"
# 只用 uv 下载到工作台里的 Python；系统自带的 python3 会弹窗要求安装开发者工具。
export UV_PYTHON_INSTALL_DIR="$RUNTIME/python" UV_PYTHON_PREFERENCE=only-managed

# 很多代理软件只设置了「系统设置」里的代理，终端不会自动使用，这里替它带上。
if [ -z "${https_proxy:-}${HTTPS_PROXY:-}${all_proxy:-}${ALL_PROXY:-}" ]; then
    proxy_info="$(scutil --proxy 2>/dev/null)"
    if printf '%s\n' "$proxy_info" | grep -q 'HTTPSEnable : 1'; then
        proxy_host="$(printf '%s\n' "$proxy_info" | awk '$1 == "HTTPSProxy" {print $3}')"
        proxy_port="$(printf '%s\n' "$proxy_info" | awk '$1 == "HTTPSPort" {print $3}')"
        if [ -n "$proxy_host" ] && [ -n "$proxy_port" ]; then
            export https_proxy="http://$proxy_host:$proxy_port" http_proxy="http://$proxy_host:$proxy_port"
            export HTTPS_PROXY="$https_proxy" HTTP_PROXY="$http_proxy"
            export no_proxy=127.0.0.1,localhost NO_PROXY=127.0.0.1,localhost
            echo "使用系统代理：$https_proxy"
        fi
    fi
fi
