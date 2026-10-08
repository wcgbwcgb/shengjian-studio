# 优化音频生成速度

合成或处理音频（配乐、混响、音效、混音）时按下面的做法写，避免一步跑上几分钟。

## 卷积一律用 FFT

- 混响、滤波核、任何长度超过几百个采样的卷积，用 `scipy.signal.fftconvolve`（或分块的 `scipy.signal.oaconvolve`）。
- 不要用 `np.convolve` 或 `scipy.signal.convolve(..., method='direct')`。它是逐点相乘，44.1kHz 下 1.2 秒的混响作用在 5 秒音频上就要约 20 秒，FFT 只要 0.01 秒，结果相同。
- 立体声两个声道可以一起处理：`fftconvolve(x, ir[:, None], axes=0)`。

```python
from scipy.signal import fftconvolve
wet = fftconvolve(stereo, ir[:, None], axes=0)[: len(stereo)]
```

## 其他常见的慢点

- 不要在 Python 里逐个采样循环。用 numpy 整段向量化计算；音符、鼓点这类事件可以循环，但每个事件内部要向量化。
- 滤波用 `scipy.signal.sosfilt` 加上 `butter(..., output='sos')`，同一个滤波器只设计一次，重复使用。
- 同一个音色或采样（鼓、和弦）只合成一次，缓存后多次叠加，不要每拍重新生成。
- 合成时就用最终的采样率，避免中途反复重采样。
- 只是拼接、混音、调音量、淡入淡出时，直接用 ffmpeg（`amix`、`volume`、`afade`、`concat`），比读进 Python 再写出去快。
- 有疑问时先用几秒的片段计时，确认速度合理再生成完整长度。
