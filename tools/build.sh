#!/bin/zsh
# Builds the three firmware images into firmware/build/.
set -e
ROOT=${0:a:h:h}
: ${PICO_SDK_PATH:=$HOME/pico/pico-sdk}
: ${PICO_TOOLCHAIN_BIN:=$HOME/pico/toolchain/bin}
export PATH=$PICO_TOOLCHAIN_BIN:/opt/homebrew/bin:$PATH
BUILD=$ROOT/firmware/build
if [ ! -f "$BUILD/build.ninja" ]; then
  cmake -S "$ROOT/firmware" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DPICO_SDK_PATH="$PICO_SDK_PATH" \
    -DPICOTOOL_FETCH_FROM_GIT_PATH="$HOME/pico/picotool-cache" \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 > "$BUILD.configure.log" 2>&1 \
    || { tail -30 "$BUILD.configure.log"; exit 1 }
fi
ninja -C "$BUILD" | tail -2
