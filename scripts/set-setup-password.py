"""Set the local Live screening password without storing its plaintext."""
from getpass import getpass
from pathlib import Path
import sys
import re

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.setup_lock import hash_password


def main():
    password = getpass('Live setup password: ')
    if password != getpass('Repeat password: '):
        raise SystemExit('Passwords did not match; no file changed.')
    encoded = hash_password(password)
    path = ROOT / '.env'
    lines = path.read_text(encoding='utf-8-sig').splitlines() if path.exists() else []
    lines = [line for line in lines if not re.match(r'^\s*(?:SETUPS_PIN|SETUPS_PASSWORD_HASH)\s*=',line)]
    path.write_text('\n'.join([*lines, f"SETUPS_PASSWORD_HASH='{encoded}'", '']), encoding='utf-8')
    print('Saved a salted password hash in the local .env. Restart GARDiFUND to use it.')


if __name__ == '__main__':
    main()
