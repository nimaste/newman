#!/bin/bash
# Checks the iOS sources on Linux (no Mac needed), in Docker with the official Swift image:
#   1. swiftc -parse on every .swift file (syntax only; SwiftUI/UIKit can't be type-checked here)
#   2. type-checks, builds and runs the pure Foundation core (Shared/Core) against JSON captured
#      from a live hub (Tests/Fixtures) with Tests/LinuxRunner/main.swift.
# Usage: ios/Tests/run-linux-checks.sh   (from anywhere; needs docker)
set -euo pipefail
IOS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${SWIFT_IMAGE:-swift:6.0}"
docker run --rm -v "$IOS":/src:ro -w /src "$IMAGE" bash -c '
  set -euo pipefail
  echo "== swiftc -parse (all Swift files)"
  files=$(find . -name "*.swift" -not -path "./build/*" | sort)
  echo "$files" | wc -l | xargs echo "files:"
  swiftc -parse $files && echo "parse OK"
  echo "== type-check + run Shared/Core against Tests/Fixtures"
  mkdir -p /tmp/out
  swiftc -swift-version 5 -warnings-as-errors Shared/Core/*.swift Shared/UI/Symbols.swift Tests/LinuxRunner/*.swift -o /tmp/out/core-tests
  /tmp/out/core-tests Tests/Fixtures
'
