#!/bin/zsh
# Builds the native macOS capture helper used by the MCP server (needs Xcode's swiftc).
set -e
ROOT=${0:a:h:h}
mkdir -p "$ROOT/src/mcp_kvm/_bin"
swiftc -O "$ROOT/tools/capture/kvm-capture.swift" -o "$ROOT/src/mcp_kvm/_bin/kvm-capture"
echo "built $ROOT/src/mcp_kvm/_bin/kvm-capture"
