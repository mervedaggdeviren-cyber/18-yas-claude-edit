#!/usr/bin/env python3
"""Composes and synthesises an original, royalty-free background bed.

Soft and warm, ~84 BPM: detuned pad chords, a gentle FM electric piano
pattern, a round sine bass and a very quiet shaker, with a synthetic room
reverb. Progression Dm9 - Bbmaj9 - Fmaj7 - Am7 (vi - IV - I - iii in F), timed
so the last bar resolves on Fmaj7.

Usage: make_music.py OUT.wav DURATION_SECONDS
"""
import sys
import wave

import numpy as np

SR = 48000
BPM = 84
BEAT = 60 / BPM
BAR = 4 * BEAT

# (bass, pad voicing) as MIDI notes; smooth voice leading between chords
CHORDS = [
    (38, [53, 57, 60, 64]),   # Dm9  (F A C E over D)
    (34, [53, 57, 60, 62]),   # Bbmaj9 (F A C D over Bb)
    (41, [53, 57, 60, 64]),   # Fmaj7
    (45, [55, 57, 60, 64]),   # Am7  (G A C E)
]
# eighth-note positions of the electric piano in each bar, with velocities
KEYS = [(0, 0.85), (3, 0.55), (4, 0.7), (6, 0.5)]
KEY_ORDER = [3, 1, 2, 0]  # which pad voice (+1 octave) each hit plays

rng = np.random.default_rng(1805)


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def pad_voice(f, n):
    """Detuned, softened saw: few harmonics with steep roll-off."""
    t = np.arange(n) / SR
    out = np.zeros(n)
    for cents in (-7, 0, 6):
        fd = f * 2 ** (cents / 1200)
        ph = rng.uniform(0, 2 * np.pi)
        for k in range(1, 7):
            out += np.sin(2 * np.pi * fd * k * t + ph * k) * (np.exp(-(k - 1) / 1.6) / k)
    return out / 3


def ep_note(f, n, vel):
    """FM electric piano: ratio-1 body plus a quickly fading bell partial."""
    t = np.arange(n) / SR
    index = 1.6 * np.exp(-t / 0.22) + 0.25
    body = np.sin(2 * np.pi * f * t + index * np.sin(2 * np.pi * f * t))
    bell = 0.12 * np.sin(2 * np.pi * f * 14 * t) * np.exp(-t / 0.05)
    env = (1 - np.exp(-t / 0.004)) * np.exp(-t / 0.9)
    trem = 1 + 0.08 * np.sin(2 * np.pi * 4.5 * t)
    return (body + bell) * env * trem * vel


def one_pole_lp(x, fc):
    a = np.exp(-2 * np.pi * fc / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = (1 - a) * v + a * acc
        y[i] = acc
    return y


def reverb_ir(seconds=2.4):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    pre = int(0.02 * SR)
    ir = np.zeros((n + pre, 2))
    for ch in range(2):
        noise = rng.standard_normal(n) * np.exp(-t / (seconds / 6.9))
        ir[pre:, ch] = one_pole_lp(noise, 5000)
    return ir / np.sqrt((ir ** 2).sum(axis=0))


def convolve(x, ir):
    n = len(x) + len(ir) - 1
    size = 1 << (n - 1).bit_length()
    X = np.fft.rfft(x, size)
    out = np.stack([np.fft.irfft(X * np.fft.rfft(ir[:, c], size), size)[:len(x)] for c in range(2)], 1)
    return out


def main():
    out_path, dur = sys.argv[1], float(sys.argv[2])
    n_total = int((dur + 3) * SR)
    n_bars = int(np.ceil(dur / BAR))
    offset = (2 - (n_bars - 1)) % 4  # make the last bar land on Fmaj7 (index 2)
    pad = np.zeros(n_total)
    keys = np.zeros(n_total)
    bass = np.zeros(n_total)
    shaker = np.zeros(n_total)
    for b in range(n_bars):
        root, voicing = CHORDS[(b + offset) % 4]
        s = int(b * BAR * SR)
        last = b == n_bars - 1
        # pad: overlapping swells so chord changes crossfade
        n = int((BAR + (3.0 if last else 1.2)) * SR)
        n = min(n, n_total - s)
        t = np.arange(n) / SR
        env = (1 - np.exp(-t / 0.35)) * np.clip((BAR + (3.0 if last else 1.2) - t) / 1.2, 0, 1)
        for m in voicing:
            pad[s:s + n] += pad_voice(hz(m), n) * env * 0.16
        # bass: two soft half notes
        for half in (0, 1):
            if last and half:
                break
            hs = s + int(half * 2 * BEAT * SR)
            hn = min(int((2 * BEAT + (2.5 if last else 0.15)) * SR), n_total - hs)
            ht = np.arange(hn) / SR
            benv = (1 - np.exp(-ht / 0.02)) * np.exp(-ht / (3.0 if last else 1.4))
            bass[hs:hs + hn] += (np.sin(2 * np.pi * hz(root) * ht)
                                 + 0.25 * np.sin(4 * np.pi * hz(root) * ht)) * benv * 0.35
        # electric piano (from bar 1 on; the last bar is a single held chord)
        if b >= 1:
            hits = [(0, 0.8)] if last else KEYS
            for j, (pos, vel) in enumerate(hits):
                ks = s + int(pos * BEAT / 2 * SR)
                kn = min(int(2.2 * SR), n_total - ks)
                notes = [voicing[i] + 12 for i in (range(4) if last else [KEY_ORDER[j]])]
                for m in notes:
                    keys[ks:ks + kn] += ep_note(hz(m), kn, vel * (0.55 if last else 1)) * 0.22
        # shaker on eighths from bar 2, off in the last bar
        if 2 <= b < n_bars - 1:
            for e in range(8):
                ss = s + int(e * BEAT / 2 * SR) + int(rng.normal(0, 0.004) * SR)
                sn = int(0.06 * SR)
                st = np.arange(sn) / SR
                burst = rng.standard_normal(sn) * np.exp(-st / 0.018) * (0.5 if e % 2 == 0 else 0.8)
                shaker[ss:ss + sn] += burst * 0.035
    shaker = np.diff(np.concatenate([[0], shaker]))  # crude high-pass: keep it airy
    pad = one_pole_lp(pad, 2200)

    dry = np.zeros((n_total, 2))
    dry[:, 0] = pad * 0.95 + keys * 0.8 + bass + shaker * 0.7
    dry[:, 1] = pad * 0.95 + keys * 1.0 + bass + shaker * 1.0
    wet = convolve(pad * 0.6 + keys, reverb_ir())
    mix = dry + wet * 0.35
    mix = mix[:int(dur * SR)]
    # fade in under the hook, fade out with the last frame
    fi, fo = int(1.2 * SR), int(2.5 * SR)
    mix[:fi] *= np.linspace(0, 1, fi)[:, None]
    mix[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.5
    mix /= np.max(np.abs(mix)) / 10 ** (-1 / 20)
    with wave.open(out_path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((mix * 32767).astype("<i2").tobytes())


if __name__ == "__main__":
    main()
