import hashlib, secrets, getpass
p = getpass.getpass('New dashboard password: ')
if len(p) < 12:
    raise SystemExit('Use at least 12 characters')
s = secrets.token_bytes(16)
h = hashlib.scrypt(p.encode(), salt=s, n=16384, r=8, p=1)
print('VINYLPI_DASHBOARD_PASSWORD_SCRYPT=' + s.hex() + ':' + h.hex())
print('VINYLPI_DASHBOARD_SESSION_SECRET=' + secrets.token_urlsafe(48))
