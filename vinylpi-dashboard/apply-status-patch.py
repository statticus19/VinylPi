#!/usr/bin/env python3
"""Patch only boot notification metadata/status in the installed dashboard main.py."""
from pathlib import Path
p=Path('/home/vinyl/vinylpi-dashboard/dashboard/main.py')
s=p.read_text()
name="'vinylpi-boot-notify.service':'Sends a Discord notification when VinylPi boots.',"
if name not in s:
    anchor="SERVICE_DESCRIPTIONS = {"
    if s.count(anchor)!=1:raise SystemExit('Unknown dashboard version: no description mapping')
    s=s.replace(anchor, anchor+'\n '+name,1)
# oneshot without RemainAfterExit returns inactive/dead even on success
needle="    if active == 'inactive' and (unit in OPTIONAL_UNITS or service_type == 'oneshot'):"
if "unit == 'vinylpi-boot-notify.service' and result == 'success'" not in s:
    if s.count(needle)!=1:raise SystemExit('Unknown health_class layout; refusing to patch')
    s=s.replace(needle, "    if unit == 'vinylpi-boot-notify.service' and active == 'inactive' and result == 'success':\n        return 'completed', 'Completed'\n"+needle,1)
# Make the displayed subtype informative for this completed oneshot only.
needle="        return {'unit': unit, 'active': active, 'sub': sub,"
if "sub = 'oneshot' if unit == 'vinylpi-boot-notify.service'" not in s:
    if s.count(needle)!=1: raise SystemExit('Unknown status dictionary layout; refusing to patch')
    s=s.replace(needle, "        sub = 'oneshot' if unit == 'vinylpi-boot-notify.service' and kind == 'completed' else sub\n"+needle,1)
# preserve all existing page layouts / themes
p.write_text(s)
print('Service presentation patched:', p)
