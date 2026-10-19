#!/usr/bin/env python3
"""Instagram Reels render pipeline for the "18 yaşında olsaydım" video.

Reads an edit plan (JSON) and produces a 1080x1920 H.264/AAC MP4:
  * keeps only the listed source segments (jump cuts, optional punch-in zoom)
  * colour correction (white balance, contrast, gentle highlight roll-off)
  * speech clean-up (high-pass, light FFT denoise, EQ, compression, 2-pass loudnorm)
  * ASS overlay: hook title, "n/5" tip cards + progress indicator, word-synced
    subtitles with keyword highlights, end-screen question

Usage: render.py plan.json out.mp4 [--preview]
"""
import json
import os
import re
import subprocess
import sys
import tempfile

from PIL import ImageFont

W, H = 1080, 1920
FPS = 30
FONT_DIR = "/usr/share/fonts/opentype/inter"
# libass sizes a font by its full line height; Pillow by its em. Measured: 0.81.
ASS_TO_PIL = 0.81

# Palette (ASS colours are &HAABBGGRR, alpha 00 = opaque)
CREAM = "&H00EFF5FA&"      # #FAF5EF
INK = "&H001A1F2A&"        # #2A1F1A
ACCENT = "&H004A65B5&"     # #B5654A terracotta
GOLD = "&H009DD2FF&"       # #FFD29D warm highlight on dark pills
WHITE = "&H00FFFFFF&"
DARK = "&H00141414&"


def ass_time(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


_fonts = {}


def text_width(text, weight, size):
    key = (weight, size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(f"{FONT_DIR}/Inter-{weight}.otf", round(size * ASS_TO_PIL))
    return _fonts[key].getlength(text)


def pill(cx, cy, w, h, r=None):
    """ASS vector drawing of a rounded rectangle centred on (cx, cy)."""
    r = min(h / 2, r if r is not None else h / 2)
    x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    k = 0.5523 * r
    f = lambda v: f"{v:.1f}"
    p = [
        f"m {f(x0 + r)} {f(y0)}",
        f"l {f(x1 - r)} {f(y0)}",
        f"b {f(x1 - r + k)} {f(y0)} {f(x1)} {f(y0 + r - k)} {f(x1)} {f(y0 + r)}",
        f"l {f(x1)} {f(y1 - r)}",
        f"b {f(x1)} {f(y1 - r + k)} {f(x1 - r + k)} {f(y1)} {f(x1 - r)} {f(y1)}",
        f"l {f(x0 + r)} {f(y1)}",
        f"b {f(x0 + r - k)} {f(y1)} {f(x0)} {f(y1 - r + k)} {f(x0)} {f(y1 - r)}",
        f"l {f(x0)} {f(y0 + r)}",
        f"b {f(x0)} {f(y0 + r - k)} {f(x0 + r - k)} {f(y0)} {f(x0 + r)} {f(y0)}",
    ]
    return " ".join(p)


class Timeline:
    """Maps source timestamps onto the edited (concatenated) timeline."""

    def __init__(self, segments):
        self.segs = []
        t = 0.0
        for s in segments:
            self.segs.append((s["s"], s["e"], t))
            t += s["e"] - s["s"]
        self.duration = t

    def map(self, src, end=False):
        for s, e, o in self.segs:
            if s <= src <= e:
                return o + (src - s)
        # src falls in a removed gap: a start snaps forward to the next kept
        # segment, an end snaps back to the previous one
        prev = 0.0
        for s, e, o in self.segs:
            if src < s:
                return prev if end else o
            prev = o + (e - s)
        return self.duration


# ---------------------------------------------------------------- ASS builder

def build_ass(plan, tl):
    ev = []

    def add(layer, start, end, text, style="Base"):
        if end - start < 0.02:
            return
        ev.append(f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}")

    dur = tl.duration
    hook = plan.get("hook")
    end_card = plan.get("end_card")
    tips = plan.get("tips", [])
    hl_words = [w.lower() for w in plan.get("highlights", [])]

    # ---- Hook title (visible on the very first frame: also works as cover)
    hook_end = 0.0
    if hook:
        hook_end = tl.map(hook["end"]) if "end" in hook else hook["dur"]
        lines = hook["lines"]  # list of (text, size, colour-key)
        heights = [l["size"] * 1.18 for l in lines]
        box_h = sum(heights) + 70
        box_w = max(text_width(l["text"], "ExtraBold", l["size"]) for l in lines) + 110
        cy = hook.get("y", 420)
        fade = r"\fad(0,250)"
        add(5, 0, hook_end, rf"{{\an7\pos(0,0)\p1\bord0\shad0\1c{CREAM}\1a&H0C&{fade}}}{pill(W/2, cy, box_w, box_h, 34)}")
        y = cy - box_h / 2 + 35
        for l, lh in zip(lines, heights):
            col = ACCENT if l.get("accent") else INK
            add(6, 0, hook_end,
                rf"{{\an8\pos({W/2},{y:.0f})\fnInter\b1\fs{l['size']}\1c{col}\bord0\shad0{fade}}}{l['text']}",
                "Hook")
            y += lh

    # ---- Tip cards + persistent progress indicator
    n_tips = plan.get("n_tips", 5)
    tip_times = [(tl.map(t["at"]), t) for t in tips]
    for i, (t0, tip) in enumerate(tip_times):
        t_next = tip_times[i + 1][0] if i + 1 < len(tip_times) else (
            tl.map(plan["outro_at"]) if "outro_at" in plan else dur)
        card_end = min(t0 + tip.get("card_dur", 2.6), t_next)
        # card
        cy = plan.get("tip_card_y", 380)
        label = f"TAVSİYE {tip['n']}/{n_tips}"
        title = tip["title"]
        tw = max(text_width(title, "Bold", 54), text_width(label, "SemiBold", 32) * 1.25) + 96
        bh = 150
        anim = r"\fad(180,220)"
        add(5, t0, card_end, rf"{{\an7\pos(0,0)\p1\bord0\shad0\1c{CREAM}\1a&H0C&{anim}}}{pill(W/2, cy, tw, bh, 30)}")
        add(6, t0, card_end, rf"{{\an8\pos({W/2},{cy - bh/2 + 24:.0f})\fnInter\b1\fs32\fsp4\1c{ACCENT}\bord0\shad0{anim}}}{label}", "Card")
        add(6, t0, card_end, rf"{{\an8\pos({W/2},{cy - bh/2 + 66:.0f})\fnInter\b1\fs54\1c{INK}\bord0\shad0{anim}}}{title}", "Card")
        # progress indicator for the rest of this tip
        if card_end < t_next:
            add_progress(add, card_end, t_next, tip["n"], n_tips)

    # ---- End card
    if end_card:
        t0 = tl.map(end_card["at"])
        lines = end_card["lines"]
        sizes = [l["size"] for l in lines]
        box_w = max(text_width(l["text"], "Bold", l["size"]) for l in lines) + 110
        box_h = sum(s * 1.2 for s in sizes) + 70
        cy = end_card.get("y", 420)
        anim = r"\fad(300,0)"
        add(5, t0, dur + 1, rf"{{\an7\pos(0,0)\p1\bord0\shad0\1c{CREAM}\1a&H0C&{anim}}}{pill(W/2, cy, box_w, box_h, 34)}")
        y = cy - box_h / 2 + 35
        for l in lines:
            col = ACCENT if l.get("accent") else INK
            weight = r"\b1" if not l.get("light") else r"\b0"
            add(6, t0, dur + 1, rf"{{\an8\pos({W/2},{y:.0f})\fnInter{weight}\fs{l['size']}\1c{col}\bord0\shad0{anim}}}{l['text']}", "Card")
            y += l["size"] * 1.2

    # ---- Subtitles
    sub_y = plan.get("sub_y", 1330)
    fs = plan.get("sub_size", 60)
    for cue in plan.get("cues", []):
        t0, t1 = tl.map(cue["s"]), tl.map(cue["e"], end=True)
        if cue.get("hide"):
            continue
        text = cue["text"]
        tw = text_width(text, "Bold", fs)
        bh = fs * 1.42
        add(1, t0, t1, rf"{{\an7\pos(0,0)\p1\bord0\shad0\1c{DARK}\1a&H5A&}}{pill(W/2, sub_y, tw + 52, bh, 22)}")
        add(2, t0, t1, rf"{{\an5\pos({W/2},{sub_y + 2})\fnInter\b1\fs{fs}\1c{WHITE}\bord0\shad0}}{highlight(text, hl_words)}", "Sub")

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Base,Inter,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Sub,Inter,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1
Style: Hook,Inter,80,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,8,0,0,0,1
Style: Card,Inter,54,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,8,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    return header + "\n".join(ev) + "\n"


def add_progress(add, t0, t1, n, total):
    """Small dark pill at the top: 'n/5' plus five short segments."""
    cy = 262
    seg_w, gap, seg_h = 46, 10, 7
    bars_w = total * seg_w + (total - 1) * gap
    label = f"{n}/{total}"
    lw = text_width(label, "Bold", 34)
    pw = lw + 26 + bars_w + 56
    x = W / 2 - pw / 2 + 28
    add(3, t0, t1, rf"{{\an7\pos(0,0)\p1\bord0\shad0\1c{DARK}\1a&H70&\fad(200,0)}}{pill(W/2, cy, pw, 58)}")
    add(4, t0, t1, rf"{{\an4\pos({x:.0f},{cy + 1})\fnInter\b1\fs34\1c{WHITE}\bord0\shad0\fad(200,0)}}{label}", "Card")
    bx = x + lw + 26
    for i in range(total):
        col, alpha = (CREAM, "&H00&") if i < n else (WHITE, "&HA0&")
        cx = bx + i * (seg_w + gap) + seg_w / 2
        add(4, t0, t1, rf"{{\an7\pos(0,0)\p1\bord0\shad0\1c{col}\1a{alpha}\fad(200,0)}}{pill(cx, cy, seg_w, seg_h)}")


def tr_lower(s):
    """Turkish lower-casing that keeps string length (str.lower turns İ into 2 chars)."""
    return s.replace("İ", "i").replace("I", "ı").lower()


def highlight(text, words):
    """Colour configured keywords/phrases in GOLD."""
    # A keyword is a word stem: "30 yaş" also covers "30 yaşında", so extend
    # each match to the end of the word and colour whole words only.
    spans = []
    low = tr_lower(text)
    for w in sorted(words, key=len, reverse=True):
        for m in re.finditer(r"(?<!\w)" + re.escape(tr_lower(w)) + r"\w*", low):
            if not any(a < m.end() and m.start() < b for a, b in spans):
                spans.append((m.start(), m.end()))
    out, pos = "", 0
    for a, b in sorted(spans):
        out += text[pos:a] + f"{{\\1c{GOLD}}}" + text[a:b] + f"{{\\1c{WHITE}}}"
        pos = b
    return out + text[pos:]


# ---------------------------------------------------------------- ffmpeg graph

def crop_for_zoom(z, src_w, src_h, cx, cy):
    cw = int(round(src_w / z / 2)) * 2
    ch = int(round(src_h / z / 2)) * 2
    x = min(max(0, cx * src_w - cw / 2), src_w - cw)
    y = min(max(0, cy * src_h - ch / 2), src_h - ch)
    return cw, ch, int(x), int(y)


def build_graph(plan, ass_path, af_chain, audio_only=False):
    src_w, src_h = plan["src_w"], plan["src_h"]
    fcx, fcy = plan.get("focus", [0.45, 0.45])
    parts = []
    labels = []
    edge = 0.012  # short audio fades where a pause was removed, to avoid clicks
    segs = plan["segments"]
    for i, s in enumerate(segs):
        z = s.get("zoom", 1.0)
        cw, ch, x, y = crop_for_zoom(z, src_w, src_h, fcx, fcy)
        d = s["e"] - s["s"]
        if not audio_only:
            parts.append(
                # bounds sit on frame times; the small offset keeps float
                # rounding from adding or dropping a frame
                f"[0:v]trim=start={max(0, s['s'] - 0.005):.4f}:end={s['e'] - 0.005:.4f},setpts=PTS-STARTPTS,"
                f"crop={cw}:{ch}:{x}:{y},scale={W}:{H}:flags=lanczos,setsar=1[v{i}]")
        fades = ""
        if i > 0 and s["s"] > segs[i - 1]["e"] + 1e-3:
            fades += f",afade=t=in:d={edge}"
        if i + 1 < len(segs) and segs[i + 1]["s"] > s["e"] + 1e-3:
            fades += f",afade=t=out:st={max(0, d - edge):.3f}:d={edge}"
        parts.append(
            f"[0:a]atrim=start={s['s']:.4f}:end={s['e']:.4f},asetpts=PTS-STARTPTS{fades}[a{i}]")
        labels.append(f"[a{i}]" if audio_only else f"[v{i}][a{i}]")
    n = len(segs)
    if audio_only:
        parts.append(f"{''.join(labels)}concat=n={n}:v=0:a=1,{af_chain}[aout]")
        return ";\n".join(parts)
    parts.append(f"{''.join(labels)}concat=n={n}:v=1:a=1[vc][ac]")

    grade = plan["grade"]
    vf = (
        "[vc]scale=in_color_matrix=bt709:out_color_matrix=bt709,format=gbrp,"
        f"{grade},"
        "unsharp=5:5:0.35:5:5:0,"
        f"ass='{ass_path}',"
        "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p")
    tail = plan.get("tail_hold", 0)
    if tail:
        vf += f",tpad=stop_mode=clone:stop_duration={tail}"
    parts.append(vf + "[vout]")
    a = f"[ac]{af_chain}"
    if tail:
        a += f",apad=pad_dur={tail}"
    parts.append(a + "[aout]")
    return ";\n".join(parts)


AF_BASE = ("highpass=f=75,afftdn=nr=8:nf=-50:tn=1,"
           "equalizer=f=250:t=q:w=1.2:g=-1.5,equalizer=f=3500:t=q:w=1.5:g=1.5,"
           "acompressor=threshold=-24dB:ratio=2.2:attack=8:release=160:makeup=1")


def loudnorm_chain(plan):
    """Two-pass loudnorm (Instagram ~ -14 LUFS, -1.5 dBTP)."""
    g = build_graph(plan, None, AF_BASE + ",loudnorm=I=-14:TP=-1.5:LRA=9:print_format=json",
                    audio_only=True)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", plan["source"], "-filter_complex", g,
                        "-map", "[aout]", "-f", "null", "-"], capture_output=True, text=True)
    m = json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
    return (AF_BASE + f",loudnorm=I=-14:TP=-1.5:LRA=9:measured_I={m['input_i']}:"
            f"measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
            f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,"
            "aresample=48000")


def main():
    plan_path, out = sys.argv[1], sys.argv[2]
    preview = "--preview" in sys.argv
    plan = json.load(open(plan_path, encoding="utf-8"))
    tl = Timeline(plan["segments"])
    work = tempfile.mkdtemp(prefix="reels_")
    ass_path = os.path.join(work, "overlay.ass")
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(build_ass(plan, tl))
    if plan.get("ass_copy"):
        with open(plan["ass_copy"], "w", encoding="utf-8") as f:
            f.write(build_ass(plan, tl))
    af = loudnorm_chain(plan)
    graph = build_graph(plan, ass_path, af)
    enc = (["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"] if preview else
           ["-c:v", "libx264", "-preset", "slow", "-crf", "18", "-profile:v", "high",
            "-level:v", "4.2", "-g", "60", "-bf", "2"])
    cmd = (["ffmpeg", "-hide_banner", "-y", "-i", plan["source"], "-filter_complex", graph,
            "-map", "[vout]", "-map", "[aout]", "-r", str(FPS)] + enc +
           ["-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-color_range", "tv",
            "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", out])
    print(f"edited duration: {tl.duration + plan.get('tail_hold', 0):.2f}s, segments: {len(plan['segments'])}")
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
