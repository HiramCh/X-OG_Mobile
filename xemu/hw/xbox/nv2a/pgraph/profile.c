/*
 * QEMU Geforce NV2A profiling helpers
 *
 * Copyright (c) 2020-2024 Matt Borgerson
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with this library; if not, see <http://www.gnu.org/licenses/>.
 */

#include "hw/xbox/nv2a/nv2a_int.h"

NV2AStats g_nv2a_stats;

void nv2a_profile_increment(void)
{
    int64_t now = qemu_clock_get_us(QEMU_CLOCK_REALTIME);
    const int64_t fps_update_interval = 250000;
    g_nv2a_stats.last_flip_time = now;

    static int64_t frame_count = 0;
    frame_count++;

    static int64_t ts = 0;
    int64_t delta = now - ts;
    if (delta >= fps_update_interval) {
        g_nv2a_stats.increment_fps = frame_count * 1000000 / delta;
        ts = now;
        frame_count = 0;
    }
}

/*
 * XEMU_ANDROID_PROFILE=1: once per second (and for every frame slower than
 * 500 ms) print the non-zero NV2A counters with a CLOCK_BOOTTIME stamp, the
 * same clock as the kernel GPU fault lines ("[21402.685] ... GROUP_ERROR"),
 * so faults can be matched with the work that preceded them.
 */
static void android_profile_frame(int64_t render_time_ms)
{
    static int enabled = -1;
    static int sum[NV2A_PROF__COUNT];
    static int frames, max_ms;
    static double window_start;

    if (enabled < 0) {
        const char *v = getenv("XEMU_ANDROID_PROFILE");
        enabled = v && v[0] == '1';
    }
    if (!enabled) {
        return;
    }

    struct timespec ts;
    clock_gettime(CLOCK_BOOTTIME, &ts);
    double boot = ts.tv_sec + ts.tv_nsec / 1e9;
    if (window_start == 0) {
        window_start = boot;
    }

    const int *c = g_nv2a_stats.frame_working.counters;
    if (render_time_ms > 500) {
        char buf[1024];
        int n = snprintf(buf, sizeof(buf), "[prof %.3f] SLOW frame %lldms:",
                         boot, (long long)render_time_ms);
        for (int i = 0; i < NV2A_PROF__COUNT && n < (int)sizeof(buf) - 40; i++) {
            if (c[i]) {
                n += snprintf(buf + n, sizeof(buf) - n, " %s=%d",
                              nv2a_profile_get_counter_name(i), c[i]);
            }
        }
        fprintf(stderr, "%s\n", buf);
    }

    for (int i = 0; i < NV2A_PROF__COUNT; i++) {
        sum[i] += c[i];
    }
    frames++;
    if (render_time_ms > max_ms) {
        max_ms = render_time_ms;
    }

    if (boot - window_start >= 1.0) {
        char buf[1024];
        int n = snprintf(buf, sizeof(buf), "[prof %.3f] fps=%.1f max_ms=%d:",
                         boot, frames / (boot - window_start), max_ms);
        for (int i = 0; i < NV2A_PROF__COUNT && n < (int)sizeof(buf) - 40; i++) {
            if (sum[i]) {
                n += snprintf(buf + n, sizeof(buf) - n, " %s=%d",
                              nv2a_profile_get_counter_name(i), sum[i]);
            }
        }
        fprintf(stderr, "%s\n", buf);
        memset(sum, 0, sizeof(sum));
        frames = 0;
        max_ms = 0;
        window_start = boot;
    }
}

void nv2a_profile_flip_stall(void)
{
    int64_t now = qemu_clock_get_us(QEMU_CLOCK_REALTIME);
    int64_t render_time = (now-g_nv2a_stats.last_flip_time)/1000;

    android_profile_frame(render_time);

    g_nv2a_stats.frame_working.mspf = render_time;
    g_nv2a_stats.frame_history[g_nv2a_stats.frame_ptr] =
        g_nv2a_stats.frame_working;
    g_nv2a_stats.frame_ptr =
        (g_nv2a_stats.frame_ptr + 1) % NV2A_PROF_NUM_FRAMES;
    g_nv2a_stats.frame_count++;
    memset(&g_nv2a_stats.frame_working, 0, sizeof(g_nv2a_stats.frame_working));
}

const char *nv2a_profile_get_counter_name(unsigned int cnt)
{
    const char *default_names[NV2A_PROF__COUNT] = {
        #define _X(x) stringify(x),
        NV2A_PROF_COUNTERS_XMAC
        #undef _X
    };

    assert(cnt < NV2A_PROF__COUNT);
    return default_names[cnt] + 10; /* 'NV2A_PROF_' */
}

int nv2a_profile_get_counter_value(unsigned int cnt)
{
    assert(cnt < NV2A_PROF__COUNT);
    unsigned int idx = (g_nv2a_stats.frame_ptr + NV2A_PROF_NUM_FRAMES - 1) %
                       NV2A_PROF_NUM_FRAMES;
    return g_nv2a_stats.frame_history[idx].counters[cnt];
}
