#!/usr/bin/env python3
"""Surface read-back at resolution scale > 1x: shrink on the GPU first.

When the guest reads back a render target and the internal resolution is
scaled (2x, 3x...), the Android GL path read the whole scaled surface
(4x/9x the data, synchronously) and shrank it on the CPU.  Now the colour
read-back first blits the surface down to native size on the GPU and reads
only that, so the CPU gets 1x data and no shrink loop.
XEMU_ANDROID_GPU_DOWNSCALE=0 restores the old behaviour.
"""
import sys

P = "hw/xbox/nv2a/pgraph/gl/surface.c"
s = open(P, encoding="utf-8").read()

old = '''    if (android_surface_uses_rgba8_transfer(surface)) {
        const unsigned int factor = downscale ? pg->surface_scale_factor : 1;
        const unsigned int read_width = surface->width * factor;
        const unsigned int read_height = surface->height * factor;
        const unsigned int rgba_stride = read_width * 4;
        uint8_t *rgba_pixels = g_malloc(read_height * rgba_stride);
        uint8_t *rgba_linear = rgba_pixels;
        uint8_t *linear_guest = pixels;

        android_glo_readpixels(pg->gl_renderer_state, GL_RGBA,
                               GL_UNSIGNED_BYTE, 4, rgba_stride,
                               read_width, read_height, flip, rgba_pixels);'''
new = '''    if (android_surface_uses_rgba8_transfer(surface)) {
        GLint xog_prev_read_fbo = 0, xog_prev_draw_fbo = 0;
        bool xog_gpu_shrunk = false;
        if (downscale && pg->surface_scale_factor > 1 && xog_gpu_downscale_on()) {
            static GLuint xog_fbo, xog_tex;
            static unsigned int xog_w, xog_h;
            const unsigned int f = pg->surface_scale_factor;
            glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &xog_prev_read_fbo);
            glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &xog_prev_draw_fbo);
            if (!xog_fbo) {
                glGenFramebuffers(1, &xog_fbo);
                glGenTextures(1, &xog_tex);
            }
            if (xog_w != surface->width || xog_h != surface->height) {
                GLint prev_tex = 0;
                glGetIntegerv(GL_TEXTURE_BINDING_2D, &prev_tex);
                glBindTexture(GL_TEXTURE_2D, xog_tex);
                glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, surface->width,
                             surface->height, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
                glBindTexture(GL_TEXTURE_2D, prev_tex);
                xog_w = surface->width;
                xog_h = surface->height;
            }
            glBindFramebuffer(GL_DRAW_FRAMEBUFFER, xog_fbo);
            glFramebufferTexture2D(GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                   GL_TEXTURE_2D, xog_tex, 0);
            if (glCheckFramebufferStatus(GL_DRAW_FRAMEBUFFER) ==
                GL_FRAMEBUFFER_COMPLETE) {
                GLboolean scissor = glIsEnabled(GL_SCISSOR_TEST);
                glDisable(GL_SCISSOR_TEST);
                glBlitFramebuffer(0, 0, surface->width * f, surface->height * f,
                                  0, 0, surface->width, surface->height,
                                  GL_COLOR_BUFFER_BIT, GL_NEAREST);
                if (scissor) {
                    glEnable(GL_SCISSOR_TEST);
                }
                glBindFramebuffer(GL_READ_FRAMEBUFFER, xog_fbo);
                glReadBuffer(GL_COLOR_ATTACHMENT0);
                xog_gpu_shrunk = true;
                downscale = false;
            } else {
                glBindFramebuffer(GL_DRAW_FRAMEBUFFER, xog_prev_draw_fbo);
            }
        }
        const unsigned int factor = downscale ? pg->surface_scale_factor : 1;
        const unsigned int read_width = surface->width * factor;
        const unsigned int read_height = surface->height * factor;
        const unsigned int rgba_stride = read_width * 4;
        uint8_t *rgba_pixels = g_malloc(read_height * rgba_stride);
        uint8_t *rgba_linear = rgba_pixels;
        uint8_t *linear_guest = pixels;

        android_glo_readpixels(pg->gl_renderer_state, GL_RGBA,
                               GL_UNSIGNED_BYTE, 4, rgba_stride,
                               read_width, read_height, flip, rgba_pixels);
        if (xog_gpu_shrunk) {
            glBindFramebuffer(GL_READ_FRAMEBUFFER, xog_prev_read_fbo);
            glBindFramebuffer(GL_DRAW_FRAMEBUFFER, xog_prev_draw_fbo);
            glReadBuffer(surface->color ? surface->fmt.gl_attachment : GL_NONE);
        }'''
if s.count(old) != 1:
    sys.exit("gles_gpu_downscale: pattern found %d times" % s.count(old))
s = s.replace(old, new)

helper = '''
#ifdef __ANDROID__
static bool xog_gpu_downscale_on(void)
{
    static int v = -1;
    if (v < 0) {
        const char *e = getenv("XEMU_ANDROID_GPU_DOWNSCALE");
        v = !(e && e[0] == '0');
    }
    return v;
}
#endif
'''
anchor = "static void android_glo_readpixels(PGRAPHGLState *r, GLenum gl_format,"
if s.count(anchor) != 1:
    sys.exit("gles_gpu_downscale: anchor not found")
s = s.replace(anchor, helper + anchor)
open(P, "w", encoding="utf-8").write(s)
print("gles_gpu_downscale: GPU shrink before surface read-back")
