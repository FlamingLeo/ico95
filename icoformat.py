"""Read and write classic (Windows 95 era) .ico files.

File layout (all little-endian):

    ICONDIR        reserved=0, type=1, count
    ICONDIRENTRY   one per image: width, height, colorCount, reserved,
                   planes, bitCount, bytesInRes, imageOffset
    image data     one per entry: BITMAPINFOHEADER (biHeight = 2 * height,
                   because it covers both masks), colour table (RGBQUADs),
                   XOR bitmap (the colours), AND mask (1 bpp transparency).
                   Both bitmaps are stored bottom-up, rows padded to 32 bits.

Pixel values of an IconImage:

    0..n-1       palette index                    AND=0, XOR=index
    TRANSPARENT  the screen shows through         AND=1, XOR=black
    INVERT       the screen colour is inverted    AND=1, XOR=white
"""

import struct
from dataclasses import dataclass

TRANSPARENT = -1
INVERT = -2

SIZES = (16, 32, 48)
DEPTHS = (1, 4, 8)

# Sizes / depths Windows 95 itself uses. 48x48 icons only appear with the
# "large icons" option of Windows 98 / Plus! 98 and later.
WIN95_SIZES = (16, 32)

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)

PALETTE_MONO = [BLACK, WHITE]

# The standard Windows 16-colour (VGA) palette, in the order icon files use.
PALETTE_VGA = [
    (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
    (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
    (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
    (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
]


def _build_256_palette():
    # The 20 static colours Windows reserves in 256-colour mode sit at
    # indices 0-9 and 246-255; the middle is a 6x6x6 colour cube plus grays.
    first = [
        (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0), (0, 0, 128),
        (128, 0, 128), (0, 128, 128), (192, 192, 192), (192, 220, 192),
        (166, 202, 240),
    ]
    last = [
        (255, 251, 240), (160, 160, 164), (128, 128, 128), (255, 0, 0),
        (0, 255, 0), (255, 255, 0), (0, 0, 255), (255, 0, 255),
        (0, 255, 255), (255, 255, 255),
    ]
    steps = (0, 51, 102, 153, 204, 255)
    cube = [(r, g, b) for r in steps for g in steps for b in steps]
    grays = [(v, v, v) for v in (round(255 * i / 21) for i in range(1, 21))]
    middle = cube + grays
    assert len(middle) == 236
    return first + middle + last


PALETTE_256 = _build_256_palette()

DEPTH_NAMES = {1: "Monochrome", 4: "16 colors", 8: "256 colors"}


class IcoError(Exception):
    pass


def default_palette(bpp):
    return list({1: PALETTE_MONO, 4: PALETTE_VGA, 8: PALETTE_256}[bpp])


def palette_is_locked(bpp):
    """Monochrome and 16-colour icons always use the fixed system palette:
    Windows 95 in 16-colour mode cannot show anything else."""
    return bpp < 8


@dataclass
class IconImage:
    size: int
    bpp: int
    palette: list   # 2**bpp (r, g, b) tuples
    pixels: list    # size*size values, row-major, top row first

    @classmethod
    def blank(cls, size, bpp, fill=TRANSPARENT):
        return cls(size, bpp, default_palette(bpp), [fill] * (size * size))

    def copy(self):
        return IconImage(self.size, self.bpp, list(self.palette), list(self.pixels))

    @property
    def label(self):
        return f"{self.size}×{self.size}, {DEPTH_NAMES[self.bpp]}"

    @property
    def key(self):
        return (self.size, self.bpp)

    def converted(self, size, bpp, dither=False):
        """A copy scaled (nearest neighbour) to `size` and remapped to `bpp`.
        Transparent and inverted pixels are kept; with `dither`, colour
        reduction uses Floyd-Steinberg error diffusion."""
        palette = list(self.palette) if bpp == self.bpp else default_palette(bpp)
        scaled = [self.pixels[(y * self.size // size) * self.size + x * self.size // size]
                  for y in range(size) for x in range(size)]
        if palette == self.palette:
            return IconImage(size, bpp, palette, scaled)
        rgba = [(0, 0, 0, 0) if v < 0 else self.palette[v] + (255,) for v in scaled]
        quantized = quantize(rgba, size, palette, dither)
        return IconImage(size, bpp, palette,
                         [v if v < 0 else q for v, q in zip(scaled, quantized)])


def sort_key(img):
    # 32x32 16-colour first: Windows 3.x tools and some old resource tools
    # only look at the first entry, and that is the one Windows 95 prefers.
    size_order = {32: 0, 16: 1, 48: 2}
    depth_order = {4: 0, 8: 1, 1: 2}
    return (size_order.get(img.size, 9), depth_order.get(img.bpp, 9))


def is_blank(img):
    return all(v == TRANSPARENT for v in img.pixels)


def missing_for_win95(images):
    """(size, bpp) keys of the 16-colour images Windows 95 wants but the
    icon lacks or has only as a blank (fully transparent) image: 32x32 and
    16x16, plus one per size that only has 256 colours. Empty when there is
    nothing drawn to convert from."""
    drawn = {img.key for img in images if not is_blank(img)}
    if not drawn:
        return []
    wanted = [(32, 4), (16, 4)] + [(s, 4) for s in SIZES if (s, 8) in drawn]
    return [k for k in dict.fromkeys(wanted) if k not in drawn]


def best_source(images, size, bpp):
    """The image that converts best to (size, bpp): same size first, then
    shrinking rather than enlarging, the closest size, the same colour depth,
    and finally the most colours."""
    return min(images, key=lambda s: (s.size != size, s.size < size,
                                      abs(s.size - size), s.bpp != bpp, -s.bpp))


def fill_missing(images, dither=False):
    """Complete the icon for Windows 95. Returns (all images, new images):
    missing images are added and blank ones replaced, each converted from the
    best drawn original (never from another newly generated image)."""
    sources = [img for img in images if not is_blank(img)]
    new = [best_source(sources, size, bpp).converted(size, bpp, dither)
           for size, bpp in missing_for_win95(images)]
    new_keys = {img.key for img in new}
    kept = [img for img in images if img.key not in new_keys]
    return sorted(kept + new, key=sort_key), new


# --- colour matching ------------------------------------------------------

def _distance(a, b):
    dr, dg, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return 2 * dr * dr + 4 * dg * dg + 3 * db * db


def nearest_index(palette, rgb):
    return min(range(len(palette)), key=lambda i: _distance(palette[i], rgb))


def quantize(rgba, width, palette, dither=False):
    """Map (r, g, b, a) pixels to palette indices; alpha < 128 becomes
    TRANSPARENT. With `dither`, Floyd-Steinberg error diffusion is used."""
    out = []
    cache = {}
    if not dither:
        for r, g, b, a in rgba:
            if a < 128:
                out.append(TRANSPARENT)
            else:
                rgb = (r, g, b)
                if rgb not in cache:
                    cache[rgb] = nearest_index(palette, rgb)
                out.append(cache[rgb])
        return out

    work = [[float(r), float(g), float(b)] for r, g, b, _ in rgba]
    height = len(rgba) // width
    for y in range(height):
        for x in range(width):
            i = y * width + x
            if rgba[i][3] < 128:
                out.append(TRANSPARENT)
                continue
            rgb = tuple(max(0, min(255, round(c))) for c in work[i])
            if rgb not in cache:
                cache[rgb] = nearest_index(palette, rgb)
            idx = cache[rgb]
            out.append(idx)
            err = [work[i][c] - palette[idx][c] for c in range(3)]
            for dx, dy, w in ((1, 0, 7), (-1, 1, 3), (0, 1, 5), (1, 1, 1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and ny < height:
                    j = ny * width + nx
                    for c in range(3):
                        work[j][c] += err[c] * w / 16
    return out


# --- validation -----------------------------------------------------------

def validate(images):
    """Return (errors, warnings): errors make the file invalid, warnings
    concern how well it works on Windows 95."""
    errors, warnings = [], []
    if not images:
        errors.append("The icon contains no images.")
    seen = set()
    for img in images:
        name = img.label
        if img.key in seen:
            errors.append(f"There is more than one {name} image.")
        seen.add(img.key)
        if img.size not in SIZES:
            errors.append(f"{img.size}×{img.size} is not a supported icon size.")
        if img.bpp not in DEPTHS:
            errors.append(f"{img.bpp} bits per pixel is not supported.")
            continue
        if len(img.palette) != 2 ** img.bpp:
            errors.append(f"{name}: palette must have {2 ** img.bpp} entries.")
        if len(img.pixels) != img.size * img.size:
            errors.append(f"{name}: wrong number of pixels.")
        if any(not (INVERT <= v < len(img.palette)) for v in img.pixels):
            errors.append(f"{name}: pixel refers to a color outside the palette.")
        if TRANSPARENT in img.pixels and BLACK not in img.palette:
            errors.append(f"{name}: transparent pixels need pure black in the palette.")
        if INVERT in img.pixels and WHITE not in img.palette:
            errors.append(f"{name}: inverted pixels need pure white in the palette.")
        if is_blank(img):
            warnings.append(f"{name}: empty (fully transparent), so the icon "
                            "is invisible wherever Windows uses this image.")
        if img.size not in WIN95_SIZES:
            warnings.append(f"{name}: Windows 95 does not use 48×48 icons "
                            "(Windows 98 and later do).")
        if img.bpp == 8:
            warnings.append(f"{name}: Windows 95 only shows 256-color icons with "
                            "\"Show icons using all possible colors\" (Plus!).")
    keys = {img.key for img in images}
    if images and (32, 4) not in keys:
        warnings.append("No 32×32, 16 colors image: this is the main icon "
                        "Windows 95 uses.")
    if images and (16, 4) not in keys:
        warnings.append("No 16×16, 16 colors image: Windows 95 will shrink "
                        "the 32×32 one for small icons, which looks rough.")
    for size in {img.size for img in images}:
        if (size, 8) in keys and (size, 4) not in keys:
            warnings.append(f"{size}×{size}: 256-color image without a "
                            "16-color version for normal Windows 95 displays.")
    return errors, list(dict.fromkeys(warnings))


# --- writing --------------------------------------------------------------

def _stride(width, bpp):
    return (width * bpp + 31) // 32 * 4


def encode_image(img):
    """The DIB (header, colour table, XOR bitmap, AND mask) for one image."""
    size, bpp = img.size, img.bpp
    black = img.palette.index(BLACK) if BLACK in img.palette else None
    white = img.palette.index(WHITE) if WHITE in img.palette else None
    xor_stride, and_stride = _stride(size, bpp), _stride(size, 1)
    xor = bytearray(xor_stride * size)
    mask = bytearray(and_stride * size)
    per_byte = 8 // bpp

    for y in range(size):
        row = size - 1 - y                      # bottom-up
        for x in range(size):
            v = img.pixels[y * size + x]
            if v < 0:
                mask[row * and_stride + x // 8] |= 0x80 >> (x % 8)
                v = black if v == TRANSPARENT else white
                if v is None:
                    raise IcoError(f"{img.label}: palette lacks black/white "
                                   "needed for transparent/inverted pixels.")
            shift = 8 - bpp * (x % per_byte + 1)
            xor[row * xor_stride + x // per_byte] |= v << shift

    header = struct.pack(
        "<IiiHHIIiiII",
        40,                     # biSize
        size,                   # biWidth
        size * 2,               # biHeight: XOR + AND
        1,                      # biPlanes
        bpp,                    # biBitCount
        0,                      # biCompression = BI_RGB
        len(xor) + len(mask),   # biSizeImage
        0, 0,                   # biXPelsPerMeter, biYPelsPerMeter
        0,                      # biClrUsed: 0 = all 2**bpp colours
        0,                      # biClrImportant
    )
    table = b"".join(struct.pack("BBBB", b, g, r, 0) for r, g, b in img.palette)
    return header + table + bytes(xor) + bytes(mask)


def write_ico(images):
    errors, _ = validate(images)
    if errors:
        raise IcoError("\n".join(errors))
    images = sorted(images, key=sort_key)
    blobs = [encode_image(img) for img in images]
    out = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for img, blob in zip(images, blobs):
        out += struct.pack(
            "<BBBBHHII",
            img.size, img.size,
            2 ** img.bpp if img.bpp < 8 else 0,     # colour count, 0 if >= 256
            0,                                      # reserved
            1,                                      # planes
            img.bpp,                                # bit count
            len(blob),
            offset,
        )
        offset += len(blob)
    for blob in blobs:
        out += blob
    return bytes(out)


def save_ico(path, images):
    data = write_ico(images)
    with open(path, "wb") as f:
        f.write(data)


# --- reading --------------------------------------------------------------

def _decode_entry(blob):
    """Return (size, bpp, palette, pixels) for a 1/4/8 bpp DIB entry, with
    AND-masked pixels turned into TRANSPARENT / INVERT."""
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        raise IcoError("PNG-compressed image (Windows Vista format)")
    if len(blob) < 40:
        raise IcoError("truncated image header")
    (hsize, size, height2, _planes, bpp, compression,
     _, _, _, clr_used, _) = struct.unpack_from("<IiiHHIIiiII", blob)
    if hsize != 40 or compression != 0:
        raise IcoError("unsupported bitmap format")
    if bpp not in DEPTHS:
        raise IcoError(f"{bpp}-bit image")
    if size not in SIZES or height2 != 2 * size:
        raise IcoError(f"{size}\u00d7{height2 // 2} is not a Windows 95 icon size")

    count = clr_used or 2 ** bpp
    palette = [(r, g, b) for b, g, r, _ in struct.iter_unpack("4B", blob[40:40 + 4 * count])]
    palette = (palette + [BLACK] * 2 ** bpp)[:2 ** bpp]
    xor_stride, and_stride = _stride(size, bpp), _stride(size, 1)
    xor_start = 40 + 4 * count
    and_start = xor_start + xor_stride * size
    if len(blob) < and_start + and_stride * size:
        raise IcoError("truncated bitmap data")

    per_byte = 8 // bpp
    pixels = []
    for y in range(size):
        xrow = xor_start + (size - 1 - y) * xor_stride
        arow = and_start + (size - 1 - y) * and_stride
        for x in range(size):
            byte = blob[xrow + x // per_byte]
            v = (byte >> (8 - bpp * (x % per_byte + 1))) & ((1 << bpp) - 1)
            if blob[arow + x // 8] & (0x80 >> (x % 8)):
                v = TRANSPARENT if sum(palette[v]) < 384 else INVERT
            pixels.append(v)
    return size, bpp, palette, pixels


def read_ico(data):
    """Parse an .ico file. Returns (images, notes); notes describe entries
    that were adjusted or skipped."""
    if len(data) < 6:
        raise IcoError("File is too short to be an icon.")
    reserved, kind, count = struct.unpack_from("<HHH", data)
    if reserved != 0 or kind not in (1, 2):
        raise IcoError("Not an icon file.")
    if kind == 2:
        raise IcoError("This is a cursor (.cur) file, not an icon.")
    if len(data) < 6 + 16 * count:
        raise IcoError("Icon directory is truncated.")

    images, notes = {}, []
    for i in range(count):
        nbytes, offset = struct.unpack_from("<II", data, 6 + 16 * i + 8)
        try:
            img = IconImage(*_decode_entry(data[offset:offset + nbytes]))
        except (IcoError, struct.error) as e:
            notes.append(f"Entry {i + 1} skipped: {e}.")
            continue
        if palette_is_locked(img.bpp) and img.palette != default_palette(img.bpp):
            fixed = default_palette(img.bpp)
            remap = [nearest_index(fixed, rgb) for rgb in img.palette]
            img = IconImage(img.size, img.bpp, fixed,
                            [v if v < 0 else remap[v] for v in img.pixels])
            notes.append(f"{img.label}: palette mapped to the standard Windows palette.")
        if img.key in images:
            notes.append(f"Duplicate {img.label} image dropped.")
            continue
        images[img.key] = img
    if not images:
        raise IcoError("The file contains no Windows 95 images (1, 4 or 8-bit). For a "
                       "newer icon, use File \u2192 Import Picture instead.\n\n"
                       + "\n".join(notes))
    return sorted(images.values(), key=sort_key), notes


def load_ico(path):
    with open(path, "rb") as f:
        return read_ico(f.read())
