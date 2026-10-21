#!/usr/bin/env python3
"""Strict GLSL ES fixes for Mali (run from an xemu-based source root).

Uniform ("inline") vertex attributes were emitted as
    vec4 vN = inlineValue[k];
at global scope.  A global initializer that reads a uniform is not legal
GLSL ES 3.x (needs GL_EXT_shader_non_constant_global_initializers, which
Adreno has and Mali does not).  Mali rejects the whole vertex shader, the
program fails to link and every draw that uses a constant attribute
(terrain, HUD, first-person weapon...) is silently skipped.
Declare the global and assign it at the top of main() instead.
"""
import sys

p = "hw/xbox/nv2a/pgraph/glsl/vsh.c"
s = open(p, encoding="utf-8").read()
old_decl = '''            mstring_append_fmt(header, "vec4 v%d = inlineValue[%d];\\n", i,
                               num_uniform_attrs);
            num_uniform_attrs += 1;'''
new_decl = '''            mstring_append_fmt(header, "vec4 v%d;\\n", i);
            num_uniform_attrs += 1;'''
old_main = '''    MString *body = mstring_from_str("void main() {\\n");
'''
new_main = '''    MString *body = mstring_from_str("void main() {\\n");

    for (int i = 0, k = 0; i < NV2A_VERTEXSHADER_ATTRIBUTES; i++) {
        if (state->uniform_attrs & (1 << i)) {
            mstring_append_fmt(body, "v%d = inlineValue[%d];\\n", i, k++);
        }
    }
'''
for old, new in ((old_decl, new_decl), (old_main, new_main)):
    if s.count(old) != 1:
        sys.exit("gles_strict: pattern not found once in %s:\n%s" % (p, old))
    s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
print("gles_strict: uniform attributes assigned in main()")

# Depth clamp: desktop GL enables GL_DEPTH_CLAMP for every draw; GLES only
# has it through GL_EXT_depth_clamp. Enable it when the driver offers it so
# far/near geometry is not clipped away.
p = "hw/xbox/nv2a/pgraph/gl/draw.c"
s = open(p, encoding="utf-8").read()
old = '''#ifndef __ANDROID__
    glEnable(GL_DEPTH_CLAMP);
#endif'''
new = '''#ifndef __ANDROID__
    glEnable(GL_DEPTH_CLAMP);
#else
    {
        static int has_depth_clamp = -1;
        if (has_depth_clamp < 0) {
            GLint n = 0;
            has_depth_clamp = 0;
            glGetIntegerv(GL_NUM_EXTENSIONS, &n);
            for (GLint e = 0; e < n; e++) {
                const char *ext = (const char *)glGetStringi(GL_EXTENSIONS, e);
                if (ext && strcmp(ext, "GL_EXT_depth_clamp") == 0) {
                    has_depth_clamp = 1;
                    break;
                }
            }
            fprintf(stderr, "nv2a: GL_EXT_depth_clamp %s\\n",
                    has_depth_clamp ? "enabled" : "unavailable");
        }
        if (has_depth_clamp) {
            glEnable(0x864F /* GL_DEPTH_CLAMP_EXT */);
        }
    }
#endif'''
if s.count(old) != 1:
    sys.exit("gles_strict: depth clamp pattern not found in " + p)
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
print("gles_strict: GL_EXT_depth_clamp enabled when available")
