# 加快渲染与编码

渲染动画、逐帧生成画面或编码视频时按下面的做法写。文中的数字是在这台电脑上用以前生成过的脚本实测的，画面和原来一致。

## 通用

- 终版一次输出网页能直接播放的 MP4：H.264、`yuv420p`、AAC、`-movflags +faststart`。不要渲染完再整条重新编码一遍。
- 编码器先试 NVIDIA 显卡：`-c:v h264_nvenc -preset p5 -tune hq -rc vbr -cq 19 -b:v 0`。用 `ffmpeg -v error -f lavfi -i color=s=256x256:d=0.1 -c:v h264_nvenc -f null -` 试一下，成功才说明能用（ffmpeg 列出 nvenc 不代表有显卡）。不能用时用 `-c:v libx264 -preset veryfast -crf 18`。
- 长视频不要用 `-preset slow` 或 `medium`：同样的 crf 下画质看不出差别，编码时间要多好几倍。
- 全量渲染前，先渲 1–2 秒或几十帧计时，按总帧数换算总时间。预计超过几分钟时，先找出慢在哪里。
- 拼接、裁剪、缩放、叠图、淡入淡出这类操作直接用 ffmpeg 滤镜完成，不要读进 Python 逐帧处理。

## Remotion

- `npx remotion render` 和 `npx remotion still` 都加 `--gl=angle`。无头 Chrome 默认不用显卡，模糊、阴影、渐变都由 CPU 来画。70 秒 1080p 的终版从 550 秒降到 199 秒，半分辨率预览从 150 秒降到 87 秒。
- 终版再加 `--color-space=bt709`，输出就是标准的 `yuv420p`，可以直接交付，不用再转码。
- `--concurrency` 设 8 就够了，16 也不会更快。
- 改完检查时，先用 `npx remotion still ... --frame=N --gl=angle` 看关键帧。要看动起来的效果，用 `--scale=0.5 --frames=起-止` 只渲改过的段落，不要每改一次就整条重渲。

## Python 逐帧画面（PIL、numpy、cairo 等）

把"画第 i 帧"写成只依赖 i 的函数，随机数在模块顶层用固定种子预先生成。然后多进程并行画帧，主进程按顺序把像素写进 ffmpeg。实测 3124 帧 1080×1920：单进程 131 秒，12 个进程加 nvenc 34 秒（x264 veryfast 35 秒）。

```python
import os, subprocess
from multiprocessing import Pool

def frame_bytes(i):
    return frame(i).tobytes()          # frame(i) 返回 W×H 的 RGB 图像

if __name__ == '__main__':             # Windows 上多进程必须写在这里面
    ff = subprocess.Popen(['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                           '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-', '-i', 'mix.wav',
                           *VIDEO, '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k',
                           '-shortest', '-movflags', '+faststart', 'out.mp4'], stdin=subprocess.PIPE)
    with Pool(min(12, os.cpu_count())) as pool:
        for data in pool.imap(frame_bytes, range(N), chunksize=4):
            ff.stdin.write(data)
    ff.stdin.close()
    ff.wait()
```

- `VIDEO` 是上面选好的编码器参数。
- 每个子进程都会重新执行一遍模块顶层的代码，所以顶层只放几秒内能做完的准备工作。

## 网页 / HTML 动画逐帧截图

用本文件旁边的 `tools/html-frames.mjs`。页面里定义 `window.render(t)`，把画面设成第 t 秒的样子。脚本会开多个浏览器并行截帧，边截边交给 ffmpeg 编码（自动选 nvenc 或 x264），不会先存下几万张图片。实测 1800 帧 1080p：原来单浏览器先存 JPEG 再编码要 59 秒，用这个脚本 25 秒。

- 出成片：`node <本文件所在目录>/tools/html-frames.mjs page.html out.mp4 --dur 秒数 --fps 30 --w 1920 --h 1080 --audio mix.wav`
- 只渲其中一段：再加 `--from 起始秒`。
- 只看几帧：`node <本文件所在目录>/tools/html-frames.mjs page.html --stills 检查目录 --times 1.5,12,40`
- 第一次运行时会把 playwright-core 装进 toolbox，以后共用。

自己写截帧脚本时，要开多个浏览器实例（例如 5 个，每个 2 个页面）。一个浏览器里开再多页面，每秒也只能截 30 多帧。JPEG 截图送进 ffmpeg 编码时，加 `-vf scale=out_range=tv,format=yuv420p`。
