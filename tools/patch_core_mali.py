"""Binary patch libxemu-core-i386.so so the Vulkan renderer starts on Mali GPUs.

Mali (e.g. Mali-G720 in Dimensity 8x00 chips) lacks three Vulkan features
that xemu marks as required: fillModeNonSolid, shaderClipDistance and
shaderTessellationAndGeometryPointSize. xemu already enables only the
features the device reports, so the hard requirement check is the only
thing stopping it. This patch, for the exact vendored build
(xemu 92407546 + X-OG Mobile Android changes):

 1. skips the "Device does not support required features" bail-out in
    pgraph_vk_init_instance (two branches);
 2. blanks the geometry shader line that copies gl_PointSize (needs
    shaderTessellationAndGeometryPointSize); point sprites rely on the
    vertex-stage point size instead;
 3. maps every NV2A polygon mode to VK_POLYGON_MODE_FILL (LINE/POINT need
    fillModeNonSolid); wireframe draws render filled.

shaderClipDistance is not referenced by the Vulkan shader generator.
Every patch checks the original bytes first and refuses on mismatch.

usage: patch_core_mali.py [--optional] <in.so> <out.so>
  --optional: if the library is not the known prebuilt build (e.g. a core
  rebuilt from source with the fix), copy it unchanged instead of failing.
"""
import hashlib
import struct
import sys

TEXT_DELTA = 0x1000  # .text VMA - file offset (LOAD R E segment)

NOP = struct.pack("<I", 0xD503201F)


def b_insn(src, dst):
    return struct.pack("<I", 0x14000000 | (((dst - src) >> 2) & 0x3FFFFFF))


PATCHES = [
    # (description, file offset, original bytes, new bytes)
    ("feature check: last-item failure branch 'b 0xab16dc' -> 'b 0xab16b0'",
     0xAB16A8 - TEXT_DELTA, b_insn(0xAB16A8, 0xAB16DC), b_insn(0xAB16A8, 0xAB16B0)),
    ("feature check: 'tbz w25, #0, 0xab16dc' -> nop",
     0xAB16AC - TEXT_DELTA, None, NOP),
    ("geometry shader: drop gl_PointSize copy",
     0x41419F,
     b"  gl_PointSize = gl_in[index].gl_PointSize;\n",
     b"  " + b" " * len(b"gl_PointSize = gl_in[index].gl_PointSize;") + b"\n"),
    ("pgraph_polygon_mode_vk_map {FILL, POINT, LINE} -> all FILL",
     0x4CCC54, struct.pack("<3I", 0, 2, 1), struct.pack("<3I", 0, 0, 0)),
]

# tbz w25, #0, 0xab16dc at 0xab16ac: b5=0, op=0 (tbz), b40=0, imm14=(0xab16dc-0xab16ac)/4, Rt=25
TBZ = 0x36000000 | ((((0xAB16DC - 0xAB16AC) >> 2) & 0x3FFF) << 5) | 25
PATCHES[1] = (PATCHES[1][0], PATCHES[1][1], struct.pack("<I", TBZ), PATCHES[1][3])


def matches_known_build(data):
    desc, off, old, new = PATCHES[0]
    cur = bytes(data[off:off + len(old)])
    return cur in (old, new)


def main(src, dst, optional=False):
    data = bytearray(open(src, "rb").read())
    if optional and not matches_known_build(data):
        print("core is not the known prebuilt build; Mali binary patch not needed/applicable")
        if src != dst:
            open(dst, "wb").write(data)
        return
    for desc, off, old, new in PATCHES:
        assert len(old) == len(new), desc
        cur = bytes(data[off:off + len(old)])
        if cur == new:
            print("already applied:", desc)
            continue
        if cur != old:
            sys.exit("refusing to patch (%s): found %s, expected %s" % (desc, cur.hex(), old.hex()))
        data[off:off + len(new)] = new
        print("patched:", desc)
    open(dst, "wb").write(data)
    print("sha256", hashlib.sha256(data).hexdigest())


if __name__ == "__main__":
    args = sys.argv[1:]
    optional = "--optional" in args
    args = [a for a in args if a != "--optional"]
    main(args[0], args[1], optional)
