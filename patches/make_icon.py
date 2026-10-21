#!/usr/bin/env python3
"""Original launcher icon: glowing green orb (original-Xbox palette) crossed
by a tilted energy ring. Writes adaptive-icon layers + legacy icons into
<res>/mipmap-*/.  usage: make_icon.py <res_dir> [preview.png]"""
import math
import sys
from PIL import Image, ImageDraw, ImageFilter

SS = 4  # supersampling
DENS = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}


def radial(size, stops, center=(0.5, 0.5), radius=0.5):
    img = Image.new("RGBA", (size, size))
    px = img.load()
    cx, cy, r = center[0] * size, center[1] * size, radius * size
    for y in range(size):
        for x in range(size):
            t = min(1.0, math.hypot(x - cx, y - cy) / r)
            for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
                if t0 <= t <= t1:
                    f = (t - t0) / (t1 - t0) if t1 > t0 else 0
                    px[x, y] = tuple(int(c0[i] + (c1[i] - c0[i]) * f) for i in range(4))
                    break
    return img


def background(n):
    return radial(n, [(0, (24, 64, 14, 255)), (0.55, (8, 26, 6, 255)), (1, (2, 6, 2, 255))],
                  radius=0.75)


def ring_layer(n, width, color, blur=0):
    lay = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    rx, ry = 0.40 * n, 0.13 * n
    box = (n / 2 - rx, n / 2 - ry, n / 2 + rx, n / 2 + ry)
    d.ellipse(box, outline=color, width=int(width))
    lay = lay.rotate(-24, resample=Image.BICUBIC, center=(n / 2, n / 2))
    if blur:
        lay = lay.filter(ImageFilter.GaussianBlur(blur))
    return lay


def foreground(n):
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    # outer glow
    glow = radial(n, [(0, (150, 255, 80, 170)), (0.45, (90, 200, 40, 90)), (1, (0, 0, 0, 0))],
                  radius=0.42)
    img = Image.alpha_composite(img, glow)
    # back half of the ring (behind the orb)
    img = Image.alpha_composite(img, ring_layer(n, 0.05 * n, (120, 255, 60, 140), blur=0.02 * n))
    img = Image.alpha_composite(img, ring_layer(n, 0.022 * n, (225, 255, 190, 255)))
    # orb
    orb_r = 0.235
    orb = radial(n, [(0, (215, 255, 160, 255)), (0.35, (125, 205, 55, 255)),
                     (0.8, (40, 110, 18, 255)), (0.97, (12, 45, 6, 255)), (1, (12, 45, 6, 0))],
                 center=(0.44, 0.40), radius=orb_r * 1.25)
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).ellipse((n / 2 - orb_r * n, n / 2 - orb_r * n,
                                  n / 2 + orb_r * n, n / 2 + orb_r * n), fill=255)
    orb.putalpha(mask)
    img = Image.alpha_composite(img, orb)
    # specular highlight
    hl = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ImageDraw.Draw(hl).ellipse((0.36 * n, 0.31 * n, 0.50 * n, 0.39 * n), fill=(255, 255, 255, 150))
    img = Image.alpha_composite(img, hl.filter(ImageFilter.GaussianBlur(0.012 * n)))
    # front half of the ring (in front of the orb): lower half only
    front = Image.alpha_composite(ring_layer(n, 0.05 * n, (120, 255, 60, 140), blur=0.02 * n),
                                  ring_layer(n, 0.022 * n, (225, 255, 190, 255)))
    cut = Image.new("L", (n, n), 0)
    ImageDraw.Draw(cut).rectangle((0, n / 2, n, n), fill=255)
    cut = cut.rotate(-24, resample=Image.BICUBIC, center=(n / 2, n / 2))
    front.putalpha(Image.composite(front.getchannel("A"), Image.new("L", (n, n), 0), cut))
    return Image.alpha_composite(img, front)


def monochrome(fg):
    a = fg.getchannel("A")
    out = Image.new("RGBA", fg.size, (255, 255, 255, 0))
    out.putalpha(a)
    return out


def main():
    res = sys.argv[1]
    N = 432 * SS // 4  # build at 432 (xxxhdpi adaptive layer)
    big = 432
    bg = background(big)
    fg = foreground(big * 2).resize((big, big), Image.LANCZOS)
    mono = monochrome(fg)
    legacy_full = Image.alpha_composite(bg, fg)
    for name, k in DENS.items():
        layer = int(108 * k)
        icon = int(48 * k)
        d = "%s/mipmap-%s" % (res, name)
        bg.resize((layer, layer), Image.LANCZOS).save(d + "/ic_launcher_background.png")
        fg.resize((layer, layer), Image.LANCZOS).save(d + "/ic_launcher_foreground.png")
        mono.resize((layer, layer), Image.LANCZOS).save(d + "/ic_launcher_monochrome.png")
        # legacy: crop the 72/108 visible area, round it
        c = big * 18 // 108
        leg = legacy_full.crop((c, c, big - c, big - c)).resize((icon, icon), Image.LANCZOS)
        m = Image.new("L", (icon * 4, icon * 4), 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, icon * 4 - 1, icon * 4 - 1), radius=icon, fill=255)
        leg.putalpha(m.resize((icon, icon), Image.LANCZOS))
        leg.save(d + "/ic_launcher.png")
    if len(sys.argv) > 2:
        prev = legacy_full.copy()
        m = Image.new("L", (big, big), 0)
        ImageDraw.Draw(m).ellipse((0, 0, big - 1, big - 1), fill=255)
        prev.putalpha(m)
        prev.save(sys.argv[2])


if __name__ == "__main__":
    main()
