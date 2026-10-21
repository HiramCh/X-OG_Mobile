package emu.xbox.og;

import android.content.Context;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.view.Choreographer;
import android.view.Surface;

/**
 * FPS-style aiming for games like Halo 2, which only understand an analog
 * right stick. Two sources are converted into right-stick deflection every
 * display frame:
 *
 * <ul>
 *   <li><b>Touch look</b>: a finger dragged on free screen space. Finger
 *   speed (dp/s) maps to stick deflection, so the camera turns while the
 *   finger moves and stops when it stops, much like a mouse.</li>
 *   <li><b>Gyro</b>: the device's angular velocity (rad/s), looking "through"
 *   the phone in landscape.</li>
 * </ul>
 *
 * Halo's inner dead zone swallows small stick values, so any non-zero output
 * is lifted by {@link #antiDeadzone} first. Nothing is sent while the
 * on-screen right stick is held, so the two never fight.
 */
final class AimController implements SensorEventListener, Choreographer.FrameCallback {
    interface Output {
        void onAimAxis(int axis, float value);
    }

    private static final float GYRO_NOISE_RAD_S = 0.015f;
    private static final float TOUCH_SPEED_FULL_DP_S = 900f;
    private static final float GYRO_RATE_FULL_RAD_S = 2.2f;
    private static final float SMOOTHING = 0.45f;
    private static final float EPSILON = 0.002f;

    private final Output output;
    private final SensorManager sensorManager;
    private final Sensor gyro;
    private final float density;

    private boolean touchEnabled = true;
    private boolean gyroEnabled;
    private boolean invertY;
    private float sensitivity = 1.0f;
    private float antiDeadzone = 0.18f;
    private int displayRotation = Surface.ROTATION_90;

    private boolean running;
    private boolean manualRightStick;
    private boolean gyroRegistered;

    // Touch accumulation, written from the UI thread between frames.
    private float pendingTouchDx;
    private float pendingTouchDy;
    private long lastFrameNanos;

    // Latest gyro sample in screen space (rad/s), x = look right, y = look up.
    private volatile float gyroYaw;
    private volatile float gyroPitch;

    private float outX;
    private float outY;
    private boolean lastSentZero = true;

    AimController(Context context, Output output) {
        this.output = output;
        this.sensorManager = (SensorManager) context.getSystemService(Context.SENSOR_SERVICE);
        this.gyro = sensorManager != null ? sensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE) : null;
        this.density = context.getResources().getDisplayMetrics().density;
    }

    boolean hasGyro() {
        return gyro != null;
    }

    void configure(boolean touchEnabled, boolean gyroEnabled, boolean invertY,
                   float sensitivity, float antiDeadzone) {
        this.touchEnabled = touchEnabled;
        this.gyroEnabled = gyroEnabled && gyro != null;
        this.invertY = invertY;
        this.sensitivity = Math.max(0.1f, sensitivity);
        this.antiDeadzone = Math.max(0f, Math.min(0.5f, antiDeadzone));
        updateGyroRegistration();
    }

    boolean isTouchEnabled() {
        return touchEnabled;
    }

    void setDisplayRotation(int rotation) {
        displayRotation = rotation;
    }

    /** Called while the game is actually running (not paused / backgrounded). */
    void setRunning(boolean running) {
        if (this.running == running) {
            return;
        }
        this.running = running;
        pendingTouchDx = 0;
        pendingTouchDy = 0;
        lastFrameNanos = 0;
        updateGyroRegistration();
        Choreographer choreographer = Choreographer.getInstance();
        choreographer.removeFrameCallback(this);
        if (running) {
            choreographer.postFrameCallback(this);
        } else {
            sendZero();
        }
    }

    /** The on-screen right stick is held: it owns the right stick axes. */
    void setManualRightStick(boolean held) {
        manualRightStick = held;
        if (held) {
            outX = 0;
            outY = 0;
            lastSentZero = true;
        }
    }

    /** Raw finger movement in pixels (screen y grows downward). */
    void addTouchDelta(float dxPx, float dyPx) {
        if (!touchEnabled) {
            return;
        }
        pendingTouchDx += dxPx;
        pendingTouchDy += dyPx;
    }

    void releaseTouch() {
        pendingTouchDx = 0;
        pendingTouchDy = 0;
    }

    @Override
    public void doFrame(long frameTimeNanos) {
        if (!running) {
            return;
        }
        Choreographer.getInstance().postFrameCallback(this);
        if (lastFrameNanos == 0) {
            lastFrameNanos = frameTimeNanos;
            return;
        }
        float dt = (frameTimeNanos - lastFrameNanos) / 1_000_000_000f;
        lastFrameNanos = frameTimeNanos;
        if (dt <= 0f || dt > 0.25f) {
            pendingTouchDx = 0;
            pendingTouchDy = 0;
            return;
        }

        float targetX = 0f;
        float targetY = 0f;

        if (touchEnabled) {
            float vx = (pendingTouchDx / density) / dt;
            float vy = (pendingTouchDy / density) / dt;
            targetX += vx / TOUCH_SPEED_FULL_DP_S;
            // Screen y grows downward; xemu stick y is positive when pushed up.
            targetY += -vy / TOUCH_SPEED_FULL_DP_S;
        }
        pendingTouchDx = 0;
        pendingTouchDy = 0;

        if (gyroEnabled) {
            targetX += gyroYaw / GYRO_RATE_FULL_RAD_S;
            targetY += gyroPitch / GYRO_RATE_FULL_RAD_S;
        }

        targetX *= sensitivity;
        targetY *= sensitivity;
        if (invertY) {
            targetY = -targetY;
        }

        outX += (targetX - outX) * (1f - SMOOTHING);
        outY += (targetY - outY) * (1f - SMOOTHING);

        if (manualRightStick) {
            return;
        }
        float sx = shape(outX);
        float sy = shape(outY);
        boolean zero = sx == 0f && sy == 0f;
        if (zero && lastSentZero) {
            return;
        }
        output.onAimAxis(AndroidInputMapper.AXIS_RSTICK_X, sx);
        output.onAimAxis(AndroidInputMapper.AXIS_RSTICK_Y, sy);
        lastSentZero = zero;
    }

    /** Clamp to [-1, 1] and jump over the game's inner dead zone. */
    private float shape(float v) {
        float a = Math.abs(v);
        if (a < EPSILON) {
            return 0f;
        }
        float lifted = antiDeadzone + (1f - antiDeadzone) * Math.min(1f, a);
        return Math.copySign(Math.min(1f, lifted), v);
    }

    private void sendZero() {
        outX = 0;
        outY = 0;
        gyroYaw = 0;
        gyroPitch = 0;
        if (!lastSentZero && !manualRightStick) {
            output.onAimAxis(AndroidInputMapper.AXIS_RSTICK_X, 0f);
            output.onAimAxis(AndroidInputMapper.AXIS_RSTICK_Y, 0f);
        }
        lastSentZero = true;
    }

    private void updateGyroRegistration() {
        boolean want = running && gyroEnabled && gyro != null;
        if (want && !gyroRegistered) {
            gyroRegistered = sensorManager.registerListener(this, gyro, SensorManager.SENSOR_DELAY_GAME);
        } else if (!want && gyroRegistered) {
            sensorManager.unregisterListener(this);
            gyroRegistered = false;
            gyroYaw = 0;
            gyroPitch = 0;
        }
    }

    @Override
    public void onSensorChanged(SensorEvent event) {
        if (event.sensor.getType() != Sensor.TYPE_GYROSCOPE) {
            return;
        }
        // Device axes: x = right edge (portrait), y = top edge, z = out of screen.
        // Looking "through" the phone: turning it left looks left, tipping the
        // top edge back looks up.
        float gx = event.values[0];
        float gy = event.values[1];
        float yaw;
        float pitch;
        switch (displayRotation) {
            case Surface.ROTATION_270:
                yaw = gx;
                pitch = gy;
                break;
            case Surface.ROTATION_0:
                yaw = -gy;
                pitch = gx;
                break;
            case Surface.ROTATION_180:
                yaw = gy;
                pitch = -gx;
                break;
            case Surface.ROTATION_90:
            default:
                yaw = -gx;
                pitch = -gy;
                break;
        }
        gyroYaw = Math.abs(yaw) < GYRO_NOISE_RAD_S ? 0f : yaw;
        gyroPitch = Math.abs(pitch) < GYRO_NOISE_RAD_S ? 0f : pitch;
    }

    @Override
    public void onAccuracyChanged(Sensor sensor, int accuracy) {
    }
}
