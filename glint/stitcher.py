"""Scrolling-capture stitcher (pure Python, no GTK).

Frames are lists of row byte-strings of identical width. As the user scrolls,
each new frame overlaps the previous one; we find the vertical shift by
matching row hashes, keep static header/footer bands (sticky toolbars) once,
and append only the newly revealed rows.
"""

from collections import defaultdict

MIN_MATCH = 0.85
MAX_STATIC_FRACTION = 0.4


def _static_bands(a, b):
    """Leading/trailing counts of rows identical at the same index."""
    h = min(len(a), len(b))
    top = 0
    while top < h and a[top] == b[top]:
        top += 1
    if top == h:
        return h, 0
    bot = 0
    while bot < h - top and a[h - 1 - bot] == b[h - 1 - bot]:
        bot += 1
    limit = int(h * MAX_STATIC_FRACTION)
    return min(top, limit), min(bot, limit)


def find_shift(prev, new, min_overlap=8):
    """Shift s such that prev[s:] ~= new[:len-s] (content moved up by s rows).

    Returns (shift, score) or (None, 0) when there is no convincing overlap.
    A shift of 0 means nothing scrolled.
    """
    m = min(len(prev), len(new))
    if m == 0:
        return None, 0
    prev, new = prev[:m], new[:m]
    positions = defaultdict(list)
    for i, h in enumerate(prev):
        positions[h].append(i)

    # Vote with "distinctive" rows (unique in the new frame and differing
    # from both neighbours) so blank runs don't produce bogus candidates.
    counts = defaultdict(int)
    for h in new:
        counts[h] += 1
    votes = defaultdict(int)
    for a in range(m):
        h = new[a]
        if counts[h] != 1:
            continue
        if (a > 0 and new[a - 1] == h) or (a + 1 < m and new[a + 1] == h):
            continue
        for p in positions.get(h, ()):
            s = p - a
            if s >= 0:
                votes[s] += 1
    candidates = sorted(votes, key=lambda s: (-votes[s], s))[:8]
    if 0 not in candidates:
        candidates.append(0)

    best, best_score = None, 0.0
    floor = max(min_overlap, int(m * 0.1))
    for s in candidates:
        overlap = m - s
        if overlap < floor:
            continue
        matches = sum(1 for i in range(overlap) if prev[s + i] == new[i])
        score = matches / overlap
        if score > best_score + 1e-9 or (abs(score - best_score) < 1e-9 and best is not None and s < best):
            best, best_score = s, score
    if best is None or best_score < MIN_MATCH:
        return None, best_score
    return best, best_score


class Stitcher:
    def __init__(self, max_height=30000):
        self.max_height = max_height
        self.first = None
        self.prev = None          # rows of the last accepted frame
        self.prev_h = None        # their hashes
        self.top = None
        self.bot = None
        self.body = None          # moving-region rows accumulated so far
        self.last = None          # last accepted frame (for the footer)
        self.misses = 0
        self.frames = 0

    @property
    def height(self):
        if self.body is None:
            return len(self.first) if self.first else 0
        return self.top + len(self.body) + self.bot

    def add(self, rows):
        """Feed a frame. Returns True if the output grew."""
        rows = list(rows)
        hashes = [hash(r) for r in rows]
        self.frames += 1
        if self.first is None:
            self.first = self.prev = self.last = rows
            self.prev_h = hashes
            return False
        if len(rows) != len(self.prev):
            return False

        if self.top is None:
            top, bot = _static_bands(self.prev_h, hashes)
            if top >= len(rows):
                return False  # identical frame
            top_, bot_ = top, bot
        else:
            top_, bot_ = self.top, self.bot

        h = len(rows)
        P = self.prev_h[top_:h - bot_]
        N = hashes[top_:h - bot_]
        if len(P) < 4:
            return False
        shift, _score = find_shift(P, N)
        if shift is None:
            self.misses += 1
            if self.misses < 3 or self.body is None:
                return False
            # Scrolled further than a viewport between samples: accept a gap
            # rather than stalling forever.
            shift = len(N)
        self.misses = 0
        if shift == 0:
            return False

        if self.top is None:
            self.top, self.bot = top_, bot_
            self.body = self.prev[top_:h - bot_]
        m = len(N)
        added = rows[top_ + m - shift: top_ + m]
        room = self.max_height - self.height
        if room <= 0:
            return False
        self.body.extend(added[:room])
        self.prev, self.prev_h, self.last = rows, hashes, rows
        return True

    def result_rows(self):
        if self.first is None:
            return []
        if self.body is None:
            return list(self.first)
        h = len(self.last)
        return list(self.first[:self.top]) + self.body + list(self.last[h - self.bot:] if self.bot else [])
