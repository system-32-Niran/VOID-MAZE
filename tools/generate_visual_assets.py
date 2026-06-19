from pathlib import Path
import math
import random

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "assets"
ASSET_DIR.mkdir(exist_ok=True)

CELL = 128
TRANSPARENT = (0, 0, 0, 0)


def glow_layer(size, center, radius, color):
    layer = Image.new("RGBA", size, TRANSPARENT)
    draw = ImageDraw.Draw(layer)
    draw.ellipse(
        (
            center[0] - radius,
            center[1] - radius,
            center[0] + radius,
            center[1] + radius,
        ),
        fill=color,
    )
    return layer.filter(ImageFilter.GaussianBlur(max(2, radius // 3)))


def polygon(draw, points, fill, outline=(5, 8, 14, 255), width=4):
    draw.polygon(points, fill=fill)
    draw.line(points + [points[0]], fill=outline, width=width, joint="curve")


def draw_hunter(image, ox=0, oy=0):
    draw = ImageDraw.Draw(image)
    cx, cy = ox + 64, oy + 64
    image.alpha_composite(glow_layer(image.size, (cx, cy), 51, (230, 30, 42, 32)))

    polygon(
        draw,
        [(cx - 51, cy), (cx - 15, cy - 42), (cx + 43, cy - 28),
         (cx + 52, cy), (cx + 43, cy + 28), (cx - 15, cy + 42)],
        (16, 18, 24, 255),
        width=6,
    )
    polygon(
        draw,
        [(cx - 18, cy - 33), (cx + 33, cy - 24),
         (cx + 42, cy), (cx + 33, cy + 24), (cx - 18, cy + 33)],
        (50, 12, 18, 255),
        width=4,
    )
    draw.ellipse(
        (cx + 5, cy - 31, cx + 58, cy + 31),
        fill=(202, 34, 43, 255),
        outline=(6, 7, 10, 255),
        width=6,
    )
    draw.polygon(
        [(cx + 31, cy - 24), (cx + 58, cy), (cx + 31, cy + 24)],
        fill=(84, 9, 14, 255),
    )
    draw.line(
        (cx + 27, cy - 10, cx + 49, cy - 6),
        fill=(255, 235, 218, 255),
        width=5,
    )
    draw.line(
        (cx + 27, cy + 10, cx + 49, cy + 6),
        fill=(255, 235, 218, 255),
        width=5,
    )
    draw.ellipse(
        (cx - 8, cy - 48, cx + 18, cy - 24),
        fill=(34, 37, 43, 255),
        outline=(5, 7, 10, 255),
        width=4,
    )
    draw.ellipse(
        (cx - 8, cy + 24, cx + 18, cy + 48),
        fill=(34, 37, 43, 255),
        outline=(5, 7, 10, 255),
        width=4,
    )


def draw_runner(image, ox=0, oy=0):
    draw = ImageDraw.Draw(image)
    cx, cy = ox + 64, oy + 64
    image.alpha_composite(glow_layer(image.size, (cx, cy), 38, (35, 224, 211, 30)))

    draw.ellipse(
        (cx - 37, cy - 30, cx + 34, cy + 30),
        fill=(23, 161, 164, 255),
        outline=(5, 10, 14, 255),
        width=6,
    )
    draw.ellipse(
        (cx + 2, cy - 25, cx + 46, cy + 25),
        fill=(225, 237, 233, 255),
        outline=(5, 10, 14, 255),
        width=5,
    )
    draw.line(
        (cx + 18, cy - 18, cx + 38, cy - 12),
        fill=(47, 219, 205, 255),
        width=5,
    )
    draw.line(
        (cx + 18, cy + 18, cx + 38, cy + 12),
        fill=(47, 219, 205, 255),
        width=5,
    )
    draw.rounded_rectangle(
        (cx - 43, cy - 22, cx - 16, cy + 22),
        radius=7,
        fill=(229, 181, 45, 255),
        outline=(5, 10, 14, 255),
        width=5,
    )
    draw.line(
        (cx - 17, cy - 24, cx + 2, cy + 24),
        fill=(245, 211, 103, 255),
        width=5,
    )
    for side in (-1, 1):
        draw.ellipse(
            (cx - 3, cy + side * 31 - 9, cx + 12, cy + side * 31 + 9),
            fill=(20, 75, 83, 255),
            outline=(5, 10, 14, 255),
            width=3,
        )


def draw_cleaver(draw, box):
    x, y, w, h = box
    cy = y + h // 2
    draw.line((x + 18, cy + 19, x + 58, cy + 4),
              fill=(58, 35, 27, 255), width=13)
    draw.line((x + 18, cy + 19, x + 58, cy + 4),
              fill=(132, 77, 43, 255), width=6)
    polygon(draw, [(x + 53, cy - 8), (x + 104, cy - 22),
                   (x + 111, cy + 8), (x + 67, cy + 20)],
            (173, 183, 181, 255), width=3)
    draw.line((x + 64, cy + 14, x + 108, cy + 3),
              fill=(224, 234, 228, 255), width=3)
    draw.line((x + 78, cy - 15, x + 90, cy + 8),
              fill=(111, 35, 38, 255), width=5)


def draw_spear(draw, box):
    x, y, w, h = box
    draw.line((x + 15, y + 91, x + 102, y + 34),
              fill=(57, 37, 29, 255), width=10)
    draw.line((x + 15, y + 91, x + 102, y + 34),
              fill=(151, 101, 55, 255), width=4)
    polygon(draw, [(x + 94, y + 38), (x + 116, y + 13),
                   (x + 108, y + 47)], (214, 220, 216, 255), width=3)
    draw.line((x + 102, y + 34, x + 113, y + 23),
              fill=(237, 64, 59, 255), width=5)


def draw_bomb(draw, box):
    x, y, w, h = box
    cx, cy = x + 64, y + 67
    draw.ellipse((cx - 31, cy - 28, cx + 31, cy + 28),
                 fill=(229, 184, 34, 255), outline=(9, 12, 17, 255), width=5)
    draw.rectangle((cx - 25, cy - 7, cx + 25, cy + 8),
                   fill=(50, 53, 56, 255))
    draw.rounded_rectangle((cx - 12, cy - 40, cx + 14, cy - 24),
                           radius=4, fill=(91, 96, 97, 255),
                           outline=(9, 12, 17, 255), width=3)
    draw.line((cx + 10, cy - 39, cx + 27, cy - 52),
              fill=(193, 104, 37, 255), width=4)
    draw.ellipse((cx + 23, cy - 57, cx + 32, cy - 48),
                 fill=(255, 236, 114, 255))


def draw_pearl(image, box):
    draw = ImageDraw.Draw(image)
    x, y, w, h = box
    cx, cy = x + 64, y + 64
    image.alpha_composite(glow_layer(image.size, (cx, cy), 42, (45, 238, 226, 95)))
    draw.ellipse((cx - 28, cy - 28, cx + 28, cy + 28),
                 fill=(34, 177, 185, 255), outline=(8, 16, 24, 255), width=5)
    draw.ellipse((cx - 20, cy - 20, cx + 20, cy + 20),
                 fill=(58, 223, 211, 255), outline=(158, 255, 242, 255), width=3)
    draw.arc((cx - 38, cy - 17, cx + 38, cy + 17),
             190, 535, fill=(204, 255, 250, 230), width=4)
    draw.ellipse((cx - 10, cy - 15, cx + 1, cy - 4),
                 fill=(235, 255, 253, 255))


def draw_trap(draw, box):
    x, y, w, h = box
    cx, cy = x + 64, y + 66
    draw.arc((cx - 39, cy - 30, cx + 39, cy + 31),
             190, 350, fill=(127, 75, 40, 255), width=12)
    draw.arc((cx - 39, cy - 31, cx + 39, cy + 30),
             10, 170, fill=(127, 75, 40, 255), width=12)
    for side in (-1, 1):
        for i in range(4):
            px = cx + side * (13 + i * 8)
            points = [(px - 5, cy - 4), (px, cy + 11), (px + 5, cy - 4)]
            draw.polygon(points, fill=(203, 210, 199, 255))
    draw.ellipse((cx - 15, cy - 15, cx + 15, cy + 15),
                 fill=(62, 64, 62, 255), outline=(13, 14, 14, 255), width=4)
    draw.line((cx - 48, cy + 22, cx - 27, cy + 12),
              fill=(88, 52, 31, 255), width=7)


def draw_toolkit(draw, box):
    x, y, w, h = box
    draw.rounded_rectangle((x + 22, y + 38, x + 107, y + 96),
                           radius=10, fill=(39, 75, 91, 255),
                           outline=(7, 12, 17, 255), width=5)
    draw.rounded_rectangle((x + 45, y + 24, x + 84, y + 49),
                           radius=8, fill=(30, 44, 53, 255),
                           outline=(7, 12, 17, 255), width=4)
    draw.rectangle((x + 27, y + 61, x + 102, y + 73),
                   fill=(211, 166, 42, 255))
    draw.line((x + 43, y + 87, x + 82, y + 47),
              fill=(192, 204, 201, 255), width=8)
    draw.ellipse((x + 74, y + 37, x + 94, y + 57),
                 outline=(192, 204, 201, 255), width=7)
    draw.line((x + 38, y + 93, x + 31, y + 100),
              fill=(192, 204, 201, 255), width=8)


def create_character_atlas():
    image = Image.new("RGBA", (CELL * 2, CELL), TRANSPARENT)
    draw_hunter(image, 0, 0)
    draw_runner(image, CELL, 0)
    image.save(ASSET_DIR / "character_atlas.png")


def create_item_atlas():
    image = Image.new("RGBA", (CELL * 3, CELL * 2), TRANSPARENT)
    draw = ImageDraw.Draw(image)
    draw_cleaver(draw, (0, 0, CELL, CELL))
    draw_spear(draw, (CELL, 0, CELL, CELL))
    draw_bomb(draw, (CELL * 2, 0, CELL, CELL))
    draw_pearl(image, (0, CELL, CELL, CELL))
    draw_trap(draw, (CELL, CELL, CELL, CELL))
    draw_toolkit(draw, (CELL * 2, CELL, CELL, CELL))
    image.save(ASSET_DIR / "item_atlas.png")


def icon_tile(image, col, row, accent):
    x, y = col * CELL, row * CELL
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((x + 5, y + 5, x + 123, y + 123),
                           radius=16, fill=(15, 21, 30, 255),
                           outline=(4, 7, 12, 255), width=5)
    draw.rounded_rectangle((x + 11, y + 11, x + 117, y + 117),
                           radius=12, outline=(*accent, 150), width=3)
    image.alpha_composite(
        glow_layer(image.size, (x + 64, y + 64), 42, (*accent, 28))
    )
    return draw, x, y


def create_skill_icons():
    image = Image.new("RGBA", (CELL * 4, CELL * 2), (8, 12, 18, 255))

    draw, x, y = icon_tile(image, 0, 0, (72, 224, 117))
    for offset in (0, 20):
        polygon(draw, [(x + 30 + offset, y + 76), (x + 49 + offset, y + 45),
                       (x + 70 + offset, y + 57), (x + 54 + offset, y + 86)],
                (70, 215, 112, 255), width=3)
    draw.line((x + 20, y + 93, x + 94, y + 93),
              fill=(132, 255, 165, 255), width=5)

    draw, x, y = icon_tile(image, 1, 0, (111, 216, 245))
    draw.pieslice((x + 31, y + 27, x + 97, y + 105),
                  180, 360, fill=(103, 201, 229, 130))
    draw.ellipse((x + 45, y + 40, x + 83, y + 79),
                 fill=(182, 238, 249, 165))
    for i in range(4):
        draw.line((x + 29 + i * 19, y + 35, x + 19 + i * 19, y + 98),
                  fill=(178, 238, 250, 85), width=4)

    draw, x, y = icon_tile(image, 2, 0, (187, 92, 245))
    draw.rectangle((x + 56, y + 23, x + 79, y + 105),
                   fill=(88, 45, 122, 255), outline=(211, 146, 255, 255), width=3)
    draw.ellipse((x + 31, y + 42, x + 67, y + 78),
                 fill=(198, 128, 247, 210))
    draw.line((x + 41, y + 85, x + 88, y + 42),
              fill=(232, 191, 255, 255), width=7)

    draw, x, y = icon_tile(image, 3, 0, (244, 73, 67))
    draw_spear(draw, (x, y, CELL, CELL))

    draw, x, y = icon_tile(image, 0, 1, (245, 211, 65))
    draw_bomb(draw, (x, y, CELL, CELL))
    for angle in range(0, 360, 45):
        a = math.radians(angle)
        draw.line((x + 64 + math.cos(a) * 39, y + 64 + math.sin(a) * 39,
                   x + 64 + math.cos(a) * 51, y + 64 + math.sin(a) * 51),
                  fill=(255, 239, 119, 255), width=4)

    draw, x, y = icon_tile(image, 1, 1, (53, 222, 209))
    draw_pearl(image, (x, y, CELL, CELL))

    draw, x, y = icon_tile(image, 2, 1, (238, 126, 43))
    draw_trap(draw, (x, y, CELL, CELL))

    icon_tile(image, 3, 1, (58, 66, 77))
    image.save(ASSET_DIR / "skill_icons.png")


def create_environment_atlas():
    random.seed(7)
    image = Image.new("RGB", (CELL * 2, CELL), (8, 13, 19))
    draw = ImageDraw.Draw(image)

    for y in range(CELL):
        shade = 14 + int(10 * y / CELL)
        draw.line((0, y, CELL - 1, y), fill=(shade - 3, shade + 2, shade + 7))
    for x in range(0, CELL, 32):
        draw.line((x, 0, x, CELL), fill=(21, 31, 39), width=2)
    for y in range(0, CELL, 32):
        draw.line((0, y, CELL, y), fill=(19, 28, 36), width=2)
    for _ in range(45):
        x, y = random.randrange(CELL), random.randrange(CELL)
        shade = random.randrange(24, 44)
        draw.point((x, y), fill=(shade - 6, shade, shade + 5))
    draw.line((8, 102, 43, 92), fill=(52, 59, 61), width=2)
    draw.line((91, 18, 111, 26), fill=(45, 53, 57), width=2)

    ox = CELL
    for y in range(CELL):
        shade = 18 + int(12 * y / CELL)
        draw.line((ox, y, ox + CELL - 1, y),
                  fill=(5, shade + 10, shade + 18))
    draw.rounded_rectangle((ox + 7, 7, ox + 120, 120),
                           radius=8, fill=(7, 35, 50),
                           outline=(10, 91, 119), width=4)
    draw.rounded_rectangle((ox + 17, 17, ox + 110, 110),
                           radius=5, fill=(8, 27, 39),
                           outline=(8, 59, 79), width=3)
    for px, py in ((25, 25), (103, 25), (25, 103), (103, 103)):
        draw.ellipse((ox + px - 4, py - 4, ox + px + 4, py + 4),
                     fill=(78, 108, 119), outline=(3, 12, 17))
    draw.line((ox + 20, 86, ox + 105, 42), fill=(10, 54, 70), width=5)
    draw.line((ox + 31, 102, ox + 114, 58), fill=(6, 21, 30), width=3)
    image.save(ASSET_DIR / "environment_atlas.png")


if __name__ == "__main__":
    create_character_atlas()
    create_item_atlas()
    create_skill_icons()
    create_environment_atlas()
    print("Generated visual assets.")
