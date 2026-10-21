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
sub("android/app/build.gradle.kts", 'versionCode = 5', 'versionCode = 9')
sub("android/app/build.gradle.kts", 'versionName = "0.3.1"', 'versionName = "0.3.1-h2mtk5"')
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

# Fill the whole screen, including the camera cut-out, and move existing
# installs to the stretched (full width + height) picture once.
sub(J + "MainActivity.kt", """  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    val prefs = getSharedPreferences("x1box_prefs", MODE_PRIVATE)""", """  override fun onCreate(savedInstanceState: Bundle?) {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
      window.attributes = window.attributes.apply {
        layoutInDisplayCutoutMode =
          android.view.WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
      }
    } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
      window.attributes = window.attributes.apply {
        layoutInDisplayCutoutMode =
          android.view.WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
      }
    }
    getSharedPreferences("x1box_prefs", MODE_PRIVATE).let { p ->
      if (!p.getBoolean("h2mtk_fullscreen_v1", false)) {
        p.edit().putString("aspect_ratio", "fit")
          .remove(PerGameSettingsManager.runtimeKey("aspect_ratio"))
          .putBoolean("h2mtk_fullscreen_v1", true).apply()
      }
    }
    super.onCreate(savedInstanceState)
    val prefs = getSharedPreferences("x1box_prefs", MODE_PRIVATE)""")

# Original-Xbox style theme: black with glowing green, always dark.
XBOX_COLORS = """<resources>
  <color name="xemu_black">#050A04</color>
  <color name="xemu_surface">#0D1A0A</color>
  <color name="xemu_surface_variant">#15290F</color>
  <color name="xemu_text">#E4FFD6</color>
  <color name="xemu_text_muted">#8DBA78</color>
  <color name="xemu_green">#7AC943</color>
  <color name="xemu_green_dark">#3E8E1C</color>
  <color name="xemu_outline">#2F5A1F</color>
  <color name="xemu_outline_variant">#1F3D15</color>
</resources>
"""
for d in ("values", "values-night"):
    import glob, re
    for f in glob.glob("android/app/src/main/res/%s/*.xml" % d):
        t = open(f, encoding="utf-8").read()
        if '<color name="xemu_black">' in t:
            open(f, "w", encoding="utf-8").write(XBOX_COLORS)
            print("ok xbox colors", f)
        if 'name="Theme.Xemu"' in t:
            t = re.sub(r'<item name="android:statusBarColor">[^<]*</item>',
                       '<item name="android:statusBarColor">#000000</item>', t)
            t = re.sub(r'<item name="android:navigationBarColor">[^<]*</item>',
                       '<item name="android:navigationBarColor">#000000</item>', t)
            t = t.replace('<item name="android:windowLightStatusBar">true</item>',
                          '<item name="android:windowLightStatusBar">false</item>')
            t = t.replace('<item name="colorOnPrimary">#FFFFFF</item>',
                          '<item name="colorOnPrimary">#061004</item>')
            t = t.replace('<item name="colorOnPrimaryContainer">#FFFFFF</item>',
                          '<item name="colorOnPrimaryContainer">#061004</item>')
            t = t.replace('<item name="colorOnSecondary">#FFFFFF</item>',
                          '<item name="colorOnSecondary">#061004</item>')
            t = t.replace('parent="Theme.Material3.DayNight.NoActionBar"',
                          'parent="Theme.Material3.Dark.NoActionBar"')
            open(f, "w", encoding="utf-8").write(t)
            print("ok xbox theme", f)

# Original launcher icon (green orb + energy ring)
import subprocess, os
subprocess.check_call([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "make_icon.py"),
                       "android/app/src/main/res"])
print("ok icon")
print("hakuX MTK patch applied")
