"""Small, one-bit friendly corner cards; no network calls during rendering."""
import math
from datetime import datetime
from functools import lru_cache
from PIL import Image, ImageDraw

CORNER_SIZE = (128, 108)
CORNER_POSITION = (262, 187)


@lru_cache(maxsize=160)
def asset(path, size, pixel=False):
    with Image.open(path) as source:
        rgba = source.convert("RGBA")
        if pixel and rgba.getbbox():
            rgba = rgba.crop(rgba.getbbox())
        background = Image.new("RGBA", rgba.size, "white")
        background.alpha_composite(rgba)
        image = background.convert("L")
    image.thumbnail((size, size), Image.Resampling.NEAREST if pixel else Image.Resampling.LANCZOS)
    if pixel:
        scale = max(1, size // max(image.size))
        if scale > 1:
            image = image.resize((image.width*scale, image.height*scale), Image.Resampling.NEAREST)
    if pixel:
        return image.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")
    return image.point(lambda p: 0 if p < 180 else 255)


def basketball_icon(size=18):
    image = Image.new("L", (72, 72), 255)
    d = ImageDraw.Draw(image)
    d.ellipse((5, 5, 66, 66), outline=0, width=5)
    d.line((6, 36, 66, 36), fill=0, width=4)
    d.line((36, 6, 36, 66), fill=0, width=4)
    d.arc((-17, 6, 35, 66), -80, 80, fill=0, width=4)
    d.arc((36, 6, 88, 66), 100, 260, fill=0, width=4)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def horizon_icon(size=28):
    image = Image.new("L", (112, 112), 255)
    d = ImageDraw.Draw(image)
    d.arc((28, 39, 84, 95), 180, 360, fill=0, width=5)
    d.line((9, 68, 103, 68), fill=0, width=5)
    for i in range(5):
        angle = math.pi + i*math.pi/4
        d.line([(56+38*math.cos(angle), 67+38*math.sin(angle)),
                (56+47*math.cos(angle), 67+47*math.sin(angle))], fill=0, width=4)
    d.line((22, 85, 90, 85), fill=0, width=4)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def wrap(draw, text, face, width, limit=2):
    from dashboard import fit
    words = str(text).split()
    lines = []
    while words and len(lines) < limit:
        if len(lines) == limit-1:
            lines.append(fit(draw, " ".join(words), face, width))
            break
        line = words.pop(0)
        while words and draw.textlength(line+" "+words[0], font=face) <= width:
            line += " "+words.pop(0)
        lines.append(fit(draw, line, face, width))
    return lines


def render_corner(card, now):
    from dashboard import fit, font
    image = Image.new("L", CORNER_SIZE, 255)
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((0, 0, 127, 107), radius=5, outline=0)
    d.line((6, 22, 121, 22), fill=0)
    kind = card["kind"]
    if kind == "uk":
        try:
            logo = asset(card["logo"], 21)
            image.paste(logo, (4, 0))
        except (OSError, KeyError):
            d.text((5, 2), "UK", font=font(15, True), fill=0)
        d.text((29, 1), "UK MEN'S", font=font(8, True), fill=0)
        d.text((29, 11), "BASKETBALL", font=font(8, True), fill=0)
        image.paste(basketball_icon(15), (109, 3))
        start = datetime.fromisoformat(card["start"]).astimezone(now.tzinfo)
        day = "Today" if start.date() == now.date() else "%s %d/%d" % (start.strftime("%a"), start.month, start.day)
        clock = "Time TBD" if card.get("time_tbd") else start.strftime("%I:%M %p").lstrip("0")
        d.text((6, 25), day, font=font(10, True), fill=0)
        d.text((6, 37), clock, font=font(10, True), fill=0)
        if card.get("cached"):
            d.text((121-d.textlength("cached", font=font(8, True)), 38), "cached", font=font(8, True), fill=0)
        opponent = ("at " if card.get("site") == "Away" else "vs ")+card["opponent"]
        for i, line in enumerate(wrap(d, opponent, font(10, True), 116)):
            d.text((6, 49+i*11), line, font=font(10, True), fill=0)
        site = " · ".join(x for x in (card.get("site"), card.get("venue"), card.get("city")) if x)
        for i, line in enumerate(wrap(d, site, font(9), 116)):
            d.text((6, 74+i*10), line, font=font(9), fill=0)
        if card.get("tv"):
            d.text((6, 96), fit(d, "TV: "+card["tv"], font(9, True), 116), font=font(9, True), fill=0)
    elif kind == "stats":
        d.text((6, 5), "LAST.FM", font=font(10, True), fill=0)
        if card.get("cached"):
            d.text((86, 7), "cached", font=font(8), fill=0)
        d.text((6, 26), "Scrobbles Today", font=font(10, True), fill=0)
        count = str(card["tracks_today"])
        face = font(23, True) if len(count) <= 4 else font(18, True)
        d.text((6, 35), fit(d, count, face, 116), font=face, fill=0)
        d.text((6, 63), "Top Weekly Artist", font=font(10, True), fill=0)
        d.text((6, 76), fit(d, card["artist"], font(11, True), 116), font=font(11, True), fill=0)
        d.text((6, 91), fit(d, "%s plays" % card["artist_plays"], font(11), 116), font=font(11), fill=0)
    elif kind == "sun":
        d.text((6, 5), "SUN TIMES", font=font(10, True), fill=0)
        d.text((6, 29), card["day"], font=font(11, True), fill=0)
        image.paste(horizon_icon(25), (96, 25))
        for y, key in ((54, "sunrise"), (78, "sunset")):
            d.text((6, y), key.title(), font=font(11), fill=0)
            value = card.get(key)
            clock = value.strftime("%I:%M %p").lstrip("0") if value else "—"
            d.text((122-d.textlength(clock, font=font(10, True)), y), clock, font=font(10, True), fill=0)
    elif kind == "pokemon":
        d.text((6, 5), "POKÉMON", font=font(10, True), fill=0)
        number = "#%03d" % card["number"]
        d.text((122-d.textlength(number, font=font(9)), 6), number, font=font(9), fill=0)
        try:
            sprite = asset(card["sprite"], 56, True)
            image.paste(sprite, ((128-sprite.width)//2, 29+(58-sprite.height)//2))
        except OSError:
            d.text((6, 49), "No sprite", font=font(10), fill=0)
        name = fit(d, card["name"], font(10, True), 116)
        d.text(((128-d.textlength(name, font=font(10, True)))//2, 93), name, font=font(10, True), fill=0)
    return image
