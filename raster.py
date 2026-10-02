"""Pixel-grid drawing primitives. Shapes return lists of (x, y) points;
the caller clips them and writes the colour."""

from collections import deque


def line(x0, y0, x1, y1):
    """Bresenham line, both endpoints included."""
    points = []
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        points.append((x0, y0))
        if x0 == x1 and y0 == y1:
            return points
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _box(x0, y0, x1, y1):
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def rectangle(x0, y0, x1, y1, filled=False):
    left, top, right, bottom = _box(x0, y0, x1, y1)
    return [(x, y) for y in range(top, bottom + 1) for x in range(left, right + 1)
            if filled or x in (left, right) or y in (top, bottom)]


def ellipse(x0, y0, x1, y1, filled=False):
    """Ellipse inscribed in the box. A pixel is inside when its centre is;
    the outline is the inside pixels that touch the outside (4-neighbours)."""
    left, top, right, bottom = _box(x0, y0, x1, y1)
    cx, cy = (left + right + 1) / 2, (top + bottom + 1) / 2
    rx, ry = (right - left + 1) / 2, (bottom - top + 1) / 2

    def inside(x, y):
        return ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2 <= 1.0001

    points = []
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            if inside(x, y) and (filled or not all(
                    inside(x + dx, y + dy)
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))):
                points.append((x, y))
    return points


def flood_fill(pixels, size, x, y, value):
    """4-connected fill of the area sharing the colour at (x, y)."""
    target = pixels[y * size + x]
    if target == value:
        return pixels
    out = list(pixels)
    queue = deque([(x, y)])
    out[y * size + x] = value
    while queue:
        px, py = queue.popleft()
        for nx, ny in ((px + 1, py), (px - 1, py), (px, py + 1), (px, py - 1)):
            if 0 <= nx < size and 0 <= ny < size and out[ny * size + nx] == target:
                out[ny * size + nx] = value
                queue.append((nx, ny))
    return out


def flip_horizontal(pixels, size):
    return [pixels[y * size + (size - 1 - x)] for y in range(size) for x in range(size)]


def flip_vertical(pixels, size):
    return [pixels[(size - 1 - y) * size + x] for y in range(size) for x in range(size)]
