#!/usr/bin/env python3
"""Generate the Apple-format device tree (ADT) of the UniXNU virtual machine board.

Two machines are described from their own published memory maps:

  qemu    QEMU `virt` (KVM or TCG): RAM at 0x40000000, GICv3 at 0x08000000/0x080a0000, PL011 at 0x09000000.
  crosvm  DroidVM crosvm (Gunyah or KVM): RAM at 0x80000000, GICv3 distributor at 0x3fff0000 with the
          redistributors below it, SBSA PL011 at 0x6000 (crosvm --serial hardware=sbsa).

The kernel finds the console as /arm-io/uart0 (patches/xnu/virt-pl011-uart.patch). Every arm-io reg is
relative to the arm-io range base; that base must not be zero, or pe_serial skips every UART.
"""
from pathlib import Path
import argparse
import os
import struct
import uuid
import zlib


def node(name, properties=(), children=()):
    properties = [('name', name.encode() + b'\0'), *properties]
    result = struct.pack('<II', len(properties), len(children))
    for key, data in properties:
        result += struct.pack('<32sI', key.encode(), len(data))
        result += data + bytes(-len(data) % 4)
    return result + b''.join(children)


def u32(value):
    return struct.pack('<I', value)


def u64(*values):
    return struct.pack('<%dQ' % len(values), *values)


def string(value):
    return value.encode() + b'\0'


def empty_nvram_proxy():
    """A fresh volatile CHRP bank for upstream IODTNVRAM, with no controller."""
    def header(name, size):
        raw = bytearray(struct.pack('<BBH12s', 0x70, 0, size // 16, name))
        checksum = sum(raw)
        while checksum > 255:
            checksum = (checksum & 255) + (checksum >> 8)
        raw[1] = checksum
        return raw

    image = bytearray(8192)
    image[:16] = header(b'nvram', 32)
    image[32:48] = header(b'common', 16 + 2048)
    free_at = 32 + 16 + 2048
    image[free_at:free_at + 16] = header(b'free', len(image) - free_at)
    struct.pack_into('<I', image, 16, zlib.adler32(image[20:]))
    return [('nvram-bank-size', u32(len(image))), ('nvram-bank-count', u32(1)),
            ('nvram-current-bank', u32(0)), ('nvram-proxy-data', bytes(image))]


def machine_layout(name, smp):
    """Physical addresses of the machine: dram base, arm-io window, GIC and UART."""
    if name == 'qemu':
        # hw/arm/virt.c: VIRT_GIC_DIST 0x08000000, VIRT_GIC_REDIST 0x080a0000 (0xf60000), VIRT_UART 0x09000000.
        return dict(dram_base=0x40000000, io_base=0x08000000, io_size=0x04000000,
                    gicd=(0x08000000, 0x10000), gicr=(0x080a0000, 0xf60000), uart=(0x09000000, 0x1000),
                    product='QEMU virt')
    # crosvm aarch64/src/lib.rs: AARCH64_GIC_DIST_BASE 0x40000000 - 0x10000, redistributors of 0x20000 per vCPU
    # directly below; arch/src/serial.rs + DroidVM SBSA UART at 0x6000. The arm-io window starts at 0x1000.
    gicd_base = 0x40000000 - 0x10000
    gicr_size = 0x20000 * smp
    return dict(dram_base=0x80000000, io_base=0x1000, io_size=0x40000000 - 0x1000,
                gicd=(gicd_base, 0x10000), gicr=(gicd_base - gicr_size, gicr_size), uart=(0x6000, 0x1000),
                product='DroidVM crosvm')


parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument('--machine', choices=['qemu', 'crosvm'], default='qemu')
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--smp', type=int, default=1, help='CPU count; MPIDR Aff0 numbers them, PSCI conduit is hvc')
parser.add_argument('--timebase', type=lambda value: int(value, 0), required=True,
                    help='CNTFRQ_EL0 of the guest, which is the host counter frequency under KVM and Gunyah')
parser.add_argument('--dram-base', type=lambda value: int(value, 0), help='override the machine RAM base')
parser.add_argument('--dram-size', type=lambda value: int(value, 0), default=0x80000000)
parser.add_argument('--owned-memory-size', type=lambda value: int(value, 0), default=0x40000000,
                    help='DarwinBoot window (chosen/darwinboot-memory-size): 256 MiB to 8 GiB in 2 MiB steps')
parser.add_argument('--nvram-proxy', action='store_true', help='volatile CHRP NVRAM bank in /chosen, with /options')
parser.add_argument('--vmm-present', action='store_true', help='report the hypervisor in /defaults (kern.hv_vmm_present)')
parser.add_argument('--root-match-volume-uuid', type=uuid.UUID)
parser.add_argument('--osenvironment', choices=['ramdisk', 'recoveryos', 'main'])
parser.add_argument('--crypto-hash-method', choices=['sha2-384', 'sha1'])
parser.add_argument('--virtio-mmio', type=int, default=0, metavar='N',
                    help='QEMU virt: describe the first N virtio-mmio transports (0x0a000000 + 0x200 * i) for NativeVirtioBlock')
args = parser.parse_args()
if not 1 <= args.smp <= 8:
    parser.error('--smp is 1..8')
if not 0x10000000 <= args.owned_memory_size <= 0x200000000 or args.owned_memory_size % 0x200000:
    parser.error('owned memory must be 256 MiB to 8 GiB, aligned to 2 MiB')
if not 0 < args.timebase < 1 << 32:
    parser.error('timebase does not fit the u32 property')
if args.virtio_mmio and (args.machine != 'qemu' or not 0 < args.virtio_mmio <= 32):
    parser.error('--virtio-mmio is 1..32 transports of QEMU virt')

layout = machine_layout(args.machine, args.smp)
dram_base = args.dram_base if args.dram_base is not None else layout['dram_base']
io_base = layout['io_base']


def io_reg(window):
    address, size = window
    if not io_base <= address < address + size <= io_base + layout['io_size']:
        raise SystemExit(f'{address:#x}+{size:#x} is outside the arm-io window')
    return address - io_base, size


root_matching = []
if args.root_match_volume_uuid:
    xml = ('<dict><key>IOProviderClass</key><string>IOMedia</string>'
           '<key>IOPropertyMatch</key><dict><key>UUID</key><string>' +
           str(args.root_match_volume_uuid).upper() + '</string></dict></dict>\0')
    root_matching = [('root-matching', xml.encode())]


def cpu(index):
    return node('cpu%d' % index, [('state', string('running' if index == 0 else 'waiting')), ('reg', u32(index)),
                                  ('device_type', string('cpu')), ('compatible', string('arm,armv8')),
                                  ('timebase-frequency', u32(args.timebase))])


chosen = node('chosen', [
    ('random-seed', os.urandom(64)),
    ('darwinboot-memory-size', u64(args.owned_memory_size)),
    ('dram-base', u64(dram_base)),
    ('dram-size', u64(args.dram_size)),
    *([('osenvironment', string(args.osenvironment))] if args.osenvironment else []),
    *root_matching,
    *([('crypto-hash-method', string(args.crypto_hash_method))] if args.crypto_hash_method else []),
    *(empty_nvram_proxy() if args.nvram_proxy else []),
], [node('memory-map'), node('manifest-properties')])

cells = [('#address-cells', u32(2)), ('#size-cells', u32(2))]  # IOKit parses every reg as 64-bit pairs
arm_io = node('arm-io', [('device_type', string('virt-io')), *cells, ('ranges', u64(0, io_base, layout['io_size']))], [
    node('intc', [('compatible', string('arm,gic-v3')), ('reg', u64(*io_reg(layout['gicd']), *io_reg(layout['gicr'])))]),
    node('uart0', [('compatible', string('arm,pl011')), ('reg', u64(*io_reg(layout['uart'])))]),
    # hw/arm/virt.c VIRT_MMIO: 32 transports of 0x200 bytes; QEMU creates them from the top index down, so
    # -device virtio-blk-device lands in the highest free slot. NativeVirtioBlock polls and needs dma-coherent.
    *[node('virtio-mmio-%d' % index, [('compatible', string('virtio,mmio')),
                                      ('reg', u64(*io_reg((0x0a000000 + 0x200 * index, 0x200)))),
                                      ('dma-coherent', b'')])
      for index in range(32 - args.virtio_mmio, 32)],
])

tree = node('device-tree', [('model', string('unixnu-' + args.machine)), *cells, ('compatible', string('linux,dummy-virt'))], [
    chosen,
    node('cpus', children=[cpu(index) for index in range(args.smp)]),
    node('psci', [('compatible', string('arm,psci-1.0')), ('method', string('hvc'))]),
    node('defaults', [('vmm-present', u32(1))] if args.vmm_present else []),
    *([node('options')] if args.nvram_proxy else []),
    node('product', [('product-name', string(layout['product'])), ('model', string('linux,dummy-virt'))]),
    arm_io,
])
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_bytes(tree)
print(f'{args.machine} ADT: {len(tree)} bytes, {args.smp} CPU, timebase {args.timebase} Hz, dram {dram_base:#x}+{args.dram_size:#x}, '
      f'uart0 {layout["uart"][0]:#x}')
