#!/usr/bin/env python3
"""hakuX H2 MTK: sharp upscaling + external controller on port 1.

1. Sharpness at no real cost: the final present (640x480 -> screen) samples
   bilinearly and applies a contrast-adaptive sharpening pass (CAS-style,
   5 taps per screen pixel). Graphics -> Filtering gains a "Sharp" option,
   which is the new default. XEMU_ANDROID_SHARPEN=<0..1> overrides strength.
2. External Xbox/Bluetooth/USB controller: the touch controller is a virtual
   SDL pad that grabs Xbox port 1, so a real controller ended up on port 2
   (player 2 for the game).  A physical pad now takes port 1 (the virtual pad
   is unbound) and the virtual pad returns to port 1 when it is removed.
"""
import sys

J = "android/app/src/main/java/com/rfandango/haku_x/"


def sub(path, old, new, count=1):
    s = open(path, encoding="utf-8").read()
    if s.count(old) != count:
        sys.exit("hakux_sharp_pad: %r found %d times in %s" % (old[:70], s.count(old), path))
    open(path, "w", encoding="utf-8").write(s.replace(old, new))
    print("ok %-28s %s" % (path.split("/")[-1], old.strip().splitlines()[0][:60]))


X = "ui/xemu.c"
# --- sharpening present shader
sub(X, '''                     "in vec2 v_uv;\\n"
                     "uniform sampler2D u_tex;\\n"
                     "out vec4 out_Color;\\n"
                     "void main() {\\n"
                     "  vec4 c = texture(u_tex, v_uv);\\n"
                     "  out_Color = vec4(c.rgb, 1.0);\\n"
                     "}\\n";''', '''                     "in vec2 v_uv;\\n"
                     "uniform sampler2D u_tex;\\n"
                     "uniform vec2 u_texel;\\n"
                     "uniform float u_sharp;\\n"
                     "out vec4 out_Color;\\n"
                     "void main() {\\n"
                     "  vec3 c = texture(u_tex, v_uv).rgb;\\n"
                     "  if (u_sharp > 0.0) {\\n"
                     "    vec3 n = texture(u_tex, v_uv + vec2(0.0, -u_texel.y)).rgb;\\n"
                     "    vec3 s = texture(u_tex, v_uv + vec2(0.0,  u_texel.y)).rgb;\\n"
                     "    vec3 e = texture(u_tex, v_uv + vec2( u_texel.x, 0.0)).rgb;\\n"
                     "    vec3 w = texture(u_tex, v_uv + vec2(-u_texel.x, 0.0)).rgb;\\n"
                     "    vec3 mn = min(c, min(min(n, s), min(e, w)));\\n"
                     "    vec3 mx = max(c, max(max(n, s), max(e, w)));\\n"
                     "    vec3 amp = sqrt(clamp(min(mn, 1.0 - mx) / max(mx, vec3(1e-4)), 0.0, 1.0));\\n"
                     "    vec3 wgt = amp * (-1.0 / mix(8.0, 4.5, u_sharp));\\n"
                     "    c = clamp((c + (n + s + e + w) * wgt) / (1.0 + 4.0 * wgt), 0.0, 1.0);\\n"
                     "  }\\n"
                     "  out_Color = vec4(c, 1.0);\\n"
                     "}\\n";''')
sub(X, '''    g_android_blit_scale_loc = glGetUniformLocation(prog, "u_scale");''',
    '''    g_android_blit_scale_loc = glGetUniformLocation(prog, "u_scale");
    g_android_blit_texel_loc = glGetUniformLocation(prog, "u_texel");
    g_android_blit_sharp_loc = glGetUniformLocation(prog, "u_sharp");''')
sub(X, "static bool android_blit_init(void)\n{", '''static GLint g_android_blit_texel_loc = -1;
static GLint g_android_blit_sharp_loc = -1;

static float android_sharpen_strength(void)
{
    static float v = -1.0f;
    if (v < 0.0f) {
        const char *e = getenv("XEMU_ANDROID_SHARPEN");
        v = e ? (float)atof(e) : 0.0f;
        if (v < 0.0f) v = 0.0f;
        if (v > 1.0f) v = 1.0f;
        __android_log_print(ANDROID_LOG_INFO, "hakuX", "present sharpening %.2f", v);
    }
    return v;
}

static bool android_blit_init(void)
{''')
sub(X, '''    glUniform2f(g_android_blit_scale_loc, scale_x, scale_y);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, tex);''', '''    glUniform2f(g_android_blit_scale_loc, scale_x, scale_y);
    glUniform2f(g_android_blit_texel_loc, 1.0f / (float)tw, 1.0f / (float)th);
    glUniform1f(g_android_blit_sharp_loc, android_sharpen_strength());
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, tex);
    if (android_sharpen_strength() > 0.0f) {
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    }''')

# --- settings: "Sharp" filtering option, default
sub(J + "SettingsActivity.kt", '''    setupStringPicker(R.id.btn_filtering, "filtering", "nearest",
      R.string.settings_filtering, arrayOf("Nearest", "Linear"), arrayOf("nearest", "linear"))''',
    '''    setupStringPicker(R.id.btn_filtering, "filtering", "sharp",
      R.string.settings_filtering, arrayOf("Nítido (recomendado)", "Lineal", "Nearest"), arrayOf("sharp", "linear", "nearest"))''')
sub(J + "SettingsActivity.kt", '"filtering" to "nearest"', '"filtering" to "sharp"')
sub(J + "PerGameSettingsActivity.kt", 'globalValue("filtering", "linear", mapOf("linear" to "Linear", "nearest" to "Nearest")))',
    'globalValue("filtering", "sharp", mapOf("sharp" to "Nítido", "linear" to "Linear", "nearest" to "Nearest")))')
C = "android/app/src/main/cpp/xemu_android.cpp"
sub(C, '''  if (!filterPref.empty()) ds.filtering = filterPref;''', '''  if (filterPref.empty() || filterPref == "sharp") {
    setenv("XEMU_ANDROID_SHARPEN", "0.6", 0);
    ds.filtering = "linear";
  } else {
    setenv("XEMU_ANDROID_SHARPEN", "0", 0);
    ds.filtering = filterPref;
  }''')

# --- external controller takes port 1
I = "ui/xemu-input.c"
sub(I, '''        QTAILQ_INSERT_TAIL(&available_controllers, new_con, entry);
        xemu_input_bindings_reload_map(new_con);
''', '''        QTAILQ_INSERT_TAIL(&available_controllers, new_con, entry);
        xemu_input_bindings_reload_map(new_con);

#ifdef __ANDROID__
        /* A physical pad replaces the on-screen controller on port 1. */
        const char *jname = SDL_JoystickName(new_con->sdl_joystick);
        if (!(jname && strcmp(jname, "On-Screen Controller") == 0)) {
            ControllerState *p0 = xemu_input_get_bound(0);
            if (p0 && p0 != new_con) {
                xemu_input_bind(0, NULL, 0);
            }
            xemu_input_bind(0, new_con, 0);
            char buf[160];
            snprintf(buf, sizeof(buf), "Mando '%s' en el puerto 1", new_con->name);
            xemu_queue_notification(buf);
            fprintf(stderr, "input: physical controller '%s' bound to port 1\\n",
                    new_con->name);
            xemu_input_rebind_xmu(0);
            return;
        }
        if (xemu_input_get_bound(0)) {
            /* physical pad already on port 1: keep the virtual one unbound */
            return;
        }
#endif
''')
sub(I, '''        if (!handled) {
            DPRINTF("Could not find handle for joystick instance\\n");
        }''', '''        if (!handled) {
            DPRINTF("Could not find handle for joystick instance\\n");
        }
#ifdef __ANDROID__
        /* Physical pad gone: give port 1 back to the on-screen controller. */
        if (!xemu_input_get_bound(0)) {
            ControllerState *c;
            QTAILQ_FOREACH(c, &available_controllers, entry) {
                const char *cn = c->sdl_joystick ? SDL_JoystickName(c->sdl_joystick) : NULL;
                if (c->type == INPUT_DEVICE_SDL_GAMECONTROLLER && cn &&
                    strcmp(cn, "On-Screen Controller") == 0) {
                    if (c->bound >= 0) {
                        xemu_input_bind(c->bound, NULL, 0);
                    }
                    xemu_input_bind(0, c, 0);
                    xemu_input_rebind_xmu(0);
                    break;
                }
            }
        }
#endif''')
print("hakux_sharp_pad applied")

# --- USB/Bluetooth pads: let Android's own input stack deliver them to SDL
# (SDL's HIDAPI path would claim the USB device itself), and log devices.
sub(J + "MainActivity.kt", '''    SDLActivity.nativeSetenv("SDL_ANDROID_TRAP_BACK_BUTTON", "1")''',
    '''    SDLActivity.nativeSetenv("SDL_ANDROID_TRAP_BACK_BUTTON", "1")
    SDLActivity.nativeSetenv("SDL_JOYSTICK_HIDAPI", "0")''')
sub(J + "MainActivity.kt", '''  private fun checkForPhysicalControllers() {
    val deviceIds = inputManager?.inputDeviceIds ?: return''', '''  private fun checkForPhysicalControllers() {
    val deviceIds = inputManager?.inputDeviceIds ?: return
    for (id in deviceIds) {
      val dev = inputManager?.getInputDevice(id) ?: continue
      android.util.Log.i("hakuX", "input device id=$id name='${dev.name}' " +
        "vendor=0x${Integer.toHexString(dev.vendorId)} product=0x${Integer.toHexString(dev.productId)} " +
        "sources=0x${Integer.toHexString(dev.sources)} virtual=${dev.isVirtual} " +
        "controller=${isGameController(dev)}")
    }''')
sub(J + "MainActivity.kt", '''  override fun onInputDeviceAdded(deviceId: Int) {
    val device = inputManager?.getInputDevice(deviceId)''', '''  override fun onInputDeviceAdded(deviceId: Int) {
    val device = inputManager?.getInputDevice(deviceId)
    android.util.Log.i("hakuX", "input device added id=$deviceId name='${device?.name}' " +
      "sources=0x${Integer.toHexString(device?.sources ?: 0)} controller=${isGameController(device)}")''')
sub(I, '''#include "xemu-input.h"''', '''#include "xemu-input.h"
#ifdef __ANDROID__
#include <android/log.h>
#endif''')
sub(I, '''        sdl_con = SDL_GameControllerOpen(event->cdevice.which);
        if (sdl_con == NULL) {''', '''        sdl_con = SDL_GameControllerOpen(event->cdevice.which);
#ifdef __ANDROID__
        __android_log_print(ANDROID_LOG_INFO, "hakuX",
                            "input: SDL controller added index=%d name='%s' open=%s",
                            event->cdevice.which,
                            SDL_JoystickNameForIndex(event->cdevice.which) ?: "?",
                            sdl_con ? "ok" : SDL_GetError());
#endif
        if (sdl_con == NULL) {''')
s = open(I, encoding="utf-8").read()
s = s.replace('''            fprintf(stderr, "input: physical controller '%s' bound to port 1\\n",
                    new_con->name);''', '''            __android_log_print(ANDROID_LOG_INFO, "hakuX",
                                "input: physical controller '%s' bound to port 1",
                                jname ? jname : new_con->name);''')
open(I, "w", encoding="utf-8").write(s)
print("usb pad + logging applied")

# --- multi-interface pads (EasySMX & co. show up as several devices and
# switch identity): port 1 reads ALL physical pads merged, and when one
# interface goes away port 1 goes to another physical pad before falling
# back to the on-screen controller.
sub(I, "ControllerState *bound_controllers[4] = { NULL, NULL, NULL, NULL };", '''ControllerState *bound_controllers[4] = { NULL, NULL, NULL, NULL };
#ifdef __ANDROID__
static bool xog_is_virtual_pad(ControllerState *c)
{
    const char *n = (c && c->sdl_joystick) ? SDL_JoystickName(c->sdl_joystick) : NULL;
    return n && strcmp(n, "On-Screen Controller") == 0;
}
#endif''')
sub(I, '''    } else if (state->type == INPUT_DEVICE_SDL_GAMECONTROLLER) {
        xemu_input_update_sdl_controller_state(state);
    }

    state->last_input_updated_ts = qemu_clock_get_us(QEMU_CLOCK_REALTIME);''', '''    } else if (state->type == INPUT_DEVICE_SDL_GAMECONTROLLER) {
        xemu_input_update_sdl_controller_state(state);
#ifdef __ANDROID__
        if (state->bound == 0 && !xog_is_virtual_pad(state)) {
            ControllerState *o;
            QTAILQ_FOREACH(o, &available_controllers, entry) {
                if (o == state || o->type != INPUT_DEVICE_SDL_GAMECONTROLLER ||
                    o->bound > 0 || xog_is_virtual_pad(o)) {
                    continue;
                }
                xemu_input_update_sdl_controller_state(o);
                state->buttons |= o->buttons;
                for (int a = 0; a < 6; a++) {
                    if (abs(o->axis[a]) > abs(state->axis[a])) {
                        state->axis[a] = o->axis[a];
                    }
                }
            }
        }
#endif
    }

    state->last_input_updated_ts = qemu_clock_get_us(QEMU_CLOCK_REALTIME);''')
sub(I, '''        /* Physical pad gone: give port 1 back to the on-screen controller. */
        if (!xemu_input_get_bound(0)) {
            ControllerState *c;
            QTAILQ_FOREACH(c, &available_controllers, entry) {''', '''        /* Interface gone: port 1 goes to another physical pad if any,
         * otherwise back to the on-screen controller. */
        if (!xemu_input_get_bound(0)) {
            ControllerState *c;
            QTAILQ_FOREACH(c, &available_controllers, entry) {
                if (c->type == INPUT_DEVICE_SDL_GAMECONTROLLER &&
                    !xog_is_virtual_pad(c) && c->bound < 0) {
                    xemu_input_bind(0, c, 0);
                    xemu_input_rebind_xmu(0);
                    __android_log_print(ANDROID_LOG_INFO, "hakuX",
                                        "input: port 1 -> remaining pad '%s'", c->name);
                    break;
                }
            }
        }
        if (!xemu_input_get_bound(0)) {
            ControllerState *c;
            QTAILQ_FOREACH(c, &available_controllers, entry) {''')
print("multi-interface pad merge applied")

# one-time: back to native 1x scale (2x dropped Halo 2 to ~20 fps on Mali-G720)
sub(J + "MainActivity.kt", '''          .putBoolean("h2mtk_fullscreen_v1", true).apply()
      }''', '''          .putBoolean("h2mtk_fullscreen_v1", true).apply()
      }
      if (!p.getBoolean("h2mtk_scale1_v1", false)) {
        p.edit().putInt("surface_scale", 1)
          .remove(PerGameSettingsManager.runtimeKey("surface_scale"))
          .putBoolean("h2mtk_scale1_v1", true).apply()
      }''')
print("scale reset applied")
