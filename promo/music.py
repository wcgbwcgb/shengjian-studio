"""Synthesize the promo soundtrack: 120 BPM, 70 s, warm pads + plucks + light drums.

usage: python music.py out.wav
Scene starts in promo.html fall on bar lines (one bar = 2 s).
"""
import sys
import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

SR = 44100
DUR = 70.0
BEAT = 0.5
N = int(SR * DUR)
rng = np.random.default_rng(7)

L = np.zeros(N)
R = np.zeros(N)


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def add(sig, start, pan=0.0, gain=1.0):
    i = int(start * SR)
    if i >= N:
        return
    sig = sig[: N - i] * gain
    L[i:i + len(sig)] += sig * np.cos((pan + 1) * np.pi / 4)
    R[i:i + len(sig)] += sig * np.sin((pan + 1) * np.pi / 4)


def lowpass(x, f):
    return sosfilt(butter(2, f, 'low', fs=SR, output='sos'), x)


def highpass(x, f):
    return sosfilt(butter(2, f, 'high', fs=SR, output='sos'), x)


# Fmaj7 – G6 – Em7 – Am7, each chord two bars (4 s)
CHORDS = [
    [53, 57, 60, 64],
    [55, 59, 62, 64],
    [52, 55, 59, 62],
    [57, 60, 64, 67],
]
ROOTS = [41, 43, 40, 45]


def chord_at(t):
    return int(t // 4) % 4


def pad(notes, dur):
    t = np.arange(int(dur * SR)) / SR
    out = np.zeros_like(t)
    for n in notes:
        for det in (-0.07, 0.0, 0.06):
            f = hz(n + det)
            ph = rng.uniform(0, 2 * np.pi)
            out += np.sin(2 * np.pi * f * t + ph) + 0.25 * np.sin(4 * np.pi * f * t + ph) + 0.08 * np.sin(6 * np.pi * f * t)
    env = np.minimum(1, t / 1.2) * np.minimum(1, (dur - t) / 1.0)
    return lowpass(out * env, 1800) / (len(notes) * 3)


def pluck(n, dur=1.2):
    t = np.arange(int(dur * SR)) / SR
    f = hz(n)
    s = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) + 0.12 * np.sin(6 * np.pi * f * t)
    return s * np.exp(-t * 4.5) * np.minimum(1, t / 0.004)


def bass(n, dur):
    t = np.arange(int(dur * SR)) / SR
    f = hz(n)
    s = np.sin(2 * np.pi * f * t) + 0.2 * np.sin(4 * np.pi * f * t)
    return s * np.minimum(1, t / 0.02) * np.exp(-t * 1.2)


def kick():
    t = np.arange(int(0.45 * SR)) / SR
    f = 48 + 90 * np.exp(-t * 28)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 8)


def hat(open_=False):
    t = np.arange(int((0.25 if open_ else 0.07) * SR)) / SR
    return highpass(rng.standard_normal(len(t)), 7000) * np.exp(-t * (14 if open_ else 60))


def snare():
    t = np.arange(int(0.3 * SR)) / SR
    noise = sosfilt(butter(2, [1500, 6000], 'band', fs=SR, output='sos'), rng.standard_normal(len(t)))
    return (noise * 0.8 + 0.4 * np.sin(2 * np.pi * 190 * t)) * np.exp(-t * 18)


def riser(dur):
    t = np.arange(int(dur * SR)) / SR
    x = rng.standard_normal(len(t))
    out = np.zeros_like(x)
    seg = SR // 20
    for i in range(0, len(x), seg):
        fc = 400 + 6000 * (i / len(x)) ** 2
        out[i:i + seg] = sosfilt(butter(2, [fc, fc * 1.6], 'band', fs=SR, output='sos'), x[i:i + seg])
    return out * (t / dur) ** 2


# Pads across the whole piece, last chord rings out
for k in range(17):
    s = k * 4.0
    if s >= 64:
        break
    add(pad(CHORDS[k % 4], 4.6), s, gain=0.55)
add(pad(CHORDS[0], 6.2), 64.0, gain=0.6)

# Arpeggio plucks on 8ths
ARP = [0, 2, 1, 3, 2, 1, 3, 2]
for i in range(int(66 / 0.25)):
    t = i * 0.25
    if t < 0.5:
        continue
    c = CHORDS[chord_at(t)]
    n = c[ARP[i % 8]] + 12
    g = 0.16 if t < 6 else 0.13
    add(pluck(n), t, pan=0.35 if i % 2 else -0.35, gain=g)

# Bass and drums from the brand reveal (6 s) until the outro (64 s)
for b in range(int(6 / BEAT), int(64 / BEAT)):
    t = b * BEAT
    beat = b % 4
    if beat == 0:
        add(bass(ROOTS[chord_at(t)], 1.9), t, gain=0.35)
    if beat in (0, 2) or (beat == 3 and b % 8 == 7):
        add(kick(), t + (0.25 if beat == 3 else 0), gain=0.55)
    if beat in (1, 3) and t >= 11:
        add(snare(), t, gain=0.12)
    if t >= 11:
        add(hat(), t + 0.25, pan=0.25, gain=0.09)
        if 50 <= t < 59:
            add(hat(), t, pan=-0.25, gain=0.05)
            add(hat(), t + 0.125, pan=0.2, gain=0.04)
            add(hat(), t + 0.375, pan=-0.2, gain=0.04)

# Risers into the brand reveal and the agent scene
add(riser(1.8), 4.2, gain=0.05)
add(riser(1.8), 48.2, gain=0.05)
add(kick(), 64.0, gain=0.6)

mix = np.stack([L, R], 1)
t = np.arange(N) / SR
mix *= np.minimum(1, t / 0.8)[:, None]
mix *= np.clip((DUR - t) / 3.0, 0, 1)[:, None]
mix = np.tanh(mix * 1.2)
mix /= np.abs(mix).max() / 0.89
wavfile.write(sys.argv[1], SR, (mix * 32767).astype(np.int16))
print('wrote', sys.argv[1])
