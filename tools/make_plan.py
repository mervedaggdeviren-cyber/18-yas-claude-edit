#!/usr/bin/env python3
"""Builds the edit plan (plan.json) consumed by render.py.

Edit decisions live here; the word-level transcript lives in
edit/transcript_tr.json (Whisper output, proof-read by hand).

Usage: make_plan.py SOURCE.mp4 OUT_plan.json [SFX_DIR [MUSIC.wav]]
  With SFX_DIR (output of make_sfx.py) the plan also places a soft whoosh on
  every tip card and a soft pop on the end card. With MUSIC.wav (output of
  make_music.py) it adds a quiet background bed under the voice.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from render import crop_for_zoom, text_width  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_END = 80.03

# Sentence-level pauses (source seconds, measured from the waveform) that get
# tightened to `keep` seconds. Shorter gaps inside sentences are left alone so
# the speech keeps its natural rhythm.
PAUSES = [
    (6.44, 6.68, 0.16), (13.65, 13.97, 0.12), (19.32, 19.76, 0.14),
    (22.48, 22.84, 0.14), (23.10, 23.56, 0.12), (35.94, 36.48, 0.12),
    (40.51, 40.79, 0.14), (41.74, 42.06, 0.14), (48.10, 48.50, 0.14),
    (51.22, 51.92, 0.16), (55.56, 56.02, 0.16), (59.26, 59.58, 0.14),
    (64.67, 65.14, 0.14), (67.90, 68.36, 0.14), (71.52, 72.02, 0.18),
    (76.17, 76.56, 0.16), (77.26, 77.56, 0.14),
]

# Shot changes (source seconds): the framing alternates between wide (1.00)
# and a gentle punch-in (1.08) at sentence starts, so jump cuts read as
# deliberate. Points without a removed pause are picture-only cuts.
SHOTS = [3.36, 6.56, 13.22, 19.54, 22.66, 25.86, 30.72, 35.56, 40.65,
         51.57, 55.79, 61.68, 64.90, 68.13, 71.77, 76.36]
ZOOMS = (1.00, 1.08)

TIPS = [
    (1, 3.36, "Dövme için acele etme"),
    (2, 13.22, "Tasarruf ve yatırım alışkanlığı"),
    (3, 22.66, "Ev tipi epilasyon cihazı"),
    (4, 35.56, "Kendine yurt dışı deneyimi"),
    (5, 55.79, "Kadın sağlığı ve aşılar"),
]
OUTRO_AT = 71.77          # "Peki sen ..." - progress indicator ends here
END_CARD_AT = 76.36       # "Yorumlara yaz ..."
HOOK_END = 3.36           # her own opening sentence = the hook

HIGHLIGHTS = ["30 yaş", "yatırım", "alışkanlık", "epilasyon", "yurt dışı",
              "kadın sağlığı", "uzmanlara"]

SUB_SIZE = 60
SUB_MAX_W = 790
SUB_Y = 1410
CARD_Y = 300

# Text must never cover the face. edit/face_track.json holds a face box per
# 0.25 s of source (OpenCV Haar cascade); forehead and chin are estimated from
# it. Top cards end at TOP_LIMIT, subtitles start at SUB_LIMIT (output px).
TOP_LIMIT = CARD_Y + (2 * 58 * 1.18 + 56) / 2 + 16
SUB_LIMIT = SUB_Y - SUB_SIZE * 1.42 / 2 - 16

# Subtitle lines, broken by hand at natural phrase boundaries. Every word must
# match edit/transcript_tr.json in order; timings come from the transcript.
CUE_LINES = """
Dövme yaptırmak için
30 yaşıma kadar beklerdim.
Çünkü bazı şeyleri
gerçekten isteyip istemediğini
anlamak için kendine
biraz zaman vermen gerekiyor.
Her ay, az da olsa
bir yatırım yapardım.
Miktardan çok
alışkanlık önemli, unutmayın.
Finansal özgürlüğün temeli
genç yaşta atılır.
Ev tipi bir
epilasyon cihazı alırdım.
Hijyeninden emin olmadığınız
güzellik salonlarına
tonlarca para ödemek yerine
kendi evinizin konforunda
ev tipi bir epilasyon cihazı
kullanabilirsiniz.
Kendime 18. yaş hediyesi olarak
yurt dışı deneyimi alırdım.
Bütün paranızı eşyalara,
kıyafetlere harcamak yerine
farklı ülkeler keşfetmeye,
yeni kültürler tanımaya
ve bakış açınızı değiştirmeye
yatırım yapabilirsiniz.
Çünkü bazı deneyimler
insanın hayatını
gerçekten değiştiriyor.
Kadın sağlığı için
önemli aşıları araştırır,
gerektiğinde
uzmanlara danışırdım.
Sağlığınızla ilgili konularda
bilinçlenmeyi ertelemeyin.
Çünkü kendinizi korumak
utanılacak bir şey değil.
Kendinize değer vermenin
en önemli yollarından biridir.
Peki sen 18 yaşındaki haline
bir tavsiye verecek olsan
ne söylerdin?
Yorumlara yaz.
Belki birinin hayatına dokunuruz.
"""


def snap_shot(t):
    """A shot change inside a tightened pause happens at that pause's cut."""
    for a, b, k in PAUSES:
        if a <= t <= b:
            return round(a + k / 2, 3)
    return t


def face_clearance(samples, zoom, y0):
    """Smallest distance (px) between the face and either text zone."""
    _, ch, _, _ = crop_for_zoom(zoom, 576, 1024, 0.5, 0.5)
    k = 1920 / ch
    worst = 1e9
    for x, y, w, h in samples:
        forehead = (y - 0.08 * h - y0) * k
        chin = (y + 1.10 * h - y0) * k
        worst = min(worst, forehead - TOP_LIMIT, SUB_LIMIT - chin)
    return worst


def frame_shots(segs, track):
    """Pick each punch-in's vertical crop (or a smaller zoom) so the face sits
    between the cards and the subtitles."""
    for seg in segs:
        samples = [o["face"] for o in track if o["face"] and seg["s"] <= o["t"] < seg["e"]]
        if seg["zoom"] == 1.0 or not samples:
            continue
        for z in (seg["zoom"], 1.05, 1.0):
            _, ch, _, _ = crop_for_zoom(z, 576, 1024, 0.5, 0.5)
            y0 = max(range(0, 1024 - ch + 1), key=lambda v: face_clearance(samples, z, v))
            if face_clearance(samples, z, y0) >= 0 or z == 1.0:
                seg["zoom"], seg["y0"] = z, y0
                break


def segments():
    shots = [snap_shot(t) for t in SHOTS]
    cuts = sorted(set([round(a + k / 2, 3) for a, b, k in PAUSES] + shots))
    gaps = {round(a + k / 2, 3): round(b - k / 2, 3) for a, b, k in PAUSES}
    bounds, t = [], 0.0
    for c in cuts:
        bounds.append((t, c))
        t = gaps.get(c, c)
    bounds.append((t, SRC_END))
    segs, zi = [], 0
    shot_set = set(shots)
    for i, (s, e) in enumerate(bounds):
        if i > 0 and bounds[i - 1][1] in shot_set:
            zi ^= 1
        # snap to frame boundaries (30 fps) so picture and sound stay in sync
        segs.append({"s": round(round(s * 30) / 30, 4), "e": round(round(e * 30) / 30, 4),
                     "zoom": ZOOMS[zi]})
    return segs


def cues(words):
    hidden = {"İki.", "Üç.", "Dört."}  # the tip card already shows the number
    visible = [w for w in words if w["s"] >= HOOK_END - 0.05 and w["w"] not in hidden]
    out, i = [], 0
    for line in CUE_LINES.strip().split("\n"):
        n = len(line.split())
        ws = visible[i:i + n]
        got = " ".join(w["w"] for w in ws)
        assert got == line, f"cue mismatch: {line!r} vs {got!r}"
        assert text_width(line, "Bold", SUB_SIZE) <= SUB_MAX_W, line
        out.append({"s": ws[0]["s"], "e": ws[-1]["e"], "text": line})
        i += n
    assert i == len(visible), "transcript words left without a cue"
    # display timing: hold each cue until the next one (max +0.4 s)
    for a, b in zip(out, out[1:]):
        a["e"] = min(b["s"], a["e"] + 0.4)
    out[-1]["e"] = SRC_END
    return out


def main():
    source, out = sys.argv[1], sys.argv[2]
    words = json.load(open(os.path.join(ROOT, "edit", "transcript_tr.json"), encoding="utf-8"))
    track = json.load(open(os.path.join(ROOT, "edit", "face_track.json")))
    segs = segments()
    frame_shots(segs, track)
    plan = {
        "source": source,
        "src_w": 576, "src_h": 1024,
        # punch-in shots crop from the lower part of the frame so the face sits
        # higher, leaving the chest area free for subtitles
        "focus": [0.45, 0.54],
        "grade": ("colorbalance=rs=-0.03:bs=0.02:rm=-0.035:gm=-0.005:bm=0.025:rh=-0.02:bh=0.015,"
                  "eq=contrast=1.05:brightness=0.01:saturation=0.95:gamma=1.03,"
                  "curves=all='0/0.02 0.5/0.51 0.85/0.84 1/0.97'"),
        "segments": segs,
        # cards sit above the hairline (y < ~400); her forehead starts lower
        "hook": {"end": HOOK_END, "y": CARD_Y, "lines": [
            {"text": "18 yaşında olsaydım", "size": 58},
            {"text": "kendime vereceğim *5 tavsiye*", "size": 58}]},
        "tip_card_y": CARD_Y,
        "n_tips": 5,
        "tips": [{"n": n, "at": t, "title": title} for n, t, title in TIPS],
        "outro_at": OUTRO_AT,
        "end_card": {"at": END_CARD_AT, "y": CARD_Y, "lines": [
            {"text": "18 yaşındaki kendine", "size": 56},
            {"text": "ne tavsiye verirdin?", "size": 56, "accent": True}]},
        "highlights": HIGHLIGHTS,
        "sub_size": SUB_SIZE,
        # below her chin in every frame (lowest chin measured at y~1310)
        "sub_y": SUB_Y,
        "cues": cues(words),
    }
    if len(sys.argv) > 3:
        fx_dir = sys.argv[3]
        # the whoosh swell peaks 0.30 s in, so start it just before the card lands
        plan["sfx"] = [{"file": os.path.join(fx_dir, "whoosh.wav"), "at": t,
                        "offset": -0.30, "gain_db": -15} for _, t, _ in TIPS]
        plan["sfx"].append({"file": os.path.join(fx_dir, "pop.wav"), "at": END_CARD_AT,
                            "offset": 0.0, "gain_db": -14})
    if len(sys.argv) > 4:
        # voice sits at -14 LUFS; with sidechain ducking the bed ends up
        # ~17 dB under the voice while she talks
        plan["music"] = {"file": sys.argv[4], "lufs": -23}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    for seg in segs:
        samples = [o["face"] for o in track if o["face"] and seg["s"] <= o["t"] < seg["e"]]
        y0 = seg.get("y0", 0)
        c = face_clearance(samples, seg["zoom"], y0) if samples else float("nan")
        print(f"{seg['s']:6.2f}-{seg['e']:6.2f} zoom {seg['zoom']:.2f} y0 {y0:3d}  clearance {c:5.0f}px")


if __name__ == "__main__":
    main()
