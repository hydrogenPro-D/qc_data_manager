"""Draws the app icon and writes assets/app_icon.png and assets/app_icon.ico.

    venv\\Scripts\\python.exe tools\\generate_icon.py

A glossy teal disc with QC on it, in the app's own accent colours. Drawn here
rather than kept as hand artwork so the palette and the lettering stay editable,
and so a re-run reproduces it exactly.

Everything is drawn at DRAW_SIZE and scaled down, which is what gives the edges
their smoothness: Pillow has no anti-aliased shape drawing, so the resample is
doing that job.

The ICO holds every size Windows asks for, 16px for the window corner up to
256px for Explorer. The PNG is a single size and is what a splash screen would
scale and fade, so both are shipped.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

# Supersampled, then resized down. Four times the largest output is plenty.
DRAW_SIZE = 2048
LABEL = "QC"

# The app's accents, deepened and lightened for the bevel. Teal rather than the
# example's blue: the whole application is built on #14B8A6 / #55BDBD.
GLOW = (20, 184, 166)
BEZEL_LIGHT = (125, 240, 222)
BEZEL_DARK = (8, 74, 68)
FACE_TOP = (52, 211, 190)
FACE_BOTTOM = (10, 106, 96)
INNER_TOP = (38, 196, 176)
INNER_BOTTOM = (9, 92, 84)
RING = (240, 255, 253)

# Fractions of the canvas. The disc is inset so the glow has somewhere to go.
DISC = 0.055
BEZEL_WIDTH = 0.052
RING_RADIUS = 0.355
RING_WIDTH = 0.027
# The lettering, as a fraction of the ring radius. A round frame has less
# room at the corners than its diameter suggests.
LABEL_WIDTH = 1.46

FONTS = ("seguibl.ttf", "ariblk.ttf", "segoeuib.ttf", "arialbd.ttf")


def _gradient(size: int, top: tuple, bottom: tuple) -> Image.Image:
    """A vertical ramp, which is what makes the disc read as lit from above."""
    ramp = Image.new("RGB", (1, size))
    pixels = ramp.load()
    for y in range(size):
        t = y / max(1, size - 1)
        pixels[0, y] = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
    return ramp.resize((size, size), Image.Resampling.BILINEAR)


def _disc(size: int, box: tuple, top: tuple, bottom: tuple) -> Image.Image:
    """One filled circle of gradient, transparent everywhere else."""
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    layer.paste(_gradient(size, top, bottom), (0, 0), mask)
    return layer


def _load_font(pixels: int) -> ImageFont.FreeTypeFont:
    """The heaviest sans available. Weight is what survives the 16px resize."""
    for name in FONTS:
        try:
            return ImageFont.truetype(name, pixels)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_font(draw: ImageDraw.ImageDraw, text: str, width: int) -> tuple:
    """The largest size whose text still sits inside the ring, and its box."""
    size = width
    while size > 8:
        font = _load_font(size)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= width:
            return font, box
        size = int(size * 0.94)
    return _load_font(8), draw.textbbox((0, 0), text, font=_load_font(8))


def draw_icon(size: int = DRAW_SIZE) -> Image.Image:
    """The whole icon, on a transparent square.

    No rounded tile and no background: Windows applies its own shape, and a
    baked-in one fights it.
    """
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    inset = size * DISC
    outer = (inset, inset, size - inset, size - inset)

    # The halo. Drawn, blurred, and laid down first so everything sits on it.
    halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse(outer, fill=GLOW + (215,))
    image.alpha_composite(halo.filter(ImageFilter.GaussianBlur(size * 0.021)))

    # The bevel, then the face inside it, each its own ramp so the rim catches
    # the light and the face falls away from it.
    image.alpha_composite(_disc(size, outer, BEZEL_LIGHT, BEZEL_DARK))
    bevel = size * BEZEL_WIDTH
    face = tuple(v + bevel if i < 2 else v - bevel for i, v in enumerate(outer))
    image.alpha_composite(_disc(size, face, FACE_TOP, FACE_BOTTOM))

    # The glowing ring: a soft pass for the bloom, a crisp one over it.
    radius = size * RING_RADIUS
    centre = size / 2
    ring_box = (centre - radius, centre - radius, centre + radius, centre + radius)
    bloom = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(bloom).ellipse(ring_box, outline=RING + (255,),
                                  width=int(size * RING_WIDTH * 1.6))
    image.alpha_composite(bloom.filter(ImageFilter.GaussianBlur(size * 0.012)))

    inner = tuple(v + size * RING_WIDTH if i < 2 else v - size * RING_WIDTH
                  for i, v in enumerate(ring_box))
    image.alpha_composite(_disc(size, inner, INNER_TOP, INNER_BOTTOM))

    crisp = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(crisp).ellipse(ring_box, outline=RING + (255,),
                                  width=int(size * RING_WIDTH))
    image.alpha_composite(crisp)

    # The gloss: a blurred ellipse across the upper half, clipped to the face so
    # it cannot spill over the rim.
    gloss = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).ellipse(
        (size * 0.17, size * 0.06, size * 0.83, size * 0.50),
        fill=(255, 255, 255, 70))
    gloss = gloss.filter(ImageFilter.GaussianBlur(size * 0.02))
    clip = Image.new("L", (size, size), 0)
    ImageDraw.Draw(clip).ellipse(face, fill=255)
    image.alpha_composite(Image.composite(
        gloss, Image.new("RGBA", (size, size), (0, 0, 0, 0)), clip))

    # The lettering, with a soft drop shadow so it lifts off the face.
    draw = ImageDraw.Draw(image)
    font, box = _fit_font(draw, LABEL, int(radius * LABEL_WIDTH))
    x = centre - (box[2] - box[0]) / 2 - box[0]
    y = centre - (box[3] - box[1]) / 2 - box[1]

    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((x, y + size * 0.012), LABEL,
                                font=font, fill=(0, 40, 36, 150))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(size * 0.008)))
    draw.text((x, y), LABEL, font=font, fill=(255, 255, 255, 255))
    return image


def squared(image: Image.Image) -> Image.Image:
    """Trimmed of its transparent margin and re-centred on a square.

    Without this the artwork floats in empty space, which costs most at 16 and
    32 pixels where every pixel of it counts.
    """
    box = image.getchannel("A").getbbox()
    if box:
        image = image.crop(box)
    side = max(image.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
    return square


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    art = squared(draw_icon())

    art.resize((1024, 1024), Image.Resampling.LANCZOS).save(
        ASSETS / "app_icon_source.png")
    art.resize((512, 512), Image.Resampling.LANCZOS).save(ASSETS / "app_icon.png")
    art.resize((256, 256), Image.Resampling.LANCZOS).save(
        ASSETS / "app_icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
               (128, 128), (256, 256)])
    for name in ("app_icon_source.png", "app_icon.png", "app_icon.ico"):
        path = ASSETS / name
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size // 1024} kB)")


if __name__ == "__main__":
    main()
