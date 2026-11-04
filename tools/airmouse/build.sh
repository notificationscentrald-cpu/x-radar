#!/bin/bash
# Builds AirMouse.app next to this script. Needs Xcode Command Line Tools:
#   xcode-select --install
set -euo pipefail
cd "$(dirname "$0")"
APP=AirMouse.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
swiftc -O -framework AppKit -framework CoreMotion -o "$APP/Contents/MacOS/AirMouse" main.swift
cp Info.plist "$APP/Contents/Info.plist"
codesign --force --deep --sign - "$APP"
echo "Built $APP. Run:  open $APP   (or ./$APP/Contents/MacOS/AirMouse to see logs)"
