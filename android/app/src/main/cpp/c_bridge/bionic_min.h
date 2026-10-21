/*
 * Hand-written declarations matching the Android arm64 (bionic, API 29) ABI,
 * so the bridge builds with a stock clang and no NDK sysroot.
 */
#ifndef XOG_BIONIC_MIN_H
#define XOG_BIONIC_MIN_H
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#if !defined(__aarch64__) || !defined(__LP64__)
#error "bionic_min.h only describes the arm64 ABI"
#endif

typedef long ssize_t;
typedef unsigned int mode_t;
typedef long time_t;
struct timespec { time_t tv_sec; long tv_nsec; };

/* pthread (bionic arm64 layouts; zero-initialised statics are valid). */
typedef long pthread_t;
typedef struct { int32_t __private[10]; } pthread_mutex_t; /* 40 bytes */
typedef struct { int32_t __private[12]; } pthread_cond_t;  /* 48 bytes */
typedef struct pthread_attr_t pthread_attr_t;
#define PTHREAD_MUTEX_INITIALIZER {{0}}
#define PTHREAD_COND_INITIALIZER {{0}}
int pthread_create(pthread_t *t, const pthread_attr_t *attr, void *(*fn)(void *), void *arg);
int pthread_detach(pthread_t t);
int pthread_join(pthread_t t, void **ret);
int pthread_mutex_lock(pthread_mutex_t *m);
int pthread_mutex_unlock(pthread_mutex_t *m);
int pthread_cond_broadcast(pthread_cond_t *c);
int pthread_cond_timedwait(pthread_cond_t *c, pthread_mutex_t *m, const struct timespec *abstime);

#define CLOCK_REALTIME 0
int clock_gettime(int clock, struct timespec *ts);

#define ETIMEDOUT 110
int *__errno(void);
char *strerror(int err);

void *malloc(size_t n);
void free(void *p);
size_t strlen(const char *s);
void *memcpy(void *dst, const void *src, size_t n);
int setenv(const char *name, const char *value, int overwrite);

int pipe(int fds[2]);
int dup(int fd);
int dup2(int oldfd, int newfd);
int close(int fd);
ssize_t read(int fd, void *buf, size_t n);
int chdir(const char *path);
int mkdir(const char *path, mode_t mode);

/* fcntl / unistd (arm64 generic values) */
#define O_WRONLY 01
#define O_CREAT 0100
#define O_TRUNC 01000
#define O_APPEND 02000
#define O_CLOEXEC 02000000
int open(const char *path, int flags, ...);
ssize_t write(int fd, const void *buf, size_t n);
int rename(const char *from, const char *to);

/* threads leaving / process exit */
pthread_t pthread_self(void);
void pthread_exit(void *ret) __attribute__((noreturn));
void _exit(int status) __attribute__((noreturn));

/* signals */
typedef void (*sighandler_t)(int);
#define SIG_DFL ((sighandler_t) 0)
#define SIGILL 4
#define SIGABRT 6
#define SIGBUS 7
#define SIGFPE 8
#define SIGSEGV 11
sighandler_t signal(int sig, sighandler_t handler);
int raise(int sig);

/* libdl */
#define RTLD_NEXT ((void *) -1L)
void *dlsym(void *handle, const char *symbol);

/* liblog */
#define ANDROID_LOG_INFO 4
#define ANDROID_LOG_WARN 5
int __android_log_print(int prio, const char *tag, const char *fmt, ...) __attribute__((format(printf, 3, 4)));

/* libandroid */
typedef struct ANativeWindow ANativeWindow;
#define WINDOW_FORMAT_RGBA_8888 1
struct JNINativeInterface_;
ANativeWindow *ANativeWindow_fromSurface(const struct JNINativeInterface_ **env, void *surface);
void ANativeWindow_release(ANativeWindow *window);
int32_t ANativeWindow_setBuffersGeometry(ANativeWindow *window, int32_t w, int32_t h, int32_t format);

/* libvulkan */
int32_t vkEnumerateInstanceVersion(uint32_t *version);
#endif
