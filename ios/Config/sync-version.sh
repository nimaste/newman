#!/bin/bash
# Runs automatically before every `xcodegen generate` (wired in via
# project.yml's preGenCommand). Xcode's General tab doesn't reliably write
# Version/Build edits into Version.xcconfig even though that's what backs
# those settings — in practice it writes a pbxproj-level override instead,
# which xcodegen would otherwise silently discard on the next regenerate.
# This captures whatever's currently live in the pbxproj (if anything) and
# promotes it into Version.xcconfig first, so it survives regeneration
# either way. No need to hand-edit this file or the .xcconfig — just use
# Xcode's General tab as normal.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PBXPROJ="$DIR/../Newman.xcodeproj/project.pbxproj"
XCCONFIG="$DIR/Version.xcconfig"

if [ -f "$PBXPROJ" ]; then
  MV=$(grep -m1 "MARKETING_VERSION = " "$PBXPROJ" | sed -E 's/.*MARKETING_VERSION = ([^;]+);.*/\1/' || true)
  CV=$(grep -m1 "CURRENT_PROJECT_VERSION = " "$PBXPROJ" | sed -E 's/.*CURRENT_PROJECT_VERSION = ([^;]+);.*/\1/' || true)
  if [ -n "${MV:-}" ] && [ -n "${CV:-}" ]; then
    cat > "$XCCONFIG" << EOF
// Auto-synced from the live project by Config/sync-version.sh before every
// xcodegen regenerate. Set Version/Build in Xcode's General tab as normal —
// do not hand-edit this file, it gets overwritten on the next regenerate.
MARKETING_VERSION = $MV
CURRENT_PROJECT_VERSION = $CV
EOF
  fi
fi
