/*
 * C port of android_backend.cpp for building without the Android NDK.
 *
 * Same JNI surface and behaviour as the C++ bridge, but it only needs a
 * plain clang that targets aarch64-linux-android: every libc / Android
 * declaration it uses is spelled out in bionic_min.h with the arm64
 * bionic ABI, so no NDK sysroot or C++ runtime is required.
 *
 * The unused EGL/GLES fallback of the C++ version (init_gl_locked was never
 * called) is omitted; the Vulkan probe is kept.
 */
#include "bionic_min.h"
#include <jni.h>

#define LOG_TAG "X-OG Mobile"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)
#define LOGW(...) __android_log_print(ANDROID_LOG_WARN, LOG_TAG, __VA_ARGS__)

extern int xemu_android_main(int argc, char **argv);
extern void xemu_android_set_native_window(ANativeWindow *window);
extern void xemu_android_input_set_button(int button, _Bool pressed);
extern void xemu_android_input_set_axis(int axis, float value);
extern void xemu_android_input_reset(void);
extern void xemu_android_audio_set_volume(float volume);
extern void xemu_android_request_shutdown(void);
extern void xemu_android_request_pause(void);
extern void xemu_android_request_resume(void);
extern void xemu_android_get_frame_stats(uint64_t *produced, uint64_t *presented);
extern void xemu_android_log_watchdog_snapshot(const char *reason);

#define MAX_BUTTONS 16
#define MAX_AXES 8
#define MAX_ARGS 32
#define PATH_BUF 1024
#define DISC_FDSET_ID 100

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_core_cv = PTHREAD_COND_INITIALIZER;
static ANativeWindow *g_window;
static int g_vulkan_available;
static int g_buttons[MAX_BUTTONS];
static float g_axes[MAX_AXES];
static char g_files_dir[PATH_BUF];
static char g_cache_dir[PATH_BUF];
static char g_native_lib_dir[PATH_BUF];
static int g_disc_dup_fd = -1;
static pthread_t g_xemu_thread;
static int g_xemu_thread_valid;
static int g_xemu_running; /* accessed with __atomic builtins */
static int g_stdio_redirected;

static int g_log_fd = -1;          /* files/xemu-last-run.log while a core runs */
static int g_core_dead;            /* core hit a fatal error; process must restart */
static int g_signals_installed;

static char *g_argv_store[MAX_ARGS];
static int g_argc;

static int running(void) { return __atomic_load_n(&g_xemu_running, __ATOMIC_SEQ_CST); }
static void set_running(int v) { __atomic_store_n(&g_xemu_running, v, __ATOMIC_SEQ_CST); }

static void jstr(JNIEnv *env, jstring value, char *out, size_t cap) {
    out[0] = 0;
    if (!value) {
        return;
    }
    const char *chars = (*env)->GetStringUTFChars(env, value, NULL);
    if (chars) {
        snprintf(out, cap, "%s", chars);
        (*env)->ReleaseStringUTFChars(env, value, chars);
    }
}

static jstring ret(JNIEnv *env, const char *value) {
    return (*env)->NewStringUTF(env, value);
}

static int probe_vulkan(void) {
    uint32_t version = 0;
    if (vkEnumerateInstanceVersion(&version) == 0 /* VK_SUCCESS */) {
        return (version >> 22) >= 1; /* VK_VERSION_MAJOR */
    }
    return 0;
}

static void *stdio_pump(void *arg) {
    int read_fd = (int) (intptr_t) arg;
    char buffer[512];
    char line[512];
    size_t len = 0;
    for (;;) {
        ssize_t count = read(read_fd, buffer, sizeof(buffer));
        if (count <= 0) {
            break;
        }
        for (ssize_t i = 0; i < count; ++i) {
            if (buffer[i] == '\n') {
                if (len) {
                    line[len] = 0;
                    LOGI("xemu: %s", line);
                    if (g_log_fd >= 0) {
                        line[len] = '\n';
                        write(g_log_fd, line, len + 1);
                    }
                    len = 0;
                }
            } else {
                line[len++] = buffer[i];
                if (len >= 480) {
                    line[len] = 0;
                    LOGI("xemu: %s", line);
                    if (g_log_fd >= 0) {
                        line[len] = '\n';
                        write(g_log_fd, line, len + 1);
                    }
                    len = 0;
                }
            }
        }
    }
    close(read_fd);
    return NULL;
}

static void start_stdio_logcat_locked(void) {
    if (g_stdio_redirected) {
        return;
    }
    int pipe_fds[2] = {-1, -1};
    if (pipe(pipe_fds) != 0) {
        LOGW("Unable to create stdout/stderr logcat pipe: %s", strerror(*__errno()));
        return;
    }
    dup2(pipe_fds[1], 1);
    dup2(pipe_fds[1], 2);
    close(pipe_fds[1]);
    setvbuf(stdout, NULL, _IOLBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
    pthread_t t;
    if (pthread_create(&t, NULL, stdio_pump, (void *) (intptr_t) pipe_fds[0]) == 0) {
        pthread_detach(t);
    }
    g_stdio_redirected = 1;
}

/* ---- Fatal-error containment ----
 *
 * The xemu core runs inside the app process, and QEMU reacts to many setup
 * problems (bad MCPX/BIOS/HDD, unreadable disc, failed asserts) with exit()
 * or abort(), which used to kill the whole app with no explanation. The
 * bridge is the root of the core's dlopen group, so the definitions below
 * interpose exit/abort/__assert2 for the core and its libraries: the reason
 * is written to the run log, the failing thread is parked with pthread_exit,
 * and the UI stays alive to show what happened. A fresh process is needed
 * before the next launch because the core's state is unrecoverable.
 */
static void fatal_note(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void fatal_note(const char *fmt, ...) {
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf) - 1, fmt, ap);
    va_end(ap);
    if (n < 0) {
        return;
    }
    if (n > (int) sizeof(buf) - 2) {
        n = (int) sizeof(buf) - 2;
    }
    LOGW("FATAL: %s", buf);
    if (g_log_fd >= 0) {
        static const char prefix[] = "[X-OG FATAL] ";
        write(g_log_fd, prefix, sizeof(prefix) - 1);
        buf[n] = '\n';
        write(g_log_fd, buf, (size_t) n + 1);
    }
}

static void park_failed_thread(void) __attribute__((noreturn));
static void park_failed_thread(void) {
    pthread_t self = pthread_self();
    g_core_dead = 1;
    if (g_xemu_thread_valid) {
        /* Never join the core thread from Java after this: it may be stuck. */
        if (self != g_xemu_thread) {
            pthread_detach(g_xemu_thread);
        }
        g_xemu_thread_valid = 0;
    }
    pthread_detach(self);
    set_running(0);
    pthread_cond_broadcast(&g_core_cv);
    pthread_exit(NULL);
}

static int core_active(void) {
    return running() || g_xemu_thread_valid;
}

__attribute__((visibility("default"), noreturn))
void exit(int status) {
    if (core_active()) {
        fatal_note("xemu core called exit(%d); see the messages above for the reason", status);
        park_failed_thread();
    }
    void (*real_exit)(int) = (void (*)(int)) dlsym(RTLD_NEXT, "exit");
    if (real_exit) {
        real_exit(status);
    }
    _exit(status);
}

__attribute__((visibility("default"), noreturn))
void abort(void) {
    if (core_active()) {
        fatal_note("xemu core called abort()");
        park_failed_thread();
    }
    void (*real_abort)(void) = (void (*)(void)) dlsym(RTLD_NEXT, "abort");
    if (real_abort) {
        real_abort();
    }
    _exit(134);
}

__attribute__((visibility("default"), noreturn))
void __assert2(const char *file, int line, const char *function, const char *expr) {
    fatal_note("assertion failed: %s (%s:%d, %s)", expr ? expr : "?", file ? file : "?", line,
               function ? function : "?");
    if (core_active()) {
        park_failed_thread();
    }
    void (*real_assert)(const char *, int, const char *, const char *) =
        (void (*)(const char *, int, const char *, const char *)) dlsym(RTLD_NEXT, "__assert2");
    if (real_assert) {
        real_assert(file, line, function, expr);
    }
    _exit(134);
}

/* Hard crashes (e.g. inside the GPU driver) cannot be survived; record the
 * signal so the next app start can explain it, then let it crash normally. */
static void crash_signal_handler(int sig) {
    if (g_log_fd >= 0) {
        char msg[] = "[X-OG FATAL] fatal signal 00 in emulator process (11=SIGSEGV 7=SIGBUS 4=SIGILL 6=SIGABRT 8=SIGFPE)\n";
        msg[sizeof("[X-OG FATAL] fatal signal ") - 1] = (char) ('0' + (sig / 10) % 10);
        msg[sizeof("[X-OG FATAL] fatal signal ")] = (char) ('0' + sig % 10);
        write(g_log_fd, msg, sizeof(msg) - 1);
    }
    signal(sig, SIG_DFL);
    if (sig == SIGABRT) {
        raise(sig);
    }
}

static void install_crash_handlers(void) {
    if (g_signals_installed) {
        return;
    }
    g_signals_installed = 1;
    signal(SIGSEGV, crash_signal_handler);
    signal(SIGBUS, crash_signal_handler);
    signal(SIGILL, crash_signal_handler);
    signal(SIGFPE, crash_signal_handler);
    signal(SIGABRT, crash_signal_handler);
}

static void ensure_dir(const char *path) {
    if (path[0]) {
        mkdir(path, 0700);
    }
}

static void *xemu_thread_main(void *unused) {
    (void) unused;
    LOGI("Starting in-process xemu core");
    int status = xemu_android_main(g_argc, g_argv_store);
    LOGI("In-process xemu core exited with status %d", status);
    pthread_mutex_lock(&g_lock);
    set_running(0);
    pthread_mutex_unlock(&g_lock);
    pthread_cond_broadcast(&g_core_cv);
    return NULL;
}

/* Called with g_lock held; may drop and retake it. */
static void join_finished_core_locked(void) {
    if (!running() && g_xemu_thread_valid) {
        pthread_t finished = g_xemu_thread;
        g_xemu_thread_valid = 0;
        pthread_mutex_unlock(&g_lock);
        pthread_join(finished, NULL);
        LOGI("Joined finished xemu core thread");
        pthread_mutex_lock(&g_lock);
    }
}

static float audio_volume_to_float(int volume, int muted) {
    if (muted) {
        return 0.0f;
    }
    if (volume < 0) {
        volume = 0;
    } else if (volume > 100) {
        volume = 100;
    }
    return (float) volume / 100.0f;
}

static void free_args(void) {
    for (int i = 0; i < g_argc; i++) {
        free(g_argv_store[i]);
        g_argv_store[i] = NULL;
    }
    g_argc = 0;
}

static void push_arg(const char *s) {
    if (g_argc >= MAX_ARGS - 1) {
        return;
    }
    size_t n = strlen(s) + 1;
    char *copy = (char *) malloc(n);
    memcpy(copy, s, n);
    g_argv_store[g_argc++] = copy;
    g_argv_store[g_argc] = NULL;
}

JNIEXPORT jstring JNICALL
Java_emu_xbox_og_NativeBridge_nativeInit(JNIEnv *env, jclass cls, jstring files_dir,
                                         jstring cache_dir, jstring native_lib_dir) {
    (void) cls;
    pthread_mutex_lock(&g_lock);
    jstr(env, files_dir, g_files_dir, sizeof(g_files_dir));
    jstr(env, cache_dir, g_cache_dir, sizeof(g_cache_dir));
    jstr(env, native_lib_dir, g_native_lib_dir, sizeof(g_native_lib_dir));
    g_vulkan_available = probe_vulkan();
    start_stdio_logcat_locked();
    char out[1600];
    snprintf(out, sizeof(out),
             "Native backend ready (C bridge). Vulkan probe: %s. SDL runtime: disabled. "
             "xemu core library: %s/libxemu-core-i386.so",
             g_vulkan_available ? "available" : "unavailable", g_native_lib_dir);
    pthread_mutex_unlock(&g_lock);
    return ret(env, out);
}

JNIEXPORT void JNICALL
Java_emu_xbox_og_NativeBridge_nativeSurfaceCreated(JNIEnv *env, jclass cls, jobject surface,
                                                   jint width, jint height) {
    (void) cls;
    pthread_mutex_lock(&g_lock);
    if (g_window) {
        ANativeWindow_release(g_window);
    }
    g_window = ANativeWindow_fromSurface(env, surface);
    if (g_window) {
        ANativeWindow_setBuffersGeometry(g_window, width, height, WINDOW_FORMAT_RGBA_8888);
        xemu_android_set_native_window(g_window);
    }
    pthread_mutex_unlock(&g_lock);
}

JNIEXPORT void JNICALL
Java_emu_xbox_og_NativeBridge_nativeSurfaceChanged(JNIEnv *env, jclass cls, jobject surface,
                                                   jint width, jint height) {
    Java_emu_xbox_og_NativeBridge_nativeSurfaceCreated(env, cls, surface, width, height);
}

JNIEXPORT void JNICALL
Java_emu_xbox_og_NativeBridge_nativeSurfaceDestroyed(JNIEnv *env, jclass cls) {
    (void) env;
    (void) cls;
    pthread_mutex_lock(&g_lock);
    if (g_window) {
        xemu_android_set_native_window(NULL);
        ANativeWindow_release(g_window);
        g_window = NULL;
    }
    pthread_mutex_unlock(&g_lock);
}

JNIEXPORT jstring JNICALL
Java_emu_xbox_og_NativeBridge_nativeLaunch(JNIEnv *env, jclass cls, jstring mcpx, jstring bios,
                                           jstring hdd, jstring disc, jstring renderer,
                                           jint audio_volume, jboolean audio_muted,
                                           jboolean skip_boot_animation, jstring avpack,
                                           jint disc_fd) {
    (void) cls;
    static char mcpx_path[PATH_BUF], bios_path[PATH_BUF], hdd_path[PATH_BUF];
    static char disc_path[PATH_BUF], renderer_value[64], avpack_value[64];
    char buf[PATH_BUF + 64];

    pthread_mutex_lock(&g_lock);
    join_finished_core_locked();
    jstr(env, mcpx, mcpx_path, sizeof(mcpx_path));
    jstr(env, bios, bios_path, sizeof(bios_path));
    jstr(env, hdd, hdd_path, sizeof(hdd_path));
    jstr(env, disc, disc_path, sizeof(disc_path));
    jstr(env, renderer, renderer_value, sizeof(renderer_value));
    jstr(env, avpack, avpack_value, sizeof(avpack_value));

    if (!mcpx_path[0] || !bios_path[0] || !hdd_path[0]) {
        pthread_mutex_unlock(&g_lock);
        return ret(env, "Missing required file. MCPX, BIOS and HDD are required before launch.");
    }
    if (g_core_dead) {
        pthread_mutex_unlock(&g_lock);
        return ret(env, "The emulator core stopped after a fatal error. Close the app completely "
                        "(swipe it away from recent apps) and open it again before the next launch.");
    }
    if (running()) {
        pthread_mutex_unlock(&g_lock);
        return ret(env, "xemu core is already running in-process");
    }

    char executable[PATH_BUF + 32], config_dir[PATH_BUF + 32], data_dir[PATH_BUF + 32];
    snprintf(executable, sizeof(executable), "%s/libxemu-core-i386.so", g_native_lib_dir);
    snprintf(config_dir, sizeof(config_dir), "%s/xemu-config", g_files_dir);
    snprintf(data_dir, sizeof(data_dir), "%s/xemu-data", g_files_dir);
    ensure_dir(config_dir);
    ensure_dir(data_dir);

    /* See android_backend.cpp: hand the already-open SAF descriptor to the
     * core through QEMU "-add-fd" + "/dev/fdset/N". dup() clears CLOEXEC. */
    if (disc_fd >= 0) {
        int dup_fd = dup((int) disc_fd);
        if (dup_fd < 0) {
            snprintf(buf, sizeof(buf), "Unable to duplicate disc file descriptor: %s",
                     strerror(*__errno()));
            pthread_mutex_unlock(&g_lock);
            return ret(env, buf);
        }
        g_disc_dup_fd = dup_fd;
    } else {
        g_disc_dup_fd = -1;
    }

    {
        char log_path[PATH_BUF + 32], prev_path[PATH_BUF + 32];
        snprintf(log_path, sizeof(log_path), "%s/xemu-last-run.log", g_files_dir);
        snprintf(prev_path, sizeof(prev_path), "%s/xemu-prev-run.log", g_files_dir);
        if (g_log_fd < 0) {
            rename(log_path, prev_path);
            g_log_fd = open(log_path, O_WRONLY | O_CREAT | O_TRUNC | O_APPEND | O_CLOEXEC, 0600);
        }
        if (g_log_fd >= 0) {
            static const char hdr[] = "=== X-OG Mobile H2 core launch ===\n";
            write(g_log_fd, hdr, sizeof(hdr) - 1);
        }
        install_crash_handlers();
    }

    free_args();
    push_arg(executable);
    push_arg("-monitor");
    push_arg("none");
    push_arg("-serial");
    push_arg("none");
    push_arg("-parallel");
    push_arg("none");
    push_arg("-accel");
    push_arg("tcg");
    if (g_disc_dup_fd >= 0) {
        push_arg("-add-fd");
        snprintf(buf, sizeof(buf), "fd=%d,set=%d", g_disc_dup_fd, DISC_FDSET_ID);
        push_arg(buf);
        push_arg("-dvd_path");
        snprintf(buf, sizeof(buf), "/dev/fdset/%d", DISC_FDSET_ID);
        push_arg(buf);
        LOGI("Disc passed via duplicated fd=%d as /dev/fdset/%d", g_disc_dup_fd, DISC_FDSET_ID);
    }

    setenv("HOME", g_files_dir, 1);
    setenv("XDG_CONFIG_HOME", config_dir, 1);
    setenv("XDG_DATA_HOME", data_dir, 1);
    setenv("LD_LIBRARY_PATH", g_native_lib_dir, 1);
    setenv("XEMU_ANDROID_MCPX", mcpx_path, 1);
    setenv("XEMU_ANDROID_BIOS", bios_path, 1);
    setenv("XEMU_ANDROID_HDD", hdd_path, 1);
    setenv("XEMU_ANDROID_DISC", disc_path, 1);
    setenv("XEMU_ANDROID_RENDERER", renderer_value, 1);
    snprintf(buf, sizeof(buf), "%f", (double) audio_volume_to_float(audio_volume, 0));
    setenv("XEMU_ANDROID_AUDIO_VOLUME", buf, 1);
    setenv("XEMU_ANDROID_AUDIO_MUTED", audio_muted == JNI_TRUE ? "1" : "0", 1);
    setenv("XEMU_ANDROID_SKIP_BOOT_ANIM", skip_boot_animation == JNI_TRUE ? "1" : "0", 1);
    setenv("XEMU_ANDROID_AVPACK", avpack_value, 1);
    chdir(g_files_dir);

    xemu_android_input_reset();
    set_running(1);
    if (pthread_create(&g_xemu_thread, NULL, xemu_thread_main, NULL) != 0) {
        set_running(0);
        pthread_mutex_unlock(&g_lock);
        return ret(env, "Unable to start xemu core thread");
    }
    g_xemu_thread_valid = 1;

    char out[8192];
    snprintf(out, sizeof(out),
             "xemu core launched:"
             "\n  mode=in-process native thread"
             "\n  mcpx=%s\n  bios=%s\n  hdd=%s"
             "\n  eeprom=%s/eeprom.bin (xemu generated/default)"
             "\n  disc=%s"
             "\nRenderer setting: %s"
             "\nAudio volume: %d%s"
             "\nAV pack: %s"
             "\nSkip boot animation: %s"
             "\nRenderer path: %s"
             "\nCurrent core renderer: Android Vulkan presenter registered inside xemu.",
             mcpx_path, bios_path, hdd_path, config_dir,
             disc_path[0] ? disc_path : "missing",
             renderer_value, (int) audio_volume, audio_muted == JNI_TRUE ? " (muted)" : "",
             avpack_value, skip_boot_animation == JNI_TRUE ? "yes" : "no",
             g_vulkan_available ? "Vulkan preferred" : "Vulkan unavailable");
    LOGI("%s", out);
    pthread_mutex_unlock(&g_lock);
    return ret(env, out);
}

JNIEXPORT void JNICALL
Java_emu_xbox_og_NativeBridge_nativeSetButton(JNIEnv *env, jclass cls, jint button, jboolean pressed) {
    (void) env;
    (void) cls;
    if (button >= 0 && button < MAX_BUTTONS) {
        g_buttons[button] = pressed == JNI_TRUE;
        xemu_android_input_set_button(button, pressed == JNI_TRUE);
    }
}

JNIEXPORT void JNICALL
Java_emu_xbox_og_NativeBridge_nativeSetAxis(JNIEnv *env, jclass cls, jint axis, jfloat value) {
    (void) env;
    (void) cls;
    if (axis >= 0 && axis < MAX_AXES) {
        g_axes[axis] = value;
        xemu_android_input_set_axis(axis, value);
    }
}

JNIEXPORT void JNICALL
Java_emu_xbox_og_NativeBridge_nativeSetAudio(JNIEnv *env, jclass cls, jint volume, jboolean muted) {
    (void) env;
    (void) cls;
    if (running()) {
        xemu_android_audio_set_volume(audio_volume_to_float(volume, muted == JNI_TRUE));
    }
}

JNIEXPORT jstring JNICALL
Java_emu_xbox_og_NativeBridge_nativePause(JNIEnv *env, jclass cls) {
    (void) cls;
    if (!running()) {
        return ret(env, "xemu core is not running");
    }
    xemu_android_request_pause();
    return ret(env, "Pause requested");
}

JNIEXPORT jstring JNICALL
Java_emu_xbox_og_NativeBridge_nativeResume(JNIEnv *env, jclass cls) {
    (void) cls;
    if (!running()) {
        return ret(env, "xemu core is not running");
    }
    xemu_android_request_resume();
    return ret(env, "Resume requested");
}

JNIEXPORT jstring JNICALL
Java_emu_xbox_og_NativeBridge_nativeStop(JNIEnv *env, jclass cls, jint timeout_ms) {
    (void) cls;
    pthread_mutex_lock(&g_lock);
    join_finished_core_locked();
    int requested = 0;
    int stopped = !running();
    if (!stopped) {
        LOGI("nativeStop requested; asking xemu core to shut down");
        xemu_android_request_shutdown();
        requested = 1;
        struct timespec deadline;
        clock_gettime(CLOCK_REALTIME, &deadline);
        long ms = timeout_ms > 0 ? timeout_ms : 3000;
        deadline.tv_sec += ms / 1000;
        deadline.tv_nsec += (ms % 1000) * 1000000L;
        if (deadline.tv_nsec >= 1000000000L) {
            deadline.tv_sec += 1;
            deadline.tv_nsec -= 1000000000L;
        }
        while (running()) {
            if (pthread_cond_timedwait(&g_core_cv, &g_lock, &deadline) == ETIMEDOUT) {
                break;
            }
        }
        stopped = !running();
    }
    xemu_android_input_reset();
    if (g_window) {
        xemu_android_set_native_window(NULL);
        ANativeWindow_release(g_window);
        g_window = NULL;
    }
    int join = 0;
    pthread_t finished = 0;
    if (!running() && g_xemu_thread_valid) {
        finished = g_xemu_thread;
        g_xemu_thread_valid = 0;
        join = 1;
    }
    pthread_mutex_unlock(&g_lock);
    if (join) {
        pthread_join(finished, NULL);
        LOGI("Joined xemu core thread after stop");
    }
    if (stopped) {
        return ret(env, requested ? "xemu core stopped cleanly" : "xemu core was not running");
    }
    LOGW("xemu core shutdown still pending after timeout");
    return ret(env, "xemu core shutdown pending after timeout");
}

JNIEXPORT jboolean JNICALL
Java_emu_xbox_og_NativeBridge_nativeIsRunning(JNIEnv *env, jclass cls) {
    (void) env;
    (void) cls;
    return running() ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jboolean JNICALL
Java_emu_xbox_og_NativeBridge_nativeIsCoreDead(JNIEnv *env, jclass cls) {
    (void) env;
    (void) cls;
    return g_core_dead ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jlongArray JNICALL
Java_emu_xbox_og_NativeBridge_nativeGetFrameStats(JNIEnv *env, jclass cls) {
    (void) cls;
    uint64_t produced = 0;
    uint64_t presented = 0;
    xemu_android_get_frame_stats(&produced, &presented);
    jlongArray result = (*env)->NewLongArray(env, 2);
    jlong values[2] = {(jlong) produced, (jlong) presented};
    (*env)->SetLongArrayRegion(env, result, 0, 2, values);
    return result;
}

JNIEXPORT void JNICALL
Java_emu_xbox_og_NativeBridge_nativeLogWatchdogSnapshot(JNIEnv *env, jclass cls, jstring reason) {
    (void) cls;
    char r[512];
    jstr(env, reason, r, sizeof(r));
    xemu_android_log_watchdog_snapshot(r);
}
