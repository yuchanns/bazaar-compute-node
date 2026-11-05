"""The market street, drawn from computed curves: lanterns, stalls, tents and
the rooftops behind them. Every fill is a flat colour taken from a theme
variable (or mixed from two of them), so the picture follows the theme and
holds no gradients."""

from __future__ import annotations

import math
import random

HUES = ("saffron", "jade", "crimson", "indigo", "plum")


def n(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def pts(*xy: float) -> str:
    return " ".join(f"{n(xy[i])} {n(xy[i + 1])}" for i in range(0, len(xy), 2))


class Canvas:
    def __init__(self, w: int, h: int, seed: int = 7) -> None:
        self.w, self.h = w, h
        self.parts: list[str] = []
        self.r = random.Random(seed)

    def add(self, s: str) -> None:
        self.parts.append(s)

    def path(self, d: str, cls: str, extra: str = "") -> None:
        self.add(f'<path class="{cls}" d="{d}"{(" " + extra) if extra else ""}/>')

    def svg(self, cls: str = "street-art") -> str:
        return (f'<svg class="{cls}" viewBox="0 0 {self.w} {self.h}" preserveAspectRatio="xMidYMax slice" aria-hidden="true">'
                + "".join(self.parts) + "</svg>")


# -- the sky ---------------------------------------------------------------

def sky(c: Canvas, top: float, bottom: float, stars: int) -> None:
    c.add(f'<rect class="s-sky" x="0" y="0" width="{c.w}" height="{c.h}"/>')
    for _ in range(stars):
        x, y = c.r.uniform(0, c.w), c.r.uniform(top, bottom)
        r = c.r.choice((.7, .8, 1, 1.2))
        c.add(f'<circle class="s-star" cx="{n(x)}" cy="{n(y)}" r="{r}" opacity="{c.r.choice((.35, .5, .7, .9))}"/>')
    for _ in range(max(1, stars // 14)):
        x, y, s = c.r.uniform(40, c.w - 40), c.r.uniform(top, bottom), c.r.uniform(3, 5)
        c.path(f"M{n(x)} {n(y - s)} Q{n(x)} {n(y)} {n(x + s)} {n(y)} Q{n(x)} {n(y)} {n(x)} {n(y + s)} "
               f"Q{n(x)} {n(y)} {n(x - s)} {n(y)} Q{n(x)} {n(y)} {n(x)} {n(y - s)}Z", "s-star")


def moon(c: Canvas, x: float, y: float, r: float) -> None:
    # a crescent by day is the sun's disc; the bite only shows at night
    c.add(f'<circle class="s-moon" cx="{n(x)}" cy="{n(y)}" r="{n(r)}"/>')
    c.add(f'<circle class="s-bite" cx="{n(x + r * .42)}" cy="{n(y - r * .3)}" r="{n(r * .86)}"/>')


# -- the town behind the stalls --------------------------------------------

def rooftops(c: Canvas, base: float, x0: float, x1: float, tall: float) -> None:
    x = x0
    while x < x1:
        w = c.r.uniform(70, 130)
        h = c.r.uniform(.45, 1) * tall
        top = base - h
        eave = c.r.uniform(10, 16)
        lift = c.r.uniform(5, 9)
        # walls, then a roof whose eaves turn up at both ends
        c.add(f'<rect class="s-far" x="{n(x + 6)}" y="{n(top + eave)}" width="{n(w - 12)}" height="{n(h - eave)}"/>')
        c.path(f"M{n(x - 6)} {n(top + eave - lift)} Q{n(x + 4)} {n(top + eave)} {n(x + 14)} {n(top + eave)} "
               f"L{n(x + w - 14)} {n(top + eave)} Q{n(x + w - 4)} {n(top + eave)} {n(x + w + 6)} {n(top + eave - lift)} "
               f"L{n(x + w - 10)} {n(top)} L{n(x + 10)} {n(top)} Z", "s-far2")
        rows = int((h - eave - 18) // 20)
        for row in range(max(0, rows)):
            for col in range(int((w - 24) // 22)):
                if c.r.random() < .42:
                    c.add(f'<rect class="s-window" x="{n(x + 16 + col * 22)}" y="{n(top + eave + 12 + row * 20)}" width="9" height="11" rx="1.5"/>')
        x += w + c.r.uniform(-8, 6)


# -- lanterns --------------------------------------------------------------

def catenary(x0: float, y0: float, x1: float, y1: float, sag: float):
    """A point at t along a hanging string from (x0, y0) to (x1, y1)."""
    def at(t: float) -> tuple[float, float]:
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t + sag * (1 - (2 * t - 1) ** 2)
        return x, y
    return at


def string_path(at, steps: int = 40) -> str:
    p = [at(i / steps) for i in range(steps + 1)]
    return "M" + " L".join(f"{n(x)} {n(y)}" for x, y in p)


def lantern(c: Canvas, cx: float, hang: float, drop: float, w: float, hue: str, far: bool = False) -> None:
    h = w * 1.18
    cap = w * .11
    top = hang + drop
    y0, y1 = top + cap, top + cap + h
    ym = (y0 + y1) / 2
    k = "f-" if far else ""
    c.path(f"M{n(cx)} {n(hang)} V{n(top)}", f"s-cord {k}cord")
    # caps of wood or brass, a little wider at the rim
    c.add(f'<rect class="s-cap {k}cap" x="{n(cx - w * .3)}" y="{n(top)}" width="{n(w * .6)}" height="{n(cap)}" rx="{n(cap * .35)}"/>')
    # the paper body: a barrel, fuller at the waist
    tw = w * .29
    body = (f"M{n(cx - tw)} {n(y0)} C{n(cx - w * .64)} {n(y0 + h * .06)} {n(cx - w * .64)} {n(y1 - h * .06)} {n(cx - tw)} {n(y1)} "
            f"L{n(cx + tw)} {n(y1)} C{n(cx + w * .64)} {n(y1 - h * .06)} {n(cx + w * .64)} {n(y0 + h * .06)} {n(cx + tw)} {n(y0)} Z")
    c.path(body, f"s-{hue} {k}paper")
    # the side away from the light, cut by the outermost rib
    shade = (f"M{n(cx + tw * .55)} {n(y0)} Q{n(cx + w * .36)} {n(ym)} {n(cx + tw * .55)} {n(y1)} "
             f"L{n(cx + tw)} {n(y1)} C{n(cx + w * .64)} {n(y1 - h * .06)} {n(cx + w * .64)} {n(y0 + h * .06)} {n(cx + tw)} {n(y0)} Z")
    c.path(shade, f"s-{hue}-shade {k}paper")
    # the lit window of paper facing us
    glow = (f"M{n(cx - tw * .5)} {n(y0 + h * .14)} Q{n(cx - w * .33)} {n(ym)} {n(cx - tw * .5)} {n(y1 - h * .14)} "
            f"Q{n(cx + w * .04)} {n(ym)} {n(cx - tw * .5)} {n(y0 + h * .14)} Z")
    c.path(glow, f"s-{hue}-glow {k}paper")
    for f in (-.62, -.22, .22, .62):
        c.path(f"M{n(cx + f * tw)} {n(y0)} Q{n(cx + f * w * .58)} {n(ym)} {n(cx + f * tw)} {n(y1)}", f"s-rib {k}rib")
    c.add(f'<rect class="s-cap {k}cap" x="{n(cx - w * .26)}" y="{n(y1)}" width="{n(w * .52)}" height="{n(cap * .9)}" rx="{n(cap * .35)}"/>')
    # a knot, then a fringed tassel
    ty = y1 + cap * .9
    c.path(f"M{n(cx)} {n(ty)} V{n(ty + w * .16)}", f"s-cord {k}cord")
    c.add(f'<circle class="s-{hue}-shade {k}paper" cx="{n(cx)}" cy="{n(ty + w * .2)}" r="{n(w * .07)}"/>')
    fw, fh = w * .13, w * .42
    fy = ty + w * .26
    c.path(f"M{n(cx - fw * .45)} {n(fy)} L{n(cx + fw * .45)} {n(fy)} L{n(cx + fw)} {n(fy + fh)} L{n(cx - fw)} {n(fy + fh)} Z", f"s-{hue} {k}paper")
    for i in range(3):
        fx = cx - fw * .5 + i * fw * .5
        c.path(f"M{n(fx)} {n(fy + 2)} L{n(fx + (fx - cx) * .4)} {n(fy + fh)}", f"s-rib {k}rib")


def lantern_string(c: Canvas, x0, y0, x1, y1, sag, count, sizes, far=False, hues=HUES, offset=0):
    at = catenary(x0, y0, x1, y1, sag)
    c.path(string_path(at), "s-cord f-cord" if far else "s-cord", 'fill="none"')
    for i in range(count):
        t = (i + .5) / count
        x, y = at(t)
        w = sizes[i % len(sizes)]
        drop = 6 + ((i * 7 + offset) % 5) * 4
        lantern(c, x, y, drop, w, hues[(i + offset) % len(hues)], far)


# -- stalls ----------------------------------------------------------------

def scallops(c: Canvas, x0: float, x1: float, y: float, count: int, hue_cycle: list[str]) -> None:
    sw = (x1 - x0) / count
    r = sw / 2
    for i in range(count):
        a = x0 + i * sw
        cls = hue_cycle[i % len(hue_cycle)]
        c.path(f"M{n(a)} {n(y)} A{n(r)} {n(r * .9)} 0 0 0 {n(a + sw)} {n(y)} Z", cls)
        # the fold's underside, a thinner crescent
        c.path(f"M{n(a + sw * .18)} {n(y + r * .55)} A{n(r * .7)} {n(r * .35)} 0 0 0 {n(a + sw * .82)} {n(y + r * .55)} "
               f"A{n(r)} {n(r * .9)} 0 0 1 {n(a + sw * .18)} {n(y + r * .55)} Z", cls + "-shade")


def stall(c: Canvas, x: float, base: float, w: float, h: float, hue: str, goods: str, agents: tuple[str, ...]) -> None:
    top = base - h
    ah = h * .2            # the awning's depth
    ab = top + ah          # where its valance hangs
    counter = base - h * .34
    # shadow on the ground
    c.add(f'<ellipse class="s-ground-shade" cx="{n(x + w / 2)}" cy="{n(base)}" rx="{n(w * .58)}" ry="7"/>')
    # inside: the back wall, shelves, and the stall's agents hanging as lanterns
    c.add(f'<rect class="s-inside" x="{n(x + 8)}" y="{n(ab)}" width="{n(w - 16)}" height="{n(counter - ab)}"/>')
    upper, lower = ab + (counter - ab) * .46, ab + (counter - ab) * .86
    for sy in (upper, lower):
        c.add(f'<rect class="s-inside-shade" x="{n(x + 12)}" y="{n(sy)}" width="{n(w - 24)}" height="3"/>')
    trio = (hue, "jade" if hue != "jade" else "saffron", "indigo" if hue != "indigo" else "plum")
    jx, i = x + 20, 0
    while jx < x + w - 32:
        jh = (13, 16, 13)[i % 3]
        c.add(f'<rect class="s-{trio[i % 3]}-dim" x="{n(jx)}" y="{n(upper - jh)}" width="10" height="{n(jh)}" rx="3"/>')
        c.add(f'<rect class="s-inside-shade" x="{n(jx + 2)}" y="{n(upper - jh - 3)}" width="6" height="3" rx="1"/>')
        jx += 16
        i += 1
    bx = x + 22
    for bw in (26, 20, 30):
        if bx + bw > x + w - 22:
            break
        c.add(f'<rect class="s-wood-light" x="{n(bx)}" y="{n(lower - 15)}" width="{bw}" height="15" rx="2"/>')
        c.add(f'<rect class="s-wood-shade" x="{n(bx)}" y="{n(lower - 15)}" width="{bw}" height="3" rx="1"/>')
        bx += bw + 12
    for i, a in enumerate(agents):
        ax = x + w * (.3 + .4 * (i / max(1, len(agents) - 1) if len(agents) > 1 else .5))
        lantern(c, ax, ab + 2, 4, 13, a)
    # posts
    for px in (x + 4, x + w - 12):
        c.add(f'<rect class="s-wood" x="{n(px)}" y="{n(top + 4)}" width="8" height="{n(base - top - 4)}"/>')
        c.add(f'<rect class="s-wood-shade" x="{n(px + 5)}" y="{n(top + 4)}" width="3" height="{n(base - top - 4)}"/>')
    # the awning: stripes in a gentle trapezoid, a dark band under the front edge
    over = 12
    stripes = max(6, int(w // 26)) | 1
    l0, r0 = x - 2, x + w + 2            # top edge
    l1, r1 = x - over, x + w + over      # front edge
    cycle = [f"s-{hue}", "s-alt"]
    for i in range(stripes):
        a0, a1 = l0 + (r0 - l0) * i / stripes, l0 + (r0 - l0) * (i + 1) / stripes
        b0, b1 = l1 + (r1 - l1) * i / stripes, l1 + (r1 - l1) * (i + 1) / stripes
        c.path(f"M{n(a0)} {n(top)} L{n(a1)} {n(top)} L{n(b1)} {n(ab)} L{n(b0)} {n(ab)} Z", cycle[i % 2])
    c.path(f"M{n(l1)} {n(ab - 5)} L{n(r1)} {n(ab - 5)} L{n(r1)} {n(ab)} L{n(l1)} {n(ab)} Z", f"s-{hue}-shade", 'opacity=".55"')
    scallops(c, l1, r1, ab, stripes, cycle)
    # the ridge pole along the top
    c.add(f'<rect class="s-wood" x="{n(l0 - 4)}" y="{n(top - 5)}" width="{n(r0 - l0 + 8)}" height="7" rx="3.5"/>')
    # a string of small bulbs under the valance
    sw = (r1 - l1) / stripes
    for i in range(stripes + 1):
        bx = l1 + i * sw
        c.add(f'<circle class="s-bulb" cx="{n(bx)}" cy="{n(ab + 3)}" r="2.6"/>')
    # the counter: planks below, a board on top, goods on the board
    c.add(f'<rect class="s-wood" x="{n(x)}" y="{n(counter)}" width="{n(w)}" height="{n(base - counter)}"/>')
    for i in range(1, int(w // 18)):
        c.add(f'<rect class="s-wood-shade" x="{n(x + i * 18)}" y="{n(counter + 8)}" width="2" height="{n(base - counter - 8)}"/>')
    c.add(f'<rect class="s-wood-light" x="{n(x - 6)}" y="{n(counter - 4)}" width="{n(w + 12)}" height="9" rx="2"/>')
    goods_on(c, goods, x, counter - 4, w)


def goods_on(c: Canvas, kind: str, x: float, y: float, w: float) -> None:
    if kind == "fruit":
        for i, bx in enumerate((x + w * .22, x + w * .5, x + w * .78)):
            hue = HUES[(i * 2) % len(HUES)]
            for j in range(5):
                fx = bx - 12 + (j % 3) * 12 + (6 if j >= 3 else 0)
                fy = y - 8 - (8 if j >= 3 else 0)
                c.add(f'<circle class="s-{hue}" cx="{n(fx)}" cy="{n(fy)}" r="6"/>')
            c.path(f"M{n(bx - 22)} {n(y - 9)} L{n(bx + 22)} {n(y - 9)} L{n(bx + 17)} {n(y)} L{n(bx - 17)} {n(y)} Z", "s-wood-light")
            c.path(f"M{n(bx - 22)} {n(y - 9)} L{n(bx + 22)} {n(y - 9)} L{n(bx + 21)} {n(y - 5)} L{n(bx - 21)} {n(y - 5)} Z", "s-wood-shade")
    elif kind == "jars":
        jx = x + 14
        i = 0
        while jx < x + w - 24:
            hue = HUES[i % len(HUES)]
            jh = 22 + (i % 3) * 5
            c.add(f'<rect class="s-{hue}-dim" x="{n(jx)}" y="{n(y - jh)}" width="18" height="{n(jh)}" rx="5"/>')
            c.add(f'<rect class="s-{hue}-shade" x="{n(jx + 12)}" y="{n(y - jh)}" width="6" height="{n(jh)}" rx="3"/>')
            c.add(f'<rect class="s-wood" x="{n(jx + 2)}" y="{n(y - jh - 5)}" width="14" height="6" rx="2"/>')
            jx += 26
            i += 1
    elif kind == "steam":
        px = x + w * .5
        c.path(f"M{n(px - 30)} {n(y - 30)} L{n(px + 30)} {n(y - 30)} L{n(px + 26)} {n(y)} L{n(px - 26)} {n(y)} Z", "s-pot")
        c.add(f'<rect class="s-pot-shade" x="{n(px - 34)}" y="{n(y - 34)}" width="68" height="6" rx="3"/>')
        for k in (-14, 4, 20):
            sx = px + k
            c.path(f"M{n(sx)} {n(y - 40)} C{n(sx - 10)} {n(y - 52)} {n(sx + 10)} {n(y - 60)} {n(sx)} {n(y - 74)}", "s-steam", 'fill="none"')
        c.add(f'<rect class="s-crimson-dim" x="{n(x + 14)}" y="{n(y - 16)}" width="22" height="16" rx="3"/>')
        c.add(f'<rect class="s-jade-dim" x="{n(x + w - 36)}" y="{n(y - 20)}" width="22" height="20" rx="3"/>')
    elif kind == "books":
        bx = x + 14
        i = 0
        while bx < x + w - 20:
            hue = HUES[(i * 3) % len(HUES)]
            bh = 18 + (i * 5) % 12
            c.add(f'<rect class="s-{hue}-dim" x="{n(bx)}" y="{n(y - bh)}" width="9" height="{n(bh)}" rx="1"/>')
            bx += 11
            i += 1


# -- tents -----------------------------------------------------------------

def tent(c: Canvas, cx: float, base: float, w: float, h: float, hue: str, flag: str) -> None:
    apex = base - h
    eave = base - h * .42
    hw = w / 2
    wall = hw * .84
    c.add(f'<ellipse class="s-ground-shade" cx="{n(cx)}" cy="{n(base)}" rx="{n(w * .56)}" ry="7"/>')
    # the wall, its right side turned from the light, and a door tied open
    c.add(f'<rect class="s-alt" x="{n(cx - wall)}" y="{n(eave)}" width="{n(wall * 2)}" height="{n(base - eave)}"/>')
    c.add(f'<rect class="s-alt-shade" x="{n(cx + wall * .45)}" y="{n(eave)}" width="{n(wall * .55)}" height="{n(base - eave)}"/>')
    dw = wall * .5
    c.path(f"M{n(cx - dw)} {n(base)} L{n(cx - dw)} {n(eave + 22)} Q{n(cx)} {n(eave + 4)} {n(cx + dw)} {n(eave + 22)} L{n(cx + dw)} {n(base)} Z", "s-inside-lit")
    c.path(f"M{n(cx - dw)} {n(eave + 18)} Q{n(cx - dw * .2)} {n(eave + 30)} {n(cx - dw * .55)} {n(base)} L{n(cx - dw)} {n(base)} Z", f"s-{hue}")
    c.path(f"M{n(cx + dw)} {n(eave + 18)} Q{n(cx + dw * .2)} {n(eave + 30)} {n(cx + dw * .55)} {n(base)} L{n(cx + dw)} {n(base)} Z", f"s-{hue}-shade")
    # the roof: stripes from the apex to the eave, the sides drawn in a little
    count = 8
    for i in range(count):
        t0, t1 = i / count, (i + 1) / count
        e0, e1 = cx - hw + w * t0, cx - hw + w * t1
        m0 = cx + (e0 - cx) * .52
        m1 = cx + (e1 - cx) * .52
        ym = apex + (eave - apex) * .55
        cls = f"s-{hue}" if i % 2 == 0 else "s-alt"
        if t0 >= .62:
            cls += "-shade"
        c.path(f"M{n(cx)} {n(apex)} Q{n(m0 - (e0 - cx) * .04)} {n(ym)} {n(e0)} {n(eave)} L{n(e1)} {n(eave)} "
               f"Q{n(m1 - (e1 - cx) * .04)} {n(ym)} {n(cx)} {n(apex)} Z", cls)
    cycle = [f"s-{hue}", "s-alt"]
    scallops(c, cx - hw, cx + hw, eave, count, cycle)
    # the pole and its pennant
    c.add(f'<rect class="s-wood" x="{n(cx - 1.5)}" y="{n(apex - 26)}" width="3" height="28"/>')
    c.path(f"M{n(cx + 1.5)} {n(apex - 26)} L{n(cx + 22)} {n(apex - 20)} L{n(cx + 1.5)} {n(apex - 13)} Z", f"s-{flag}")
    c.add(f'<circle class="s-wood-light" cx="{n(cx)}" cy="{n(apex - 28)}" r="3"/>')


def ground(c: Canvas, y: float) -> None:
    c.add(f'<rect class="s-ground" x="0" y="{n(y)}" width="{c.w}" height="{n(c.h - y)}"/>')
    x = 0.0
    while x < c.w:
        w = c.r.uniform(18, 34)
        c.add(f'<rect class="s-ground-light" x="{n(x)}" y="{n(y + c.r.uniform(8, c.h - y - 8))}" width="{n(w)}" height="3" rx="1.5"/>')
        x += w + c.r.uniform(16, 40)


# -- the two compositions --------------------------------------------------

def desktop() -> str:
    c = Canvas(840, 900, seed=11)
    sky(c, 0, 560, 46)
    moon(c, 690, 126, 30)
    rooftops(c, 760, -20, 860, 150)
    lantern_string(c, -30, 76, 870, 58, 92, 9, (17, 21), far=True, offset=2)
    lantern_string(c, -30, 18, 870, 120, 112, 6, (40, 32), offset=0)
    ground(c, 862)
    stall(c, 14, 862, 172, 206, "indigo", "jars", ("indigo",))
    stall(c, 214, 862, 200, 236, "saffron", "fruit", ("saffron", "jade"))
    stall(c, 442, 862, 214, 252, "crimson", "steam", ("jade",))
    stall(c, 676, 862, 156, 214, "jade", "books", ("saffron", "jade"))
    return c.svg()


def phone() -> tuple[str, str]:
    top = Canvas(390, 150, seed=5)
    top.add('<rect class="s-sky" x="0" y="0" width="390" height="150"/>')
    # the form's lower edge: a downward arc
    top.path("M0 0 H390 V6 Q195 58 0 6 Z", "s-edge")
    lantern_string(top, -20, 62, 410, 58, 22, 6, (21, 17), offset=0)
    bottom = Canvas(390, 220, seed=9)
    sky(bottom, 0, 70, 10)
    rooftops(bottom, 200, -20, 410, 62)
    ground(bottom, 206)
    stall(bottom, 12, 206, 108, 118, "indigo", "jars", ("indigo",))
    stall(bottom, 138, 206, 128, 138, "saffron", "fruit", ("saffron", "jade"))
    stall(bottom, 284, 206, 96, 116, "jade", "books", ("jade",))
    return top.svg("street-top"), bottom.svg("street-bottom")
