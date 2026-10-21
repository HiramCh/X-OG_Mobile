#!/usr/bin/env python3
"""Mali-friendly vertex uploads for the GLES renderer (run from source root).

The GL renderer mirrors all of Xbox VRAM in one 64 MB GL buffer and patches
it with glBufferSubData whenever the guest changed vertex data -- often many
times per frame while earlier draws of the same frame still read the buffer.
Desktop drivers absorb that; Mali (deferred tile-based rendering) must either
stall or ghost (copy) the whole 64 MB buffer on every such update.

On Android, dirty ranges are now copied into a streaming ring buffer with
unsynchronized mapping (orphaned on wrap), the pattern Arm recommends for
Mali.  Clean ranges keep using the big buffer.  The big buffer is brought up
to date at the end of the frame (pgraph_gl_sync), right after the fence that
leaves the GPU idle, so that update never ghosts.

XEMU_ANDROID_GLES_STREAM_VBO=0 restores the original behaviour.
"""
import sys


def sub(path, old, new):
    s = open(path, encoding="utf-8").read()
    if s.count(old) != 1:
        sys.exit("gles_stream_vbo: pattern found %d times in %s:\n%s" % (s.count(old), path, old))
    open(path, "w", encoding="utf-8").write(s.replace(old, new))


V = "hw/xbox/nv2a/pgraph/gl/vertex.c"

STREAM_CODE = r'''
#ifdef __ANDROID__
#define XOG_RING_SIZE (32u * 1024u * 1024u)
#define XOG_MAX_PENDING 1024

static int xog_stream_enabled = -1;
static GLuint xog_ring_buffer;
static GLintptr xog_ring_head;
static struct { hwaddr a, e; } xog_pending[XOG_MAX_PENDING];
static int xog_num_pending;
static bool xog_pending_overflow;

static bool xog_stream_on(void)
{
    if (xog_stream_enabled < 0) {
        const char *e = getenv("XEMU_ANDROID_GLES_STREAM_VBO");
        xog_stream_enabled = !(e && e[0] == '0');
        fprintf(stderr, "nv2a: GLES streaming vertex buffer %s\n",
                xog_stream_enabled ? "on" : "off");
    }
    return xog_stream_enabled;
}

static bool xog_pending_overlaps(hwaddr a, hwaddr e)
{
    if (xog_pending_overflow) {
        return true;
    }
    for (int i = 0; i < xog_num_pending; i++) {
        if (a < xog_pending[i].e && xog_pending[i].a < e) {
            return true;
        }
    }
    return false;
}

static void xog_pending_add(hwaddr a, hwaddr e)
{
    for (int i = 0; i < xog_num_pending; i++) {
        if (a <= xog_pending[i].e && xog_pending[i].a <= e) {
            xog_pending[i].a = MIN(a, xog_pending[i].a);
            xog_pending[i].e = MAX(e, xog_pending[i].e);
            return;
        }
    }
    if (xog_num_pending < XOG_MAX_PENDING) {
        xog_pending[xog_num_pending].a = a;
        xog_pending[xog_num_pending].e = e;
        xog_num_pending++;
    } else {
        xog_pending_overflow = true;
    }
}

/* Called with the GPU idle (after the end-of-frame fence). */
void pgraph_gl_android_flush_vertex_uploads(NV2AState *d)
{
    PGRAPHGLState *r = d->pgraph.gl_renderer_state;

    if (!xog_num_pending && !xog_pending_overflow) {
        return;
    }
    glBindBuffer(GL_ARRAY_BUFFER, r->gl_memory_buffer);
    if (xog_pending_overflow) {
        glBufferSubData(GL_ARRAY_BUFFER, 0, memory_region_size(d->vram),
                        d->vram_ptr);
    } else {
        for (int i = 0; i < xog_num_pending; i++) {
            glBufferSubData(GL_ARRAY_BUFFER, xog_pending[i].a,
                            xog_pending[i].e - xog_pending[i].a,
                            d->vram_ptr + xog_pending[i].a);
        }
    }
    xog_num_pending = 0;
    xog_pending_overflow = false;
}

/*
 * Make the bytes [start, start + len) of VRAM available to the GPU for one
 * attribute whose element 0 lives at start - prefix.  Returns the buffer
 * and the offset to pass to glVertexAttribPointer for element 0.
 */
static GLuint xog_stream_attr(NV2AState *d, hwaddr start, hwaddr len,
                              hwaddr prefix, hwaddr *attrib_ofs)
{
    PGRAPHGLState *r = d->pgraph.gl_renderer_state;
    hwaddr vram_size = memory_region_size(d->vram);

    if (start >= vram_size) {
        return 0;
    }
    if (start + len > vram_size) {
        len = vram_size - start;
    }
    hwaddr a = start & TARGET_PAGE_MASK;
    hwaddr e = TARGET_PAGE_ALIGN(start + len);
    if (e > vram_size) {
        e = vram_size;
    }

    if (memory_region_test_and_clear_dirty(d->vram, a, e - a,
                                           DIRTY_MEMORY_NV2A)) {
        xog_pending_add(a, e);
        nv2a_profile_inc_counter(NV2A_PROF_GEOM_BUFFER_UPDATE_1);
    } else if (!xog_pending_overlaps(a, e)) {
        return 0; /* big buffer is up to date for this range */
    }

    if (!xog_ring_buffer) {
        glGenBuffers(1, &xog_ring_buffer);
        glBindBuffer(GL_ARRAY_BUFFER, xog_ring_buffer);
        glBufferData(GL_ARRAY_BUFFER, XOG_RING_SIZE, NULL, GL_STREAM_DRAW);
        xog_ring_head = 0;
    }
    glBindBuffer(GL_ARRAY_BUFFER, xog_ring_buffer);

    hwaddr ofs = ROUND_UP(MAX((hwaddr)xog_ring_head, prefix), 256);
    if (ofs + len > XOG_RING_SIZE) {
        /* Orphan: the driver hands us fresh storage, no wait on the GPU. */
        glBufferData(GL_ARRAY_BUFFER, XOG_RING_SIZE, NULL, GL_STREAM_DRAW);
        xog_ring_head = 0;
        ofs = ROUND_UP(prefix, 256);
        if (ofs + len > XOG_RING_SIZE) {
            /* Too large for the ring: patch the big buffer directly. */
            glBindBuffer(GL_ARRAY_BUFFER, r->gl_memory_buffer);
            glBufferSubData(GL_ARRAY_BUFFER, a, e - a, d->vram_ptr + a);
            return 0;
        }
    }

    void *dst = glMapBufferRange(GL_ARRAY_BUFFER, ofs, len,
                                 GL_MAP_WRITE_BIT |
                                 GL_MAP_INVALIDATE_RANGE_BIT |
                                 GL_MAP_UNSYNCHRONIZED_BIT);
    if (dst) {
        memcpy(dst, d->vram_ptr + start, len);
        glUnmapBuffer(GL_ARRAY_BUFFER);
    } else {
        glBufferSubData(GL_ARRAY_BUFFER, ofs, len, d->vram_ptr + start);
    }
    xog_ring_head = ofs + len;
    *attrib_ofs = ofs - prefix;
    return xog_ring_buffer;
}
#else
void pgraph_gl_android_flush_vertex_uploads(NV2AState *d)
{
}
#endif

void pgraph_gl_bind_vertex_attributes('''

sub(V, "\nvoid pgraph_gl_bind_vertex_attributes(", STREAM_CODE)

sub(V, '''            start = attrib_data_addr + min_element * stride;
            update_memory_buffer(d, start, num_elements * stride,
                                        updated_memory_buffer);
            updated_memory_buffer = true;''', '''            start = attrib_data_addr + min_element * stride;
#ifdef __ANDROID__
            if (xog_stream_on()) {
                hwaddr elem_size = attr->size * attr->count;
                hwaddr len = (hwaddr)(num_elements - 1) * stride +
                             MAX((hwaddr)stride, elem_size);
                hwaddr ofs = 0;
                if (xog_stream_attr(d, start, len,
                                    (hwaddr)min_element * stride, &ofs)) {
                    attrib_data_addr = ofs;
                } else {
                    glBindBuffer(GL_ARRAY_BUFFER, r->gl_memory_buffer);
                }
            } else
#endif
            {
                update_memory_buffer(d, start, num_elements * stride,
                                     updated_memory_buffer);
                updated_memory_buffer = true;
            }''')

sub(V, '''void pgraph_gl_update_entire_memory_buffer(NV2AState *d)
{
    PGRAPHState *pg = &d->pgraph;
    PGRAPHGLState *r = pg->gl_renderer_state;
''', '''void pgraph_gl_update_entire_memory_buffer(NV2AState *d)
{
    PGRAPHState *pg = &d->pgraph;
    PGRAPHGLState *r = pg->gl_renderer_state;
#ifdef __ANDROID__
    xog_num_pending = 0;
    xog_pending_overflow = false;
#endif
''')
# the static state is defined further down; forward-declare it
sub(V, "static void update_memory_buffer(NV2AState *d, hwaddr addr, hwaddr size,",
    '''#ifdef __ANDROID__
static int xog_num_pending;
static bool xog_pending_overflow;
#endif

static void update_memory_buffer(NV2AState *d, hwaddr addr, hwaddr size,''')
s = open(V, encoding="utf-8").read()
s = s.replace('''static struct { hwaddr a, e; } xog_pending[XOG_MAX_PENDING];
static int xog_num_pending;
static bool xog_pending_overflow;''', '''static struct { hwaddr a, e; } xog_pending[XOG_MAX_PENDING];''')
open(V, "w", encoding="utf-8").write(s)

sub("hw/xbox/nv2a/pgraph/gl/renderer.h",
    "void pgraph_gl_update_entire_memory_buffer(NV2AState *d);",
    "void pgraph_gl_update_entire_memory_buffer(NV2AState *d);\n"
    "void pgraph_gl_android_flush_vertex_uploads(NV2AState *d);")

sub("hw/xbox/nv2a/pgraph/gl/display.c", '''        android_display_restore_gl_state(&state);
        gl_fence();
        GL_ASSERT_NO_ERROR("pgraph_gl_sync: render finish");''', '''        android_display_restore_gl_state(&state);
        gl_fence();
        GL_ASSERT_NO_ERROR("pgraph_gl_sync: render finish");
        /* GPU idle now: bring the VRAM mirror up to date without ghosting */
        pgraph_gl_android_flush_vertex_uploads(d);''')
print("gles_stream_vbo: streaming vertex uploads for Mali")
