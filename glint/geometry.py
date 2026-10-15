"""Pure geometry helpers. Rects are (x, y, w, h) tuples."""


def normalize(x0, y0, x1, y1):
    """Rect spanning two corner points, in any order."""
    return (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))


def intersect(a, b):
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[0] + a[2], b[0] + b[2])
    y1 = min(a[1] + a[3], b[1] + b[3])
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def contains(rect, x, y):
    return rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]


def union(rects):
    rects = list(rects)
    if not rects:
        return (0, 0, 0, 0)
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    return (x0, y0, x1 - x0, y1 - y0)


def to_pixels(rect, origin, ratio, limit=None):
    """Map a logical rect to integer pixel coords in an image whose top-left is
    `origin` (logical) and which has `ratio` pixels per logical unit."""
    x = round((rect[0] - origin[0]) * ratio)
    y = round((rect[1] - origin[1]) * ratio)
    w = round(rect[2] * ratio)
    h = round(rect[3] * ratio)
    if limit is not None:
        clipped = intersect((x, y, w, h), (0, 0, limit[0], limit[1]))
        if clipped is None:
            return None
        x, y, w, h = clipped
    return (int(x), int(y), int(w), int(h))


def constrain_square(x0, y0, x1, y1):
    """Make the drag (x0,y0)->(x1,y1) square, keeping the anchor at (x0,y0)."""
    side = max(abs(x1 - x0), abs(y1 - y0))
    return (x0 + side * (1 if x1 >= x0 else -1), y0 + side * (1 if y1 >= y0 else -1))


def constrain_angle(x0, y0, x1, y1, step_degrees=45):
    """Snap the segment direction to multiples of step_degrees."""
    import math

    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length == 0:
        return x1, y1
    step = math.radians(step_degrees)
    angle = round(math.atan2(dy, dx) / step) * step
    return (x0 + length * math.cos(angle), y0 + length * math.sin(angle))


def apply_aspect(rect, ratio, anchor="center"):
    """Largest rect with width/height == ratio inside `rect`."""
    x, y, w, h = rect
    if ratio <= 0 or w <= 0 or h <= 0:
        return rect
    if w / h > ratio:
        nw, nh = h * ratio, h
    else:
        nw, nh = w, w / ratio
    if anchor == "center":
        return (x + (w - nw) / 2, y + (h - nh) / 2, nw, nh)
    return (x, y, nw, nh)


def even(rect):
    """Round a pixel rect down to even width/height (needed by 4:2:0 encoders)."""
    x, y, w, h = (int(v) for v in rect)
    return (x, y, max(2, w - w % 2), max(2, h - h % 2))


def stack_positions(monitor, sizes, corner="bottom-left", margin=20, gap=12):
    """Top-left positions for cards stacked from a monitor corner.

    monitor: (x, y, w, h); sizes: list of (w, h). Newest card is last and sits
    closest to the corner, older cards are pushed away from it.
    """
    mx, my, mw, mh = monitor
    out = [None] * len(sizes)
    offset = margin
    for i in range(len(sizes) - 1, -1, -1):
        w, h = sizes[i]
        if corner.endswith("left"):
            x = mx + margin
        else:
            x = mx + mw - margin - w
        if corner.startswith("bottom"):
            y = my + mh - offset - h
        else:
            y = my + offset
        out[i] = (int(x), int(y))
        offset += h + gap
    return out
