#!/usr/bin/env python3
"""Remove per-draw glGetError() calls on Android (run from source root).

The Android GL renderer checks glGetError() after every texture stage,
every uniform update and every draw, tens of thousands of times per frame
in Halo 2. Arm advises against glGetError in production on Mali: it is a
driver round trip each time.  All those checks are now skipped unless
XEMU_ANDROID_GL_DEBUG=1, including the asserts that would otherwise trip on
errors the skipped checks no longer clear.
"""
import re
import sys

HELPER = '''
#ifdef __ANDROID__
#include <stdlib.h>
static inline int xog_gl_debug(void)
{
    static int v = -1;
    if (v < 0) {
        const char *e = getenv("XEMU_ANDROID_GL_DEBUG");
        v = (e && e[0] == '1');
    }
    return v;
}
#else
static inline int xog_gl_debug(void)
{
    return 1;
}
#endif
'''

FILES = [
    "hw/xbox/nv2a/pgraph/gl/draw.c",
    "hw/xbox/nv2a/pgraph/gl/texture.c",
    "hw/xbox/nv2a/pgraph/gl/vertex.c",
    "hw/xbox/nv2a/pgraph/gl/shaders.c",
    "hw/xbox/nv2a/pgraph/gl/surface.c",
    "hw/xbox/nv2a/pgraph/gl/display.c",
    "ui/xemu.c",
]

total = 0
for p in FILES:
    s = open(p, encoding="utf-8").read()
    n0 = 0
    # error-draining log loops
    s, n = re.subn(r'while \(\((\w+) = glGetError\(\)\) != GL_NO_ERROR\)',
                   r'while (xog_gl_debug() && (\1 = glGetError()) != GL_NO_ERROR)', s)
    n0 += n
    # asserts on the error state
    s, n = re.subn(r'assert\(glGetError\(\) == GL_NO_ERROR\)',
                   r'assert(!xog_gl_debug() || glGetError() == GL_NO_ERROR)', s)
    n0 += n
    if n0:
        inc = s.find('#include "qemu/osdep.h"')
        if inc < 0:
            inc = s.find('#include')
        if inc < 0:
            sys.exit("gles_no_geterror: no include in " + p)
        end = s.index("\n", inc) + 1
        s = s[:end] + HELPER + s[end:]
        open(p, "w", encoding="utf-8").write(s)
    print("%-40s %d checks gated" % (p, n0))
    total += n0
if total < 15:
    sys.exit("gles_no_geterror: expected more call sites, got %d" % total)
print("gles_no_geterror: %d glGetError sites gated" % total)
