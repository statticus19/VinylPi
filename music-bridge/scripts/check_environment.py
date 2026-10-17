#!/usr/bin/env python3
"""Read-only Pi readiness check. Does not display credentials or peers."""
import json
import platform
import shutil
import socket
import subprocess
import urllib.request

print('Hostname:', socket.gethostname())
print('Python:', platform.python_version())
if shutil.which('tailscale'):
    for args in [['version'], ['serve','status']]:
        r=subprocess.run(['tailscale',*args],capture_output=True,text=True,timeout=10)
        print('Tailscale', ' '.join(args)+':', (r.stdout or r.stderr).strip())
    r=subprocess.run(['tailscale','status','--json'],capture_output=True,text=True,timeout=10)
    if r.returncode == 0:
        try:
            data=json.loads(r.stdout)
            print('Pi tailnet hostname:',data.get('Self',{}).get('DNSName','').rstrip('.'))
            print('Tailscale state:',data.get('BackendState'))
        except ValueError:print('Tailscale status was not JSON.')
else:print('Tailscale command not found.')
url='https://itunes.apple.com/search?term=Paramore%20Still%20Into%20You&entity=musicVideo&media=musicVideo&country=US&limit=10'
try:
    with urllib.request.urlopen(url,timeout=20) as r:
        data=json.load(r)
        exact=[x for x in data.get('results',[]) if x.get('artistName')=='Paramore' and x.get('trackName')=='Still Into You' and x.get('kind')=='music-video']
        print('Apple video search HTTP:',r.status)
        print('Exact probe video IDs:',[str(x['trackId']) for x in exact])
except Exception as e:print('Apple video search:',type(e).__name__,str(e))
