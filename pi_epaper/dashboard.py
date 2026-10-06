"""Compact monochrome weather/calendar screen and code-drawn condition icons."""
import math
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont, ImageFilter


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/" + name, size)


def fit(draw, text, face, width):
    text = " ".join(str(text).split())
    if draw.textlength(text, font=face) <= width:
        return text
    while text and draw.textlength(text + "…", font=face) > width:
        text = text[:-1]
    return text + "…" if text else ""


def weather_description(code):
    labels = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
              45: "Fog", 48: "Freezing fog", 51: "Light drizzle", 53: "Drizzle",
              55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
              61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain",
              67: "Freezing rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow",
              77: "Snow grains", 80: "Rain showers", 81: "Rain showers",
              82: "Heavy showers", 85: "Snow showers", 86: "Snow showers",
              95: "Thunderstorm", 96: "Storm / hail", 99: "Storm / hail"}
    return labels.get(code, "Unknown")


def condition_icon(code, size=48, is_day=True):
    # Draw on a larger canvas, then reduce for clean, one-bit e-paper edges.
    scale = 4
    image = Image.new("L", (48 * scale, 48 * scale), 255)
    d = ImageDraw.Draw(image)
    def box(points):
        return tuple(round(v * scale) for v in points)
    def line(points, width=2):
        d.line([box(p) for p in points], fill=0, width=width * scale)
    def sun(cx, cy):
        d.ellipse(box((cx-7, cy-7, cx+7, cy+7)), outline=0, width=2*scale)
        for i in range(8):
            a = i * math.pi / 4
            line([(cx+11*math.cos(a), cy+11*math.sin(a)),
                  (cx+15*math.cos(a), cy+15*math.sin(a))])
    def moon(cx, cy):
        d.ellipse(box((cx-11, cy-11, cx+11, cy+11)), fill=0)
        d.ellipse(box((cx-3, cy-14, cx+16, cy+5)), fill=255)
    if code in (0, 1, 2):
        (sun if is_day else moon)(24 if code == 0 else 16, 24 if code == 0 else 16)
    if code != 0:
        mask = Image.new("L", image.size, 0)
        m = ImageDraw.Draw(mask)
        for shape in ((6, 20, 26, 37), (16, 12, 37, 36), (29, 21, 44, 37)):
            m.ellipse(box(shape), fill=255)
        m.rectangle(box((15, 27, 36, 37)), fill=255)
        image.paste(0, mask=mask)
        image.paste(255, mask=mask.filter(ImageFilter.MinFilter(2*scale+1)))
        d = ImageDraw.Draw(image)
    if code in (45, 48):
        for y in (39, 43, 47):
            line([(8, y), (40, y)], 1)
    elif code in (71, 73, 75, 77, 85, 86):
        for x, y in ((14, 42), (30, 43)):
            for a in (0, math.pi/3, 2*math.pi/3):
                line([(x-4*math.cos(a), y-4*math.sin(a)),
                      (x+4*math.cos(a), y+4*math.sin(a))], 1)
    elif code in (95, 96, 99):
        d.polygon([box(p) for p in ((26, 32), (18, 42), (25, 42), (20, 48),
                                   (34, 36), (27, 36), (31, 32))], fill=0)
    elif code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        for x in (13, 25, 37):
            line([(x, 40), (x-3, 46)])
    if code is None:
        image = Image.new("L", image.size, 255)
        ImageDraw.Draw(image).text((13*scale, 3*scale), "?", font=font(36*scale, True), fill=0)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def car_icon(size=18):
    image = Image.new("L", (80, 64), 255)
    d = ImageDraw.Draw(image)
    d.polygon([(8, 30), (19, 12), (55, 12), (68, 30), (74, 31),
               (74, 49), (6, 49), (6, 31)], fill=255, outline=0, width=6)
    d.line((15, 30, 63, 30), fill=0, width=4)
    d.line((38, 14, 38, 29), fill=0, width=4)
    for x in (19, 58):
        d.ellipse((x-7, 43, x+7, 57), fill=0)
    return image.resize((size, round(size*0.8)), Image.Resampling.LANCZOS)


def drop_icon(size=16, filled=True):
    image = Image.new("L", (64, 64), 255)
    d = ImageDraw.Draw(image)
    d.polygon([(32, 3), (12, 32), (10, 43), (16, 54), (32, 60),
               (48, 54), (54, 43), (52, 32)], fill=0 if filled else 255, outline=0, width=5)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def snowflake_icon(size=16):
    image = Image.new("L", (64, 64), 255)
    d = ImageDraw.Draw(image)
    for i in range(6):
        angle = i * math.pi / 3
        tip = (32+27*math.cos(angle), 32+27*math.sin(angle))
        d.line([(32, 32), tip], fill=0, width=4)
        branch = (32+16*math.cos(angle), 32+16*math.sin(angle))
        for turn in (-math.pi/3, math.pi/3):
            end = (branch[0]+11*math.cos(angle+turn), branch[1]+11*math.sin(angle+turn))
            d.line([branch, end], fill=0, width=4)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def precipitation_icon(day, size=16, expected=True):
    # The day's forecast can call for snow even if it is currently clear.
    code = day.get("precip_code", day.get("code"))
    if code in (71, 73, 75, 77, 85, 86):
        return snowflake_icon(size)
    return drop_icon(size, expected)


def precipitation_text(day, compact=False):
    chance, amount = day.get("precip_probability"), day.get("precip_inches")
    expected = (chance is not None and chance >= 50) or (amount is not None and amount >= 0.01)
    if compact:
        label = "Precip "
    elif expected:
        label = "Precip expected "
    elif chance is not None and chance > 0:
        label = "Precip chance "
    elif amount == 0:
        label = "Dry forecast "
    else:
        label = "Precip "
    label += "%d%%" % round(chance) if chance is not None else "—"
    if amount is not None and amount >= 0.01:
        label += ' · %.2f"' % amount
    return label, expected


def uv_text(value):
    if value is None or not math.isfinite(value) or value < 0:
        return "UV —"
    level = "Low" if value < 3 else "Medium" if value < 6 else "High"
    return "UV %g %s" % (round(value, 1), level)


def event_when(event, now):
    """Re-evaluate cached event dates in the display's local timezone."""
    if not event.get("start"):
        return event.get("when", "")
    start = datetime.fromisoformat(event["start"].replace("Z", "+00:00"))
    if not event.get("all_day") and start.tzinfo is not None:
        start = start.astimezone(now.tzinfo)
    day = "Today" if start.date() == now.date() else "%s %d/%d" % (
        start.strftime("%a"), start.month, start.day)
    return day + (" · all day" if event.get("all_day") else " " + start.strftime("%I:%M %p").lstrip("0"))


def event_metadata(draw, event, width, now=None, face=None, include_date=True):
    face = face or font(10)
    when = event_when(event, now) if now is not None else event.get("when", "")
    if not include_date:
        when = ""
    meeting = event.get("meeting", "")
    suffix = " · " + meeting if meeting else ""
    prefix = when + " · " if when else ""
    left = max(0, width - draw.textlength(prefix + suffix, font=face))
    calendar = fit(draw, event["calendar"], face, left)
    return fit(draw, prefix + calendar + suffix, face, width)


def draw_date_header(draw, now, weather=None, weather_note=""):
    draw.rectangle((0, 0, 399, 33), fill=0)
    date = now.strftime("%a %b %d")
    draw.text((11, 5), date, font=font(18, True), fill=255)
    clock = now.strftime("%I:%M %p").lstrip("0")
    clock_x = 389-draw.textlength(clock, font=font(18))
    draw.text((clock_x, 5), clock, font=font(18), fill=255)
    weather_x = round(11+draw.textlength(date, font=font(18, True))+12)
    if weather:
        icon = condition_icon(weather.get("code"), 24, weather.get("is_day", True))
        draw.bitmap((weather_x, 5), icon.point(lambda value: 255-value), fill=255)
        text_x = weather_x+29
        width = clock_x-10-text_x
        temp = "%d°%s" % (round(weather["temperature"]), weather["unit"])
        draw.text((text_x, 1), fit(draw, temp, font(14, True), width), font=font(14, True), fill=255)
        if weather_note:
            cached_x = text_x+draw.textlength(temp, font=font(14, True))+5
            if draw.textlength("cached", font=font(9)) <= clock_x-10-cached_x:
                draw.text((cached_x, 5), "cached", font=font(9), fill=255)
        draw.text((text_x, 18), fit(draw, weather_description(weather.get("code")), font(11), width),
                  font=font(11), fill=255)
    elif weather_note:
        draw.text((weather_x, 10), fit(draw, "Weather unavailable", font(11), clock_x-10-weather_x),
                  font=font(11), fill=255)


def render_dashboard(weather, events, now, weather_note="", calendar_note="", music_note="", corner=None,
                     no_match=False):
    image = Image.new("L", (400, 300), 255)
    d = ImageDraw.Draw(image)
    draw_date_header(d, now)
    if no_match:
        label = "NO MATCH"
        left = 11+d.textlength(now.strftime("%a %b %d"), font=font(18, True))+10
        clock = now.strftime("%I:%M %p").lstrip("0")
        right = 389-d.textlength(clock, font=font(18))-10
        face = font(13, True)
        text_width = d.textlength(label, font=face)
        width = text_width+34
        x = round((left+right-width)/2)
        d.text((x+17, 8), label, font=face, fill=255)
        # Solid notes keep their stems visible after conversion to one-bit BMP.
        for note_x in (x, round(x+17+text_width+6)):
            d.ellipse((note_x, 19, note_x+5, 22), fill=255)
            d.line((note_x+5, 20, note_x+5, 9), fill=255, width=2)
            d.line([(note_x+5, 9), (note_x+10, 12), (note_x+10, 15)], fill=255, width=2)
    if weather:
        temp = "%d°%s" % (round(weather["temperature"]), weather["unit"])
        temp_size = 39
        while temp_size > 24 and d.textlength(temp, font=font(temp_size, True)) > 119:
            temp_size -= 1
        d.text((10, 39), temp, font=font(temp_size, True), fill=0)
        image.paste(condition_icon(weather["code"], 48, weather.get("is_day", True)), (135, 38))
        d.text((193, 43), fit(d, weather_description(weather["code"]), font(16, True), 197),
               font=font(16, True), fill=0)
        feels_uv = "Feels like %d° · %s" % (round(weather["feels"]), uv_text(weather.get("uv_index")))
        d.text((193, 68), fit(d, feels_uv, font(11), 197), font=font(11), fill=0)
        d.text((11, 86), "Today  H %d° / L %d°" % (round(weather["high"]), round(weather["low"])),
               font=font(13), fill=0)
        precip, expected = precipitation_text(weather)
        image.paste(precipitation_icon(weather, 15, expected), (12, 105))
        d.text((34, 104), fit(d, precip, font(13, expected), 353), font=font(13, expected), fill=0)
        tomorrow = weather.get("tomorrow")
        d.text((11, 128), "Tomorrow", font=font(12), fill=0)
        if tomorrow:
            image.paste(condition_icon(tomorrow.get("code"), 22), (70, 129))
            d.text((98, 128), fit(d, weather_description(tomorrow.get("code")), font(11), 156),
                   font=font(11), fill=0)
            d.text((268, 128), "H %d° / L %d°" % (round(tomorrow["high"]), round(tomorrow["low"])),
                   font=font(11), fill=0)
            label, _ = precipitation_text(tomorrow, compact=True)
            d.text((98, 144), fit(d, label, font(10), 291), font=font(10), fill=0)
        else:
            d.text((98, 128), "Forecast unavailable", font=font(11), fill=0)
    else:
        image.paste(condition_icon(None, 44), (14, 52))
        d.text((70, 58), "Weather unavailable", font=font(21, True), fill=0)
        d.text((12, 132), "Today's and tomorrow's forecasts will appear here.", font=font(12), fill=0)
    d.line((10, 166, 389, 166), fill=0)
    warnings = " · ".join(x for x in ("Weather cached" if weather and weather_note else "",
                          "Calendar partial" if calendar_note and events else "", music_note) if x)
    d.text((193, 88), fit(d, warnings, font(9), 196), font=font(9), fill=0)
    if corner:
        from corner_render import CORNER_POSITION, render_corner
    calendar_width = CORNER_POSITION[0]-23 if corner else 378
    if not events:
        d.text((11, 179), fit(d, calendar_note or "No upcoming events in the next 7 days", font(13), calendar_width),
               font=font(13), fill=0)
    for idx, event in enumerate(events[:3]):
        y = 171 + idx*42
        d.text((11, y), fit(d, event["summary"], font(13, True), calendar_width), font=font(13, True), fill=0)
        details_y = y + 15
        details_face = font(11)
        available = calendar_width
        label = None
        if event.get("physical_address") or event.get("drive_minutes") is not None:
            minutes = event.get("drive_minutes")
            label = "~%d min" % minutes if minutes is not None else (
                "…" if event.get("drive_status") == "pending" else "—")
            width = d.textlength(label, font=details_face)
            available -= 34 + width
        full_details = " · ".join(" ".join(str(part).split()) for part in (
            event_when(event, now), event["calendar"], event.get("meeting", "")) if part)
        if d.textlength(full_details, font=details_face) > available:
            d.text((11, details_y), fit(d, event_when(event, now), details_face, calendar_width),
                   font=details_face, fill=0)
            details_y += 14
            details = event_metadata(d, event, available, now, details_face, include_date=False)
        else:
            details = event_metadata(d, event, available, now, details_face)
        d.text((11, details_y), details, font=details_face, fill=0)
        if label is not None:
            x = round(11 + d.textlength(details + " · ", font=details_face))
            d.text((11+d.textlength(details, font=details_face), details_y), " · ", font=details_face, fill=0)
            image.paste(car_icon(17), (x, details_y-1))
            d.text((x+22, details_y), label, font=details_face, fill=0)
    if corner:
        image.paste(render_corner(corner, now), CORNER_POSITION)
    return image.convert("1", dither=Image.Dither.NONE)
