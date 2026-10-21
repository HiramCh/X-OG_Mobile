#!/usr/bin/env python3
"""Mali/MediaTek + Halo 2 defaults for hakuX (run from the hakuX source root).

Changes only default values and the app identity; every option stays
selectable in the app's settings:
  * renderer           vulkan -> opengl  (OpenGL ES; X1 BOX found it avoids
                                          Mali Vulkan display problems)
  * skip_occlusion_queries  false -> true (no GPU readback stalls)
  * aspect_ratio       auto -> fit        (stretch to the full 19:9 screen)
  * applicationId      com.rfandango.haku_x.h2mtk (installs next to hakuX)
"""
import re
import sys

J = "android/app/src/main/java/com/rfandango/haku_x/"
CPP = "android/app/src/main/cpp/xemu_android.cpp"


def sub(path, old, new, count=1):
    s = open(path, encoding="utf-8").read()
    n = s.count(old)
    if n < count:
        sys.exit("patch failed: %r found %d times in %s (need %d)" % (old, n, path, count))
    s = s.replace(old, new)
    open(path, "w", encoding="utf-8").write(s)
    print("ok %-45s %s" % (path.split("/")[-1], old[:60]))


# identity
sub("android/app/build.gradle.kts", 'applicationId = "com.rfandango.haku_x"',
    'applicationId = "com.rfandango.haku_x.h2mtk"')
sub("android/app/build.gradle.kts", 'versionName = "0.3.1"', 'versionName = "0.3.1-h2mtk"')
sub("android/app/build.gradle.kts", 'resValue("string", "app_name", "hakuX")',
    'resValue("string", "app_name", "hakuX H2 MTK")')

# renderer default -> OpenGL ES
sub(J + "MainActivity.kt", 'prefs.getString("renderer", "vulkan") ?: "vulkan"',
    'prefs.getString("renderer", "opengl") ?: "opengl"')
sub(J + "SettingsActivity.kt", '"renderer" to "vulkan"', '"renderer" to "opengl"')
sub(J + "SettingsActivity.kt", 'setupStringPicker(R.id.btn_renderer, "renderer", "vulkan",',
    'setupStringPicker(R.id.btn_renderer, "renderer", "opengl",')
sub(J + "PerGameSettingsActivity.kt", 'globalValue("renderer", "vulkan",',
    'globalValue("renderer", "opengl",')

# skip occlusion queries by default
sub(J + "SettingsActivity.kt", '"skip_occlusion_queries" to false', '"skip_occlusion_queries" to true')
sub(J + "SettingsActivity.kt", 'setupSwitch(R.id.switch_skip_occlusion, "skip_occlusion_queries", false)',
    'setupSwitch(R.id.switch_skip_occlusion, "skip_occlusion_queries", true)')
sub(CPP, 'GetPrefBool(env, activity, "skip_occlusion_queries", false)',
    'GetPrefBool(env, activity, "skip_occlusion_queries", true)', 2)
sub(CPP, "bool skip_occlusion_queries = false;", "bool skip_occlusion_queries = true;")

# stretch to the full screen by default
sub(J + "SettingsActivity.kt", '"aspect_ratio" to "auto"', '"aspect_ratio" to "fit"')
sub(J + "SettingsActivity.kt", 'setupStringPicker(R.id.btn_aspect_ratio, "aspect_ratio", "auto",',
    'setupStringPicker(R.id.btn_aspect_ratio, "aspect_ratio", "fit",')
sub(CPP, 'std::string aspect_ratio = "auto";', 'std::string aspect_ratio = "fit";')
# Mali: finish the frame before swapping (X1 BOX "Mali Mediatek fixes",
# commit 02bcd8dd). Without it the display context can sample the NV2A
# frame before the Mali GPU has written it -> black screen with sound.
X = "ui/xemu.c"
sub(X, "static bool g_android_gl_bgra_supported = true;",
    "static bool g_android_gl_bgra_supported = true;\nstatic bool g_android_force_finish_before_swap = false;")
sub(X, """                            "GL_EXT_texture_format_BGRA8888=%s",
                            g_android_gl_bgra_supported ? "yes" : "no");
""", """                            "GL_EXT_texture_format_BGRA8888=%s",
                            g_android_gl_bgra_supported ? "yes" : "no");
        if ((vendor && strstr(vendor, "ARM")) ||
            (renderer && strstr(renderer, "Mali"))) {
            g_android_force_finish_before_swap = true;
            __android_log_print(ANDROID_LOG_INFO, "hakuX",
                                "Mali: glFinish before swap enabled");
        }
""")
sub(X, """#ifdef __ANDROID__
    glFlush();
#else
    glFinish();
#endif
    nv2a_release_framebuffer_surface();""", """#ifdef __ANDROID__
    if (g_android_force_finish_before_swap) {
        glFinish();
    } else {
        glFlush();
    }
#else
    glFinish();
#endif
    nv2a_release_framebuffer_surface();""")

# exported log: also keep warnings/errors from the GPU driver and the rest
sub(J + "HakuXApplication.kt", '"EGL:W", "Vulkan:V",',
    '"EGL:W", "Vulkan:V", "libEGL:V", "GLES:V", "mali:V", "Mali:V", "OpenGLRenderer:W", "SDL:V", "xemu-android:V",')
sub(J + "HakuXApplication.kt", '"*:S"', '"*:W"')
print("hakuX MTK patch applied")
