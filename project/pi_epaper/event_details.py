"""Detect actual conference URLs and explicit numbered street addresses."""
import html
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit, parse_qs, unquote

URL = re.compile(r"https?://[^\s<>\"']+", re.I)
ADDRESS = re.compile(r"\b\d{1,6}[A-Za-z]?(?:-\d{1,6})?\s+"
    r"(?:[A-Za-z0-9.'’-]+\s+){1,7}"
    r"(?:Street|St\.?|Road|Rd\.?|Avenue|Ave\.?|Lane|Ln\.?|Drive|Dr\.?|Boulevard|Blvd\.?|"
    r"Court|Ct\.?|Way|Place|Pl\.?|Parkway|Pkwy\.?|Terrace|Ter\.?|Circle|Cir\.?|Highway|Hwy\.?)\b", re.I)


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)
    def handle_starttag(self, tag, attrs):
        if tag in ("br", "p", "div", "li"):
            self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag in ("p", "div", "li"):
            self.parts.append("\n")


def plain_text(value):
    parser = PlainText()
    parser.feed(value or "")
    return html.unescape("".join(parser.parts))


def meeting_platforms(event):
    conference = event.get("conferenceData") or {}
    values = [event.get("location", ""), event.get("description", ""),
              conference.get("notes", ""), event.get("hangoutLink", "")]
    values += [p.get("uri", "") for p in conference.get("entryPoints", [])]
    platforms = set()
    for value in values:
        for url in URL.findall(html.unescape(value or "")):
            parsed = urlsplit(url.rstrip(".,;)"))
            host = (parsed.hostname or "").lower()
            # Outlook may wrap meeting links in Safe Links.
            if host.endswith(".safelinks.protection.outlook.com"):
                nested = parse_qs(parsed.query).get("url", [""])[0]
                host = (urlsplit(unquote(nested)).hostname or "").lower()
            if host in ("zoom.us", "zoomgov.com") or host.endswith((".zoom.us", ".zoomgov.com")):
                platforms.add("Zoom")
            domains = ("teams.microsoft.com", "teams.live.com", "teams.cloud.microsoft", "teams.microsoft.us")
            if any(host == domain or host.endswith("." + domain) for domain in domains):
                platforms.add("Teams")
    return "/".join(p for p in ("Zoom", "Teams") if p in platforms)


def physical_address(event):
    for value in (event.get("location", ""), event.get("description", "")):
        text = plain_text(value or "")
        text = URL.sub("", text)
        lines = text.splitlines()
        for index, line in enumerate(lines):
            match = ADDRESS.search(line)
            if not match:
                continue
            address = line[match.start():].strip()
            if index + 1 < len(lines) and not re.search(r"\b\d{5}(?:-\d{4})?\b", address):
                following = lines[index+1].strip()
                if re.search(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b", following):
                    address += ", " + following
            postal = re.search(r"\b\d{5}(?:-\d{4})?\b", address[match.end()-match.start():])
            if postal:
                stop = match.end()-match.start()+postal.end()
                address = address[:stop]
            else:
                address = re.split(r"[;|]|\b(?:Join|Zoom|Teams)\b", address, maxsplit=1, flags=re.I)[0]
            return address.strip(" ,.")[:250]
    return ""


def excluded_event(event):
    title = " ".join((event.get("summary") or "").split()).casefold()
    return title in {"out of office", "grow trainig"}
