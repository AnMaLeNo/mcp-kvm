#!/bin/zsh
# Builds the firmware and flashes the target board, with no hand on the board.
# The copy goes through the Windows target the board is plugged into.
#
#   tools/flash.sh              plain HID firmware (two-board setup)
#   tools/flash.sh cdc          HID + serial port firmware (single-board setup)
#   tools/flash.sh [cdc] --no-build
#
# Needs KVM_SSH=user@host (in .kvm.env at the repo root or in the environment)
# and the one-time target setup described in README.md.
set -e
ROOT=${0:a:h:h}
[ -f "$ROOT/.kvm.env" ] && source "$ROOT/.kvm.env"
: ${KVM_SSH:?set KVM_SSH=user@host in .kvm.env}
: ${PICO_SDK_PATH:=$HOME/pico/pico-sdk}
: ${PICO_TOOLCHAIN_BIN:=$HOME/pico/toolchain/bin}
export KVM_SSH PATH=$PICO_TOOLCHAIN_BIN:/opt/homebrew/bin:$PATH

NAME=kvm_hid
BUILD_FIRST=1
for arg in "$@"; do
  case $arg in
    cdc) NAME=kvm_hid_cdc ;;
    hid) NAME=kvm_hid ;;
    --no-build) BUILD_FIRST=0 ;;
    *) echo "unknown argument: $arg"; exit 2 ;;
  esac
done
BUILD=$ROOT/firmware/build
PY=$ROOT/.venv/bin/python
[ $BUILD_FIRST = 1 ] && "$ROOT/tools/build.sh"

# Ask the running firmware to enter its bootloader through whichever link
# reaches it. Failure is fine: the board may already be in its bootloader.
"$PY" -m mcp_kvm.cli raw bootsel > /dev/null 2>&1 || true

scp -q -o BatchMode=yes "$BUILD/$NAME.uf2" "$ROOT"/tools/win/*.py "$KVM_SSH:mcp-kvm-dev/"
# The target's default SSH shell is PowerShell.
ssh -o BatchMode=yes -o ConnectTimeout=10 $KVM_SSH \
  '& "$env:USERPROFILE\mcp-kvm-dev\venv\Scripts\python.exe" -u "$env:USERPROFILE\mcp-kvm-dev\flash.py" "$env:USERPROFILE\mcp-kvm-dev\'$NAME'.uf2"'

# The new firmware must answer; this also disarms its return to the bootloader.
for i in {1..12}; do
  sleep 1
  if INFO=$("$PY" -m mcp_kvm.cli raw info 2>/dev/null); then
    echo "FLASH_OK $NAME: $INFO"
    exit 0
  fi
done
echo "FLASH_FAIL $NAME was copied but does not answer"
exit 1
