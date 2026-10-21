/* Minimal bionic (arm64) stdio declarations for the NDK-less C bridge. */
#ifndef XOG_MIN_STDIO_H
#define XOG_MIN_STDIO_H
#include <stddef.h>
#include <stdarg.h>
typedef struct __sFILE FILE;
extern FILE *stdout; /* exported by bionic since API 23 */
extern FILE *stderr;
#define _IOFBF 0
#define _IOLBF 1
#define _IONBF 2
int setvbuf(FILE *fp, char *buf, int mode, size_t size);
int snprintf(char *buf, size_t size, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
int vsnprintf(char *buf, size_t size, const char *fmt, va_list ap);
#endif
