import struct
import sys


if sys.platform != 'win32' or sys.version_info[:2] != (3, 12) or struct.calcsize('P') != 8:
    raise SystemExit('Presence package requires Windows Python 3.12 x64. Nothing was changed.')
sys.path.insert(0, sys.argv[1])
import discord
import aiohttp
if discord.__version__ != '2.7.1' or aiohttp.__version__ != '3.14.5':
    raise SystemExit('Presence dependency version mismatch. Nothing was changed.')
print('Presence dependency import verified')
