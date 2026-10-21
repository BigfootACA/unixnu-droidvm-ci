#!/usr/bin/env python3
"""Run the unixnu build driver on Linux with the macOS-only tools replaced.

  unixnu-linux.py UNIXNU_DIR [unixnu arguments...]

- /usr/bin/compression_tool -decode -a lzfse  ->  lzfse -decode (the lzfse package)

Only the steps that need no Xcode work this way (fetch, ipsw, package, payload pieces).
"""
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / 'scripts'))
sys.argv = [str(root / 'unixnu'), *sys.argv[2:]]

import unixnu.ipsw as ipsw  # noqa: E402
import unixnu.util as util  # noqa: E402

_run = subprocess.run
_tool = util.tool


def run(command, *args, **kwargs):
    command = [str(part) for part in command]
    if command[:1] == ['/usr/bin/compression_tool']:
        options = {flag: command[i + 1] for i, flag in enumerate(command) if flag in ('-i', '-o')}
        command = ['lzfse', '-decode', '-i', options['-i'], '-o', options['-o']]
    return _run(command, *args, **kwargs)


def tool(name, *args, **kwargs):
    if name == 'compression_tool':
        return _tool('lzfse', 'pacman -S lzfse')
    return _tool(name, *args, **kwargs)


ipsw.subprocess.run = run
ipsw.tool = tool
from unixnu.cli import main  # noqa: E402
main()
