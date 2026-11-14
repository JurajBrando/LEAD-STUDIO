"""
Lyric video maker - songs + bilingual lyrics + one picture -> lyric videos (and an album video).

    python video_maker.py --spec spec.json

The spec (Lead Studio's Video maker page writes it for you):
  audio        list of song files (mp3, wav, m4a ...), relative to the workspace or absolute
  lyrics       a text file with the lyrics (formats below)
  image        the background picture
  orientation  "16:9" (YouTube) or "9:16" (Shorts, Reels, TikTok)
  language     the sung language, as a Whisper code: de, pt, sk, en, es ...  ("" = detect)
  top_label    name of the sung language, shown on the title card ("Deutsch")
  bottom_label name of the translation language ("Slovenčina")
  title        the song title (one song) - empty uses the "# Title" line in the lyrics or the file name
  album        shown in the corner, e.g. "Lieder vom Mandelbaum"
  combine      true: also join every song into one album video with a chapter list
  translate_to a language name: when the lyrics have no translation, Ollama Cloud writes one
  model        Whisper model: tiny, base, small (default), medium
  timing       a timing CSV written by an earlier run, edited by hand: re-renders with those times
  out_dir      where the videos go (default VIDEO_STUDIO/output)

Lyrics formats - all three work, and one file can hold several songs, each starting with "# Title":
  1. one line per sung line, original and translation separated by  ||  or a TAB
        Wie geht es Ihnen? || Ako sa máte?
  2. the whole original first, then a line with ---, then the whole translation, line for line
  3. the original only (add translate_to, or the videos show one language)
  A line "[Chorus]", "REFRAIN", "REFRÃO", "REFRÉN" ... marks the stanza after it as the chorus.
  Sung more often than printed is fine: every repeat is found in the recording.

Each song gets  <title>.mp4  and  <title>.timing.csv  (start, end, both lines). Fix a line's time in
the CSV, put the CSV in "timing" and run again: it re-renders without transcribing.
"""
import os, sys, re, csv, json, glob, time, shutil, hashlib, argparse, subprocess, unicodedata, bisect
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.environ.get("LS_ROOT") or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
APP = os.path.dirname(os.path.abspath(__file__))
FPS = 30
LEAD, HOLD, MAXGAP = 0.25, 1.4, 2.5


def say(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------ dependencies
NEED = {"faster_whisper": "faster-whisper", "rapidfuzz": "rapidfuzz", "PIL": "pillow",
        "imageio_ffmpeg": "imageio-ffmpeg", "numpy": "numpy"}


def ensure_deps():
    import importlib.util
    miss = [pip for mod, pip in NEED.items() if importlib.util.find_spec(mod) is None]
    if miss:
        say("Installing what the video maker needs (one time):", " ".join(miss))
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet"] + miss)
        if r.returncode:
            say("pip could not install them. Run:  python -m pip install " + " ".join(miss)); sys.exit(2)


def ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(cmd, **kw):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", creationflags=flags, **kw)
    if r.returncode:
        raise RuntimeError("ffmpeg failed: " + (r.stderr or "")[-1500:])
    return r


def duration(path):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    r = subprocess.run([ffmpeg(), "-hide_banner", "-i", path], capture_output=True, text=True, errors="replace",
                       creationflags=flags)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or "")
    if not m:
        raise RuntimeError("Could not read the length of " + path)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def resolve(p):
    if not p:
        return ""
    p = os.path.expanduser(str(p))
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


# ------------------------------------------------------------------ lyrics
CHORUS = re.compile(r"^\s*[\[\(]?\s*(chorus|refrain|refr[aã]o|refr[eé]n|ref\.?|coro|estribillo|ritornello|"
                    r"pre-?chorus|hook)\s*[\]\)]?\s*:?\s*$", re.I)
SEP = re.compile(r"^\s*(-{3,}|={3,})\s*$")


def norm_tokens(s):
    s = unicodedata.normalize("NFKD", str(s).lower().replace("ß", "ss"))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", s)


def stanzas(lines):
    """[(is_chorus, [line, ...]), ...] - blank lines end a stanza, a chorus marker flags the next one."""
    out, cur, flag, pending = [], [], False, False
    for ln in lines + [""]:
        t = ln.strip()
        if CHORUS.match(t):
            if cur:
                out.append((flag, cur)); cur = []
            pending = True; continue
        if not t:
            if cur:
                out.append((flag, cur)); cur = []
            flag = False; continue
        if not cur:
            flag, pending = pending, False
        cur.append(t)
    return out


def parse_song(title, body):
    lines = body.splitlines()
    paired = any(("||" in l or "\t" in l) for l in lines)
    res = []
    if paired:
        for ch, st in stanzas(lines):
            for l in st:
                a, b = (re.split(r"\s*\|\|\s*|\t+", l, maxsplit=1) + [""])[:2]
                res.append(dict(a=a.strip(), b=b.strip(), chorus=ch))
        return res
    cut = next((i for i, l in enumerate(lines) if SEP.match(l)), None)
    if cut is not None:
        top, bot = stanzas(lines[:cut]), stanzas(lines[cut + 1:])
        ta = [(ch, l) for ch, st in top for l in st]
        tb = [l for _, st in bot for l in st]
        if len(ta) != len(tb):
            raise SystemExit("'%s': the original has %d lines but the translation has %d. They must match line for "
                             "line - or use one line per pair with || between them." % (title or "song", len(ta), len(tb)))
        return [dict(a=a, b=b, chorus=ch) for (ch, a), b in zip(ta, tb)]
    return [dict(a=l, b="", chorus=ch) for ch, st in stanzas(lines) for l in st]


def parse_lyrics(text):
    text = text.replace("\r\n", "\n").lstrip("\ufeff")
    parts = re.split(r"(?m)^\s*#\s+(.+?)\s*$", text)
    songs = []
    if parts[0].strip():
        songs.append(dict(title="", lines=parse_song("", parts[0])))
    for i in range(1, len(parts), 2):
        songs.append(dict(title=parts[i].strip(), lines=parse_song(parts[i], parts[i + 1])))
    return [s for s in songs if s["lines"]]


def split_long(a, b, maxlen):
    if len(a) <= maxlen:
        return [(a, b)]
    sp = lambda s: [m.end() for m in re.finditer(r"[,;:](?=\s)|[.!?](?=\s)", s)]
    pa, pb = sp(a), sp(b)
    if not pa or (b and len(pa) != len(pb)):
        return [(a, b)]
    k = min(range(len(pa)), key=lambda i: abs(pa[i] - len(a) / 2))
    a1, a2 = a[:pa[k]].strip(), a[pa[k]:].strip()
    b1, b2 = (b[:pb[k]].strip(), b[pb[k]:].strip()) if b else ("", "")
    if min(len(a1), len(a2)) < 12:
        return [(a, b)]
    return split_long(a1, b1, maxlen) + split_long(a2, b2, maxlen)


def display_lines(song, maxlen):
    out = []
    for l in song["lines"]:
        for a, b in split_long(l["a"], l["b"], maxlen):
            out.append(dict(a=a, b=b, chorus=l["chorus"], tok=norm_tokens(a)))
    return out


# ------------------------------------------------------------------ translation through Ollama Cloud
def ollama_cfg():
    try:
        with open(os.path.join(APP, "lead_studio_settings.json"), encoding="utf-8") as fh:
            s = json.load(fh)
    except (OSError, ValueError):
        s = {}
    key = os.environ.get("OLLAMA_API_KEY") or s.get("ollama_key") or ""
    return (s.get("ollama_base") or "https://ollama.com").rstrip("/"), key.strip(), s.get("ollama_model") or ""


def translate(lines, target, model=""):
    base, key, dm = ollama_cfg()
    model = model or dm
    if not key or not model:
        say("No Ollama key or model saved under Connections - the videos show the original only."); return
    for i in range(0, len(lines), 30):
        chunk = lines[i:i + 30]
        prompt = ("Translate these song lines into %s. Keep the meaning, keep it short, one output line per input "
                  "line. Answer with a JSON array of strings only, in the same order, nothing else.\n\n%s"
                  % (target, json.dumps([l["a"] for l in chunk], ensure_ascii=False)))
        body = json.dumps({"model": model, "stream": False, "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(base + "/api/chat", data=body, method="POST",
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                txt = json.loads(r.read().decode("utf-8", "replace"))["message"]["content"]
            arr = json.loads(re.search(r"\[.*\]", txt, re.S).group(0))
            for l, t in zip(chunk, arr):
                l["b"] = str(t).strip()
            say("  translated lines %d-%d" % (i + 1, i + len(chunk)))
        except Exception as e:
            say("  translation failed for lines %d-%d: %s" % (i + 1, i + len(chunk), e))


# ------------------------------------------------------------------ transcription and alignment
_MODEL = {}


def transcribe(audio, lang, model, work):
    import numpy as np
    st = os.stat(audio)
    key = hashlib.sha1(("%s|%s|%s|%s|%s" % (os.path.basename(audio), st.st_size, int(st.st_mtime), lang, model)).encode()).hexdigest()[:16]
    cache = os.path.join(work, "words_%s.json" % key)
    if os.path.exists(cache):
        say("  using the transcript from an earlier run")
        return json.load(open(cache, encoding="utf-8"))
    from faster_whisper import WhisperModel
    if model not in _MODEL:
        say("  loading the Whisper '%s' model (the first time it downloads, a few hundred MB)" % model)
        _MODEL[model] = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=max(1, (os.cpu_count() or 2) - 1))
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    raw = subprocess.run([ffmpeg(), "-v", "error", "-i", audio, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, creationflags=flags).stdout
    pcm = np.frombuffer(raw, dtype=np.float32)
    say("  transcribing %.0f seconds of audio" % (len(pcm) / 16000))
    segs, info = _MODEL[model].transcribe(pcm, language=lang or None, word_timestamps=True, beam_size=5, vad_filter=False)
    words = []
    for s in segs:
        say("   %6.1f  %s" % (s.start, s.text.strip()))
        words += [dict(w=w.word, s=w.start, e=w.end) for w in (s.words or [])]
    json.dump(words, open(cache, "w", encoding="utf-8"), ensure_ascii=False)
    return words


def tokens_of(words):
    toks, times = [], []
    for w in words:
        for t in norm_tokens(w["w"]):
            toks.append(t); times.append((w["s"], w["e"]))
    return toks, times


def identify(toks, songs, maxlen):
    bg = lambda t: set(zip(t, t[1:]))
    tb = bg(toks)
    sc = []
    for i, s in enumerate(songs):
        lt = [t for l in display_lines(s, maxlen) for t in l["tok"]]
        sc.append((len(tb & bg(lt)) / max(1, len(tb)), i))
    return sorted(sc, reverse=True)


def align(lines, toks, times, thr=62):
    from rapidfuzz import fuzz
    N = len(toks)
    joined = [None] * N
    cands = []
    for li, l in enumerate(lines):
        n = len(l["tok"]); ls = " ".join(l["tok"])
        if not n:
            continue
        row = []
        for s in range(N):
            best = (0, 0)
            for m in range(max(1, n - 3), n + 4):
                if s + m > N:
                    break
                r = fuzz.ratio(ls, " ".join(toks[s:s + m]))
                if r > best[0]:
                    best = (r, m)
            row.append(best)
        for s, (r, m) in enumerate(row):
            if r < thr:
                continue
            lo, hi = max(0, s - n // 2), min(N, s + n // 2 + 1)
            if any(row[k][0] > r for k in range(lo, hi) if k != s):
                continue
            cands.append((s, s + m, (r / 100) ** 3 * len(ls), li, r))
    cands.sort(key=lambda c: c[1])
    ends = [c[1] for c in cands]
    dp = [0.0] * (len(cands) + 1); ch = [None] * (len(cands) + 1)
    for j, c in enumerate(cands, 1):
        p = bisect.bisect_right(ends, c[0], 0, j - 1)
        if dp[p] + c[2] > dp[j - 1]:
            dp[j] = dp[p] + c[2]; ch[j] = p
        else:
            dp[j] = dp[j - 1]
    sel, j = [], len(cands)
    while j > 0:
        if ch[j] is not None:
            sel.append(cands[j - 1]); j = ch[j]
        else:
            j -= 1
    sel.reverse()
    ev = []
    for s, e, w, li, r in sel:
        st, en = times[s]
        if en - st > 1.0:
            st = en - 0.4                     # a stretched first word starts late, not early
        ev.append(dict(line=li, s=st, e=times[e - 1][1], ts=s, te=e))
    filled = []                               # lines the transcript garbled, between two that were found
    for a, b in zip(ev, ev[1:] + [None]):
        filled.append(a)
        if b is None:
            continue
        k = b["line"] - a["line"] - 1
        if 1 <= k <= 2 and b["s"] - a["e"] > 0.6 * k:
            span = (b["s"] - a["e"]) / k
            for x in range(k):
                filled.append(dict(line=a["line"] + 1 + x, s=a["e"] + x * span, e=a["e"] + (x + 1) * span - 0.1, fill=True))
    cov = sum(e["te"] - e["ts"] for e in ev) / max(1, N)
    return filled, cov


# ------------------------------------------------------------------ drawing
FONT_DIRS = [os.path.join(APP, "fonts"), os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
             "/usr/share/fonts/opentype/inter", "/usr/share/fonts/truetype/dejavu", "/Library/Fonts", "/System/Library/Fonts"]
FONTS = {"bold": ["Inter-Bold.otf", "Inter-Bold.ttf", "segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"],
         "semi": ["Inter-SemiBold.otf", "Inter-SemiBold.ttf", "seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"],
         "italic": ["Inter-MediumItalic.otf", "segoeuii.ttf", "ariali.ttf", "DejaVuSans-Oblique.ttf"],
         "medium": ["Inter-Medium.otf", "segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"],
         "display": ["InterDisplay-Bold.otf", "Inter-Bold.otf", "segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"]}
_FC = {}


def font(kind, size):
    from PIL import ImageFont
    if (kind, size) in _FC:
        return _FC[kind, size]
    for d in FONT_DIRS:
        for n in FONTS[kind]:
            p = os.path.join(d, n)
            if os.path.exists(p):
                _FC[kind, size] = ImageFont.truetype(p, size); return _FC[kind, size]
    _FC[kind, size] = ImageFont.load_default(size=size) if hasattr(ImageFont, "load_default") else ImageFont.load_default()
    return _FC[kind, size]


class Style:
    def __init__(self, image, orientation, header):
        from PIL import Image, ImageDraw, ImageFilter
        self.I, self.D, self.F = Image, ImageDraw, ImageFilter
        self.v = orientation == "9:16"
        self.W, self.H = (1080, 1920) if self.v else (1920, 1080)
        self.CX = self.W // 2
        self.CY = 1000 if self.v else 560
        self.TW = 940 if self.v else 1760
        self.sizes = (72, 50) if self.v else (112, 84)      # big enough to read on a phone
        self.maxlen = 40 if self.v else 46
        self.bg = self._bg(image, header)

    def _bg(self, image, header):
        I, W, H = self.I, self.W, self.H
        src = I.open(image).convert("RGB")
        s = max(W / src.width, H / src.height)
        im = src.resize((round(src.width * s), round(src.height * s)), I.LANCZOS)
        x, y = (im.width - W) // 2, (im.height - H) // 2
        im = im.crop((x, y, x + W, y + H)).filter(self.F.GaussianBlur(2.5 if s > 1.6 else 1.2))
        sh = I.new("L", (W, H)); px = sh.load()
        for yy in range(H):
            d = abs(yy - self.CY) / (700 if self.v else 560)
            a = int(140 + 80 * max(0, 1 - d * d))
            for xx in range(W):
                px[xx, yy] = a
        im = I.composite(I.new("RGB", (W, H), (8, 14, 26)), im, sh)
        if header:
            d = self.D.Draw(im); f = font("semi", 34)
            while d.textlength(header, font=f) > self.W - 120 and f.size > 18:
                f = font("semi", f.size - 2)
            d.text((self.CX, 150 if self.v else 70), header, font=f, fill=(255, 255, 255), anchor="mm")
        return im

    def wrap(self, text, f, maxw, d):
        words, lines, cur = text.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if d.textlength(t, font=f) <= maxw or not cur:
                cur = t
            else:
                lines.append(cur); cur = w
        if cur:
            lines.append(cur)
        if len(lines) == 2:                   # even out a two-line wrap
            best = None
            for k in range(1, len(words)):
                a, b = " ".join(words[:k]), " ".join(words[k:])
                m = max(d.textlength(a, font=f), d.textlength(b, font=f))
                if m <= maxw and (best is None or m < best[0]):
                    best = (m, [a, b])
            if best:
                lines = best[1]
        return lines

    def shadowed(self, draw_fn):
        L = self.I.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        sh = self.I.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        draw_fn(self.D.Draw(sh), (0, 0, 0, 230))
        sh = sh.filter(self.F.GaussianBlur(7))
        L.alpha_composite(sh); L.alpha_composite(sh)
        draw_fn(self.D.Draw(L), None)
        return L

    def text(self, a, b, tag=None):
        d = self.D.Draw(self.I.new("RGB", (8, 8)))
        ps, bs = self.sizes
        while True:
            fa, fb = font("bold", ps), font("semi", bs)
            la = self.wrap(a, fa, self.TW, d); lb = self.wrap(b, fb, self.TW, d) if b else []
            if (len(la) <= (3 if self.v else 2) and len(lb) <= 3) or ps <= self.sizes[0] * 0.7:
                break
            ps -= 6; bs -= 4
        ah, bh, gap = int(ps * 1.2), int(bs * 1.25), (56 if lb else 0)
        total = len(la) * ah + gap + len(lb) * bh
        y0 = self.CY - total // 2

        def draw(dd, col):
            y = y0
            if tag:
                dd.text((self.CX, y - 64), tag, font=font("bold", 38), fill=col or (255, 255, 255, 215), anchor="mm")
            for l in la:
                dd.text((self.CX, y + ah // 2), l, font=fa, fill=col or (255, 255, 255), anchor="mm"); y += ah
            if lb:
                y += gap
                if not col:
                    dd.line((self.CX - 80, y - gap // 2, self.CX + 80, y - gap // 2), fill=(255, 255, 255, 150), width=4)
                for l in lb:
                    dd.text((self.CX, y + bh // 2), l, font=fb, fill=col or (255, 214, 110), anchor="mm"); y += bh
        return self.shadowed(draw)

    def title(self, t1, t2, sub, labels):
        d = self.D.Draw(self.I.new("RGB", (8, 8)))
        size = 150 if self.v else 160
        while True:
            ft = font("display", size); tl = self.wrap(t1, ft, self.TW, d)
            if len(tl) <= 2 or size <= 80:
                break
            size -= 10
        lh = int(size * 1.1)
        y0 = self.CY - 100 - (len(tl) * lh) // 2

        def draw(dd, col):
            y = y0
            for l in tl:
                dd.text((self.CX, y + lh // 2), l, font=ft, fill=col or (255, 255, 255), anchor="mm"); y += lh
            if t2:
                dd.text((self.CX, y + 60), t2, font=font("display", 96), fill=col or (255, 214, 110), anchor="mm"); y += 110
            if sub:
                dd.text((self.CX, y + 50), sub, font=font("medium", 48), fill=col or (255, 255, 255, 220), anchor="mm"); y += 70
            if labels:
                dd.text((self.CX, y + 50), labels, font=font("medium", 38), fill=col or (255, 255, 255, 190), anchor="mm")
        return self.shadowed(draw)


# ------------------------------------------------------------------ rendering
def scenes_for(events, dur):
    f = lambda t: round(t * FPS)
    total, sc, cur = f(dur), [], 0
    first = f(max(0.5, events[0]["s"] - LEAD)) if events else total
    sc.append(("title", None, 0, first)); cur = first
    for i, e in enumerate(events):
        s = e["s"] - LEAD
        if i + 1 < len(events):
            nxt = events[i + 1]["s"] - LEAD
            end = e["e"] + HOLD if nxt - e["e"] > MAXGAP else nxt
        else:
            end = min(e["e"] + 2.5, dur - 0.5)
        f0, f1 = max(f(s), cur), min(f(end), total)
        if f1 - f0 < 6:
            continue
        if f0 > cur:
            sc.append(("bg", None, cur, f0))
        sc.append(("line", i, f0, f1)); cur = f1
    if total > cur:
        sc.append(("end", None, cur, total))
    return sc


def render(style, events, dur, audio, out, work, title_args, chorus_tag):
    os.makedirs(work, exist_ok=True)
    for p in glob.glob(os.path.join(work, "s*.mp4")) + glob.glob(os.path.join(work, "s*.png")):
        os.remove(p)
    style.bg.save(os.path.join(work, "bg.png"))
    sc = scenes_for(events, dur)
    ff = ffmpeg()
    enc = ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", str(FPS)]
    jobs = []
    for n, (kind, idx, f0, f1) in enumerate(sc):
        d = (f1 - f0) / FPS; name = "s%04d.mp4" % n
        base = [ff, "-v", "error", "-y", "-framerate", str(FPS), "-i", "bg.png"]
        if kind in ("bg", "end"):
            vf = "format=yuv420p,loop=-1:1,trim=duration=%.4f" % d
            if kind == "end":
                vf += ",fade=out:st=%.3f:d=%.3f" % (max(0, d - 2), min(2, d))
            cmd = base + ["-vf", vf]
        else:
            lay = style.title(*title_args) if kind == "title" else \
                style.text(events[idx]["a"], events[idx]["b"], chorus_tag if events[idx].get("chorus") else None)
            lp = "s%04d.png" % n; lay.save(os.path.join(work, lp))
            fin = "fade=in:st=0.8:d=1:alpha=1" if kind == "title" else "fade=in:st=0:d=0.2:alpha=1"
            fc = ("[0]format=yuv420p,loop=-1:1,trim=duration=%.4f[b];[1]format=rgba,loop=-1:1,trim=duration=%.4f,%s[t];"
                  "[b][t]overlay=format=auto,format=yuv420p" % (d, d, fin))
            if kind == "title":
                fc += ",fade=in:st=0:d=1"
            cmd = base + ["-framerate", str(FPS), "-i", lp, "-filter_complex", fc]
        jobs.append((cmd + enc + ["-frames:v", str(f1 - f0), "tmp_" + name], name))
    done = [0]

    def go(j):
        run(j[0], cwd=work); os.replace(os.path.join(work, "tmp_" + j[1]), os.path.join(work, j[1]))
        done[0] += 1
        if done[0] % 20 == 0 or done[0] == len(jobs):
            say("  drawn %d of %d scenes" % (done[0], len(jobs)))
    with ThreadPoolExecutor(max(1, min(4, (os.cpu_count() or 2) // 2))) as ex:
        list(ex.map(go, jobs))
    with open(os.path.join(work, "list.txt"), "w", encoding="utf-8") as fh:
        fh.write("".join("file '%s'\n" % j[1] for j in jobs))
    run([ff, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "video.mp4"], cwd=work)
    tmp = out + ".part.mp4"
    run([ff, "-v", "error", "-y", "-i", os.path.join(work, "video.mp4"), "-i", audio, "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", tmp])
    os.replace(tmp, out)
    for p in glob.glob(os.path.join(work, "s*.mp4")) + glob.glob(os.path.join(work, "s*.png")):
        os.remove(p)


def write_timing(path, events):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["start", "end", "chorus", "original", "translation"])
        for e in events:
            w.writerow(["%.2f" % e["s"], "%.2f" % e["e"], "yes" if e.get("chorus") else "", e["a"], e["b"]])


def read_timing(path):
    ev = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            try:
                ev.append(dict(s=float(r["start"]), e=float(r["end"]), chorus=bool((r.get("chorus") or "").strip()),
                               a=r.get("original") or "", b=r.get("translation") or ""))
            except (KeyError, ValueError):
                continue
    return sorted(ev, key=lambda e: e["s"])


def safe_name(s):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s).strip().strip(".")
    return s[:120] or "song"


def hms(t):
    t = int(t)
    return "%d:%02d:%02d" % (t // 3600, t % 3600 // 60, t % 60) if t >= 3600 else "%02d:%02d" % (t // 60, t % 60)


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", required=True)
    a = ap.parse_args()
    sp = json.load(open(a.spec, encoding="utf-8"))
    ensure_deps()

    audios = [resolve(x) for x in (sp.get("audio") or []) if str(x).strip()]
    for p in audios:
        if not os.path.exists(p):
            raise SystemExit("Song file not found: " + p)
    if not audios:
        raise SystemExit("Pick at least one song file.")
    image = resolve(sp.get("image"))
    if not image or not os.path.exists(image):
        raise SystemExit("Pick a background picture.")
    out_dir = resolve(sp.get("out_dir") or "VIDEO_STUDIO/output"); os.makedirs(out_dir, exist_ok=True)
    work_root = resolve("VIDEO_STUDIO/work"); os.makedirs(work_root, exist_ok=True)
    orient = "9:16" if str(sp.get("orientation")) == "9:16" else "16:9"
    lang = str(sp.get("language") or "").strip().lower()[:5]
    model = str(sp.get("model") or "small")
    album = str(sp.get("album") or "").strip()
    top_l, bot_l = str(sp.get("top_label") or "").strip(), str(sp.get("bottom_label") or "").strip()
    labels = "  ·  ".join(x for x in (top_l, bot_l) if x)
    chorus_tag = str(sp.get("chorus_label") or "CHORUS")

    songs = []
    if sp.get("lyrics"):
        lp = resolve(sp["lyrics"])
        if not os.path.exists(lp):
            raise SystemExit("Lyrics file not found: " + lp)
        songs = parse_lyrics(open(lp, encoding="utf-8-sig", errors="replace").read())
        say("Lyrics: %d song(s), %d lines" % (len(songs), sum(len(s["lines"]) for s in songs)))
    if sp.get("translate_to"):
        for s in songs:
            if s["lines"] and not any(l["b"] for l in s["lines"]):
                say("Translating '%s' into %s through Ollama Cloud" % (s["title"] or "the lyrics", sp["translate_to"]))
                translate(s["lines"], sp["translate_to"], sp.get("translate_model") or "")

    made = []
    for n, audio in enumerate(audios, 1):
        base = os.path.splitext(os.path.basename(audio))[0]
        say("\n[%d/%d] %s" % (n, len(audios), os.path.basename(audio)))
        work = os.path.join(work_root, safe_name(base)); os.makedirs(work, exist_ok=True)
        dur = duration(audio)
        style = None
        timing = resolve(sp.get("timing")) if sp.get("timing") and len(audios) == 1 else ""
        title = str(sp.get("title") or "").strip() if len(audios) == 1 else ""
        if timing:
            say("  using the times in " + os.path.basename(timing))
            events = read_timing(timing)
            title = title or re.sub(r"\.timing$", "", os.path.splitext(os.path.basename(timing))[0])
        else:
            if not songs:
                raise SystemExit("Add the lyrics - the video maker needs the words to show.")
            words = transcribe(audio, lang, model, work)
            toks, times = tokens_of(words)
            style0 = 40 if orient == "9:16" else 46
            pick = identify(toks, songs, style0) if len(songs) > 1 else [(1.0, 0)]
            song = songs[pick[0][1]]
            if len(songs) > 1:
                say("  sings '%s' (match %.0f%%)" % (song["title"] or "song %d" % (pick[0][1] + 1), 100 * pick[0][0]))
            L = display_lines(song, style0)
            ev, cov = align(L, toks, times)
            shown = {e["line"] for e in ev}
            miss = [L[k]["a"] for k in range(len(L)) if k not in shown]
            say("  %d lines on screen, %.0f%% of the singing matched%s" % (len(ev), 100 * cov,
                ("; not found: " + " / ".join(miss[:6])) if miss else ""))
            events = [dict(s=e["s"], e=e["e"], a=L[e["line"]]["a"], b=L[e["line"]]["b"], chorus=L[e["line"]]["chorus"]) for e in ev]
            title = title or song["title"] or base
        if not events:
            say("  nothing to show - skipped"); continue
        header = "  ·  ".join(x for x in (title, album) if x)
        style = Style(image, orient, header)
        out = os.path.join(out_dir, safe_name(title) + (" (9x16)" if orient == "9:16" else "") + ".mp4")
        write_timing(os.path.splitext(out)[0] + ".timing.csv", events)
        say("  rendering %s, %.0f seconds" % (os.path.basename(out), dur))
        t0 = time.time()
        render(style, events, dur, audio, out, work, (title, str(sp.get("subtitle") or ""), album if album != title else "", labels), chorus_tag)
        say("  done in %.1f min -> %s" % ((time.time() - t0) / 60, out))
        made.append((title, out, dur))

    if sp.get("combine") and len(made) > 1:
        name = safe_name(album or "Album") + (" (9x16)" if orient == "9:16" else "") + " - full album"
        lst = os.path.join(work_root, "album_list.txt")
        with open(lst, "w", encoding="utf-8") as fh:
            fh.write("".join("file '%s'\n" % o.replace("\\", "/").replace("'", "'\\''") for _, o, _ in made))
        out = os.path.join(out_dir, name + ".mp4")
        run([ffmpeg(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart", out + ".part.mp4"])
        os.replace(out + ".part.mp4", out)
        t, ch = 0.0, []
        for title, _, d in made:
            ch.append("%s %s" % (hms(t), title)); t += d
        open(os.path.join(out_dir, name + " - chapters.txt"), "w", encoding="utf-8").write("\n".join(ch) + "\n")
        say("\nAlbum video: %s (%s)\nYouTube chapters:\n%s" % (out, hms(t), "\n".join(ch)))
    say("\nFinished: %d video(s) in %s" % (len(made), out_dir))


if __name__ == "__main__":
    main()
