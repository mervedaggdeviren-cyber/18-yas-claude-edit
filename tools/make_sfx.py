#!/usr/bin/env python3
"""Synthesises the two subtle transition sounds (royalty-free, generated here).

  whoosh.wav : 0.45 s airy noise swell, band-pass sweeping upward, slight pan
  pop.wav    : 0.12 s soft rounded "pop" for the end card

Usage: make_sfx.py OUT_DIR
"""
import os
import sys
import wave

import numpy as np

SR = 48000


def bandpass_sweep(x, f0, f1, q=1.2):
    """Biquad band-pass whose centre frequency glides from f0 to f1."""
    y = np.zeros_like(x)
    x1 = x2 = y1 = y2 = 0.0
    n = len(x)
    for i in range(n):
        f = f0 * (f1 / f0) ** (i / n)
        w = 2 * np.pi * f / SR
        alpha = np.sin(w) / (2 * q)
        b0, b2 = alpha, -alpha
        a0, a1, a2 = 1 + alpha, -2 * np.cos(w), 1 - alpha
        out = (b0 * x[i] + b2 * x2 - a1 * y1 - a2 * y2) / a0
        x2, x1 = x1, x[i]
        y2, y1 = y1, out
        y[i] = out
    return y


def whoosh():
    n = int(0.45 * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(18)
    noise = rng.standard_normal(n)
    body = bandpass_sweep(noise, 900, 5200)
    peak = 0.30  # seconds; the swell peaks just before the card lands
    env = np.where(t < peak, (t / peak) ** 2, np.exp(-(t - peak) / 0.05))
    mono = body * env
    pan = np.clip(0.35 + 0.3 * t / t[-1], 0, 1)  # drifts slightly left -> right
    return np.stack([mono * np.cos(pan * np.pi / 2), mono * np.sin(pan * np.pi / 2)], 1)


def pop():
    n = int(0.12 * SR)
    t = np.arange(n) / SR
    f = 520 + 380 * np.exp(-t / 0.018)  # quick downward glide = rounded "pop"
    phase = 2 * np.pi * np.cumsum(f) / SR
    env = (1 - np.exp(-t / 0.002)) * np.exp(-t / 0.028)
    mono = np.sin(phase) * env
    return np.stack([mono, mono], 1)


def write(path, x, peak_db):
    x = x / np.max(np.abs(x)) * 10 ** (peak_db / 20)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    write(os.path.join(out, "whoosh.wav"), whoosh(), -3)
    write(os.path.join(out, "pop.wav"), pop(), -3)


if __name__ == "__main__":
    main()
