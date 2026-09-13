[app]
title = Racing Pedals
package.name = racingpedals
package.domain = org.example
source.dir = .
source.include_exts = py
version = 1.0

requirements = python3,kivy

orientation = landscape
fullscreen = 1

# Only permission this app needs: the loopback TCP server that the PC
# reaches through `adb forward` over the USB cable.
android.permissions = INTERNET

# Reasonable modern defaults; buildozer will download the matching
# SDK/NDK/build-tools automatically on first build.
android.api = 34
android.minapi = 21
android.archs = arm64-v8a, armeabi-v7a

[buildozer]
log_level = 2
warn_on_root = 1
