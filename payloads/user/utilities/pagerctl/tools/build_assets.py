#!/usr/bin/env python3
"""
build_assets.py  --  DEV-MACHINE asset pre-render for Loki v2 (no-binaries).

The Pager has no freetype/PIL and per-pixel Python is ~0.3 fps, so all font
rasterization and image decode/scale/rotate happens HERE (needs Pillow+numpy),
producing data blobs the device blits with pure row-wise memcpy.

Device native framebuffer: 222x480 portrait, RGB565 LE, stride 444.
Logical landscape (rotation 270): 480x222.  Logical portrait (rotation 0) == native.

Coordinate model (MUST match lib/pagerctl.py):
  rot 0  : native(nrow,ncol) = (y+dy, x+dx)            ; asset stored as-is
  rot 270: native(nrow,ncol) = (LW-(x)-cw + r, y+sx)   ; asset stored rot90(k=1)
           where LW=480, and a glyph/box occupies native rows along -logical_x.
  A landscape asset (H rows, W cols) -> np.rot90(k=1) -> (W rows, H cols) native.

Outputs (written next to sources):
  <font>.fatlas    glyph atlas (per rot, per size, RLE spans)
  <image>.r565     native-resolution opaque RGB565 (lazy-scale fallback source)
  prewarm/<...>    pre-scaled+rotated native blobs for known theme sizes

Binary formats (little-endian):
  .r565  : b'R565' u8 ver=1 | u16 w | u16 h | w*h*2 RGB565
  .fatlas: b'FATL' u8 ver=2 | u8 nrot
             per rot: u16 rot | u8 nsizes
               per size: u8 size | u16 ascent | u16 line_h | u8 nglyphs
                 per glyph: u8 cp | u8 advance | u8 grows | u16 nspans
                   per span: u8 r | u8 c | u8 len
  .nbg   : b'NBG1' u8 rot | u16 nrows | u16 ncols | nrows*ncols*2 RGB565   (opaque)
  .nspr  : b'NSPR' u8 rot | u16 nrows | u16 ncols
             per nrow: u16 nruns | runs: (u16 c, u16 len, len*2 RGB565)    (transparent)

Usage:
  python3 tools/build_assets.py --fonts-only
  python3 tools/build_assets.py --images-only
  python3 tools/build_assets.py --prewarm --theme loki
"""
import argparse, json, os, struct, sys
from PIL import Image, ImageFont, ImageDraw
import numpy as np

ASCII = list(range(32, 127))
EXTRA = [0xB0]
LW = 480  # logical landscape width (for rot 270 placement math)


# ----------------------------- RGB565 helpers -----------------------------
def rgb565_u16(rgb):
    r, g, b = rgb[:, :, 0].astype(np.uint16), rgb[:, :, 1].astype(np.uint16), rgb[:, :, 2].astype(np.uint16)
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def to_native_u16(u16, rot):
    if rot == 270:
        return np.rot90(u16, k=1)
    if rot == 90:
        return np.rot90(u16, k=3)
    if rot == 180:
        return np.rot90(u16, k=2)
    return u16  # rot 0


def to_native_alpha(a, rot):
    return to_native_u16(a, rot)  # same rotation for the alpha plane


# ----------------------------- images -> .r565 ----------------------------
def convert_image(path):
    try:
        img = Image.open(path).convert("RGB")
    except Exception as e:
        return None, f"open failed: {e}"
    w, h = img.size
    blob = b"R565" + struct.pack("<BHH", 1, w, h) + rgb565_u16(np.asarray(img)).astype("<u2").tobytes()
    out = path + ".r565"
    open(out, "wb").write(blob)
    return out, None


# -------------------------- native prewarm blobs --------------------------
def encode_nbg(img_rgb, w, h, rot):
    """Opaque: scale landscape RGB image to (w,h), rotate to native, pack."""
    im = img_rgb.convert("RGB").resize((w, h), Image.BILINEAR)
    nat = to_native_u16(rgb565_u16(np.asarray(im)), rot).astype("<u2")
    nr, nc = nat.shape
    return b"NBG1" + struct.pack("<HHH", rot, nr, nc) + nat.tobytes()


def encode_nspr(img_rgba, w, h, rot):
    """Transparent: scale RGBA to (w,h), rotate, emit per-row opaque runs."""
    im = img_rgba.convert("RGBA").resize((w, h), Image.BILINEAR)
    arr = np.asarray(im)
    u16 = to_native_u16(rgb565_u16(arr[:, :, :3]), rot).astype("<u2")
    alpha = to_native_alpha(arr[:, :, 3], rot)
    nr, nc = u16.shape
    out = [b"NSPR", struct.pack("<HHH", rot, nr, nc)]
    for r in range(nr):
        row_u16 = u16[r]
        a = alpha[r]
        runs = []
        c = 0
        while c < nc:
            if a[c] > 127:
                s = c
                while c < nc and a[c] > 127:
                    c += 1
                runs.append((s, c - s, row_u16[s:c].tobytes()))
            else:
                c += 1
        out.append(struct.pack("<H", len(runs)))
        for (col, ln, px) in runs:
            out.append(struct.pack("<HH", col, ln) + px)
    return b"".join(out)


# ------------------------------ font atlases ------------------------------
def render_glyph(font, cp, line_h):
    ch = chr(cp)
    try:
        adv = int(round(font.getlength(ch)))
    except Exception:
        adv = 0
    cell_w = max(adv, 1) + 4
    im = Image.new("L", (cell_w, line_h), 0)
    ImageDraw.Draw(im).text((0, 0), ch, font=font, fill=255)
    return adv, np.asarray(im, dtype=np.uint8)


def spans_of(mask):
    spans = []
    h, w = mask.shape
    for r in range(h):
        line = mask[r]
        c = 0
        while c < w:
            if line[c] > 127:
                s = c
                while c < w and line[c] > 127:
                    c += 1
                run = c - s
                while run > 0:
                    seg = min(run, 255)
                    spans.append((r, s, seg)); s += seg; run -= seg
            else:
                c += 1
    return spans


def build_atlas(font_path, sizes, rots):
    chunks = [b"FATL", struct.pack("<BB", 2, len(rots))]
    for rot in rots:
        size_blobs = []
        for size in sizes:
            try:
                font = ImageFont.truetype(font_path, size)
            except Exception:
                continue
            ascent, descent = font.getmetrics()
            line_h = ascent + descent
            gblobs = []
            for cp in ASCII + EXTRA:
                try:
                    adv, mask = render_glyph(font, cp, line_h)
                except Exception:
                    continue
                m = to_native_alpha(mask, rot)
                sp = spans_of(m)
                grows = m.shape[0]
                gb = struct.pack("<BBBH", cp, min(adv, 255), min(grows, 255), len(sp))
                gb += b"".join(struct.pack("<BBB", r, c, ln) for (r, c, ln) in sp)
                gblobs.append(gb)
            sb = struct.pack("<BHHB", size, ascent, line_h, len(gblobs)) + b"".join(gblobs)
            size_blobs.append(sb)
        chunks.append(struct.pack("<HB", rot, len(size_blobs)))
        chunks.extend(size_blobs)
    out = os.path.splitext(font_path)[0] + ".fatlas"
    blob = b"".join(chunks)
    open(out, "wb").write(blob)
    return out, len(blob)


# ------------------------------ prewarm pass ------------------------------
def theme_boxes(theme_dir):
    """Return {'landscape': (char_w,char_h), 'portrait': (char_w,char_h)} from theme.json."""
    tj = os.path.join(theme_dir, "theme.json")
    boxes = {"landscape": (170, 170), "portrait": (145, 145)}
    try:
        d = json.load(open(tj))
        for orient, key in (("landscape", "skin_layout_landscape"), ("portrait", "skin_layout_portrait")):
            c = d.get(key, {}).get("character")
            if c and "w" in c and "h" in c:
                boxes[orient] = (c["w"], c["h"])
    except Exception:
        pass
    return boxes


def prewarm_blob_path(src, w, h, rot, ext):
    d = os.path.join(os.path.dirname(src), "prewarm")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{os.path.basename(src)}.{w}x{h}.r{rot}.{ext}")


def prewarm_theme(theme_dir):
    name = os.path.basename(theme_dir.rstrip("/"))
    boxes = theme_boxes(theme_dir)
    n_bg = n_spr = 0
    # backgrounds -> full screen
    img_dir = os.path.join(theme_dir, "images")
    bg_map = [  # (filename, w, h, rot)
        ("main_bg.png", 480, 222, 270), ("pause_bg.png", 480, 222, 270),
        ("menu_bg.png", 480, 222, 270), ("settings_bg.png", 480, 222, 270),
        ("main_bg_portrait.png", 222, 480, 0), ("pause_bg_portrait.png", 222, 480, 0),
    ]
    for fn, w, h, rot in bg_map:
        p = os.path.join(img_dir, fn)
        if os.path.exists(p):
            blob = encode_nbg(Image.open(p), w, h, rot)
            open(prewarm_blob_path(p, w, h, rot, "nbg"), "wb").write(blob); n_bg += 1
    # Per-orientation target (w,h,rot) sizes for status images: the character box
    # AND the status-icon size (skin_status.icon_size) — the small icon is drawn
    # at a different size than the big character, so it needs its own prewarm.
    def _icon_size(theme_data, key):
        try:
            return theme_data.get(key, {}).get("status", {}).get("icon_size")
        except Exception:
            return None
    try:
        td = json.load(open(os.path.join(theme_dir, "theme.json")))
    except Exception:
        td = {}
    sizes = {"landscape": [(boxes["landscape"][0], boxes["landscape"][1], 270)],
             "portrait":  [(boxes["portrait"][0], boxes["portrait"][1], 0)]}
    isl = _icon_size(td, "skin_layout_landscape")
    if isl:
        sizes["landscape"].append((isl, isl, 270))
    isp = _icon_size(td, "skin_layout_portrait")
    if isp:
        sizes["portrait"].append((isp, isp, 0))
    status_root = os.path.join(img_dir, "status")
    for dirpath, _, files in os.walk(status_root):
        for fn in files:
            if not fn.lower().endswith(".png"):
                continue
            p = os.path.join(dirpath, fn)
            img = Image.open(p)
            for orient in ("landscape", "portrait"):
                for (w, h, rot) in sizes[orient]:
                    blob = encode_nspr(img, w, h, rot)
                    open(prewarm_blob_path(p, w, h, rot, "nspr"), "wb").write(blob); n_spr += 1
    print(f"  [{name}] boxes={boxes}  icon_sizes=({isl},{isp})  bg={n_bg}  sprites={n_spr}")


# --------------------------------- main -----------------------------------
def parse_sizes(s):
    if "-" in s:
        a, b = s.split("-"); return list(range(int(a), int(b) + 1))
    return [int(x) for x in s.split(",")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--sizes", default="8-40")
    ap.add_argument("--rot", default="0,270")
    ap.add_argument("--fonts-only", action="store_true")
    ap.add_argument("--images-only", action="store_true")
    ap.add_argument("--prewarm", action="store_true")
    ap.add_argument("--theme", default=None, help="prewarm only this theme dir name")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    sizes = parse_sizes(args.sizes)
    rots = [int(x) for x in args.rot.split(",")]

    if args.prewarm:
        troot = os.path.join(root, "themes")
        themes = [args.theme] if args.theme else sorted(os.listdir(troot))
        print(f"== Prewarm native blobs: themes={themes} ==")
        for t in themes:
            td = os.path.join(troot, t)
            if os.path.isdir(td):
                prewarm_theme(td)
        return

    fonts, images = [], []
    for dp, _, files in os.walk(root):
        if "prewarm" in dp:
            continue
        for fn in files:
            low = fn.lower(); p = os.path.join(dp, fn)
            if low.endswith((".ttf", ".otf")):
                fonts.append(p)
            elif low.endswith((".png", ".bmp")):
                images.append(p)

    if not args.images_only:
        print(f"== Fonts: {len(set(fonts))} files, sizes {sizes[0]}-{sizes[-1]}, rots {rots} ==")
        total = 0
        for fp in sorted(set(fonts)):
            _, n = build_atlas(fp, sizes, rots); total += n
        print(f"  atlas total: {total/1024/1024:.2f} MiB")
    if not args.fonts_only:
        print(f"== Images: {len(set(images))} -> .r565 ==")
        ok = err = 0
        for ip in sorted(set(images)):
            _, e = convert_image(ip); ok += (e is None); err += (e is not None)
        print(f"  images: {ok} ok, {err} errors")


if __name__ == "__main__":
    sys.exit(main())
