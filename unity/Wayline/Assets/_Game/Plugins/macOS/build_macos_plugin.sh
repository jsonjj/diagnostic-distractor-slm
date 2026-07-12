#!/bin/sh
set -eu

PLUGIN_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE="$PLUGIN_DIR/WaylineTextToSpeech.mm"
OUTPUT="$PLUGIN_DIR/libWaylineTextToSpeech.dylib"

xcrun clang++ \
  -std=c++17 \
  -fobjc-arc \
  -dynamiclib \
  -arch arm64 \
  -mmacosx-version-min=13.0 \
  -framework AVFoundation \
  -framework Foundation \
  -Wl,-install_name,@rpath/libWaylineTextToSpeech.dylib \
  -o "$OUTPUT" \
  "$SOURCE"

ARCHS=$(lipo -archs "$OUTPUT")
test "$ARCHS" = "arm64"
nm -gU "$OUTPUT" | grep -q '_WaylineSpeakUtf8$'
