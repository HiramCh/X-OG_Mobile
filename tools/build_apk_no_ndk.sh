#!/usr/bin/env bash
# Builds the APK without Gradle/NDK: Ubuntu's aapt, dalvik-exchange (dx),
# zipalign and apksigner, plus stock clang for the C JNI bridge.
#   usage: tools/build_apk_no_ndk.sh <android.jar> <keystore> <storepass> <out.apk>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JAR="$1"; KS="$2"; PASS="$3"; OUT="$4"
A="$ROOT/android/app/src/main"
B="$(mktemp -d)"
mkdir -p "$B/gen" "$B/src" "$B/classes" "$B/stubs" "$B/lib/arm64-v8a"

# 1. C bridge (arm64 bionic, no sysroot)
T=--target=aarch64-linux-android29
JINC=$(dirname "$(dirname "$(readlink -f "$(which javac)")")")/include
echo 'void __xog_stub(void){}' > "$B/stub.c"
for l in c log android vulkan dl; do
  clang $T -fPIC -shared -nostdlib -fuse-ld=lld -Wl,-soname,lib$l.so "$B/stub.c" -o "$B/stubs/lib$l.so"
done
clang $T -std=c11 -O2 -fPIC -Wall -nostdlibinc -I"$A/cpp/c_bridge/include" -I"$A/cpp/c_bridge" \
  -I"$JINC" -I"$JINC/linux" -fno-stack-protector -mno-outline-atomics -fvisibility=hidden \
  -c "$A/cpp/c_bridge/android_backend.c" -o "$B/bridge.o"
clang $T -shared -nostdlib -fuse-ld=lld -Wl,-soname,libxboxemu_android.so -Wl,-z,max-page-size=16384 \
  -Wl,--hash-style=both -Wl,--build-id=sha1 -Wl,-z,noexecstack -Wl,-z,relro -Wl,-z,now \
  "$B/bridge.o" -L"$B/stubs" -L"${CORE_DIR:-$A/jniLibs/arm64-v8a}" -l:libxemu-core-i386.so \
  -landroid -llog -lvulkan -ldl -lc -o "$B/lib/arm64-v8a/libxboxemu_android.so"
# CORE_DIR: folder with a CI-built core (libxemu-core-i386.so, libqemu-system-i386.so,
# libc++_shared.so). Default: the prebuilt libraries vendored in jniLibs.
CORE_DIR="${CORE_DIR:-$A/jniLibs/arm64-v8a}"
for f in libc++_shared.so libqemu-system-i386.so libxemu-core-i386.so; do
  llvm-strip --strip-debug -o "$B/lib/arm64-v8a/$f" "$CORE_DIR/$f"
done
# The vendored prebuilt core needs the Mali binary patch; a core built from
# xemu/ already carries the source fix, so the patch is skipped there.
python3 -I "$ROOT/tools/patch_core_mali.py" --optional "$B/lib/arm64-v8a/libxemu-core-i386.so" "$B/lib/arm64-v8a/libxemu-core-i386.so"

# 2. Resources + manifest (application id emu.xbox.og.h2)
python3 - "$A/AndroidManifest.xml" "$B/AndroidManifest.xml" <<'EOF'
import sys
s=open(sys.argv[1]).read()
s=s.replace('<manifest xmlns:android="http://schemas.android.com/apk/res/android">',
 '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="emu.xbox.og" android:versionCode="16" android:versionName="0.5.1-h2mtk">\n    <uses-sdk android:minSdkVersion="29" android:targetSdkVersion="36" />')
s=s.replace('android:name=".','android:name="emu.xbox.og.')
s=s.replace('android:theme="@style/AppTheme">','android:theme="@style/AppTheme"\n        android:extractNativeLibs="true">')
open(sys.argv[2],'w').write(s)
EOF
aapt package -f -M "$B/AndroidManifest.xml" -S "$A/res" -I "$JAR" -J "$B/gen" -F "$B/unsigned.apk" \
  --rename-manifest-package emu.xbox.og.h2 --auto-add-overlay

# 3. Java -> desugared Java 8 -> dex
cp "$A"/java/emu/xbox/og/*.java "$B/gen/R.java" "$B/src/"
python3 -I "$ROOT/tools/desugar_lambdas.py" "$B"/src/*.java
javac -nowarn -Xlint:-options -source 8 -target 8 -encoding UTF-8 -cp "$JAR" -d "$B/classes" "$B"/src/*.java
if javap -c -p "$B"/classes/emu/xbox/og/*.class | grep -q invokedynamic; then
  echo "invokedynamic left after desugaring" >&2; exit 1
fi
dalvik-exchange --dex --min-sdk-version=26 --output="$B/classes.dex" "$B/classes"

# 4. Package, align, sign
(cd "$B" && python3 - <<'EOF'
import zipfile
with zipfile.ZipFile('unsigned.apk','a',zipfile.ZIP_DEFLATED) as z:
    z.write('classes.dex','classes.dex')
    for n in ['libc++_shared.so','libqemu-system-i386.so','libxemu-core-i386.so','libxboxemu_android.so']:
        z.write('lib/arm64-v8a/'+n,'lib/arm64-v8a/'+n)
EOF
)
zipalign -f -p 4 "$B/unsigned.apk" "$B/aligned.apk"
apksigner sign --ks "$KS" --ks-pass "pass:$PASS" --min-sdk-version 29 --out "$OUT" "$B/aligned.apk"
apksigner verify "$OUT"
echo "APK: $OUT"
