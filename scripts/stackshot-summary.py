#!/usr/bin/env python3
"""Print the threads of chosen processes from a stackshot report, with symbolized frames.

    stackshot-summary.py REPORT PROCESS[,PROCESS...] [--kernel KERNEL_MACHO] [--dsc DSC --ipsw IPSW]

REPORT is the output of `kcdata.py -s REPORT SHOT.kcdata` (tools/lldbmacros of the xnu tree). Kernel frames are named
with the nearest symbol of KERNEL_MACHO (llvm-nm; kcdata.py reports kernel addresses unslid), shared cache frames with `ipsw dyld a2s` against DSC (the dyld_shared_cache_arm64 of the SystemOS volume).
PROCESS 'all' prints every process.
"""
import argparse
import bisect
import json
import subprocess

DSC_UNSLID_BASE = 0x180000000


class KernelSymbols:
    def __init__(self, path):
        self.addresses, self.names = [], []
        output = subprocess.run(['llvm-nm', '-n', '--defined-only', path], capture_output=True, text=True).stdout
        for line in output.splitlines():
            fields = line.split()
            if len(fields) == 3 and fields[1] in 'tTsS':
                self.addresses.append(int(fields[0], 16))
                self.names.append(fields[2])

    def name(self, address):
        index = bisect.bisect_right(self.addresses, address) - 1
        if index < 0:
            return f'kernel {address:#x}'
        return f'{self.names[index]}+{address - self.addresses[index]:#x}'


class SharedCacheSymbols:
    def __init__(self, dsc, ipsw, cache):
        self.dsc, self.ipsw, self.cache, self.known = dsc, ipsw, cache, {}

    def name(self, offset):
        address = DSC_UNSLID_BASE + offset
        if address not in self.known:
            command = [self.ipsw, 'dyld', 'a2s', '--no-color', self.dsc, hex(address), '-i']
            if self.cache:
                command += ['--cache', self.cache]
            output = subprocess.run(command, capture_output=True, text=True).stdout.strip().splitlines()
            image = next((line.split('>', 1)[1].split()[0].rsplit('/', 1)[-1] for line in output
                          if line.lstrip().startswith('>')), '?')
            symbol = output[-1].split(': ', 1)[-1].strip() if output else f'dsc+{offset:#x}'
            self.known[address] = f'{image}: {symbol}'
        return self.known[address]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('report')
    parser.add_argument('processes')
    parser.add_argument('--kernel')
    parser.add_argument('--dsc')
    parser.add_argument('--ipsw')
    parser.add_argument('--a2s-cache')
    args = parser.parse_args()
    with open(args.report) as stream:
        stream.readline()
        report = json.load(stream)
    images = report['binaryImages']
    kernel = KernelSymbols(args.kernel) if args.kernel else None
    dsc = SharedCacheSymbols(args.dsc, args.ipsw, args.a2s_cache) if args.dsc and args.ipsw else None
    wanted = None if args.processes == 'all' else set(args.processes.split(','))

    def frame(entry):
        index, offset = entry
        uuid, base, kind = images[index][:3]
        if kind == 'S' and dsc:
            return dsc.name(offset)
        if kind in 'KTU' and kernel:
            return kernel.name(base + offset)
        return f'{kind}:{uuid[:8]}@{base:#x}+{offset:#x}'

    print(report.get('notes', ''))
    for pid, process in sorted(report['processByPid'].items(), key=lambda item: int(item[0])):
        if wanted is not None and process.get('procname') not in wanted:
            continue
        print(f'=== pid {pid} {process.get("procname")}')
        for wait in process.get('waitInfo', []):
            print(f'  wait: {wait}')
        for tid, thread in process.get('threadById', {}).items():
            state = ','.join(thread.get('state', []))
            print(f'  thread {tid} {thread.get("name", "")!r} {state} pri {thread.get("schedPriority")} '
                  f'user {thread.get("userTime", 0):.3f}s sys {thread.get("systemTime", 0):.3f}s')
            if 'waitEvent' in thread:
                print(f'    wait event {frame(thread["waitEvent"])}')
            if 'continuation' in thread:
                print(f'    continuation {frame(thread["continuation"])}')
            for entry in thread.get('kernelFrames', []):
                print(f'    k {frame(entry)}')
            for entry in thread.get('userFrames', []):
                print(f'    u {frame(entry)}')


if __name__ == '__main__':
    main()
