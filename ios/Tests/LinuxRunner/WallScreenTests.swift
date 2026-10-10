import Foundation

// The wall iPad's screen (Shared/Core/WallScreen.swift): the same cases as the Android wall's
// WallBrightnessTest.kt and WallShellTest.kt, so both tablets dim and paint alike. Called from main.swift.

private func near(_ a: Double, _ b: Double, _ what: String, line: Int = #line) {
    check(abs(a - b) < 1e-9, "\(what): got \(a), expected \(b)", line: line)
}

func runWallScreenTests() {
    // Old dim levels are raised to the floor.
    near(WallBrightness.dimSetting(0.02), WallBrightness.floor, "0.02 -> floor")
    near(WallBrightness.dimSetting(0.05), WallBrightness.floor, "old iPad default 0.05 -> floor")
    near(WallBrightness.dimSetting(0), WallBrightness.floor, "0 -> floor")
    near(WallBrightness.dimSetting(-3), WallBrightness.floor, "negative -> floor")
    near(WallBrightness.dimSetting(0.2), 0.2, "0.2 kept")
    near(WallBrightness.dimSetting(4), 1, "above 1 -> 1")
    near(WallBrightness.dimSetting(.nan), WallBrightness.defaultDim, "NaN -> default")

    // Dimmed is never below the floor.
    for l in [0, 0.01, 0.02, 0.05, 0.08, 0.15, 0.5, 1.0] {
        for saver in [false, true] {
            let v = WallBrightness.dimmed(l, powerSave: saver)
            check(v >= WallBrightness.floor && v <= 1, "\(l) saver=\(saver) -> \(v)")
        }
    }
    near(WallBrightness.dimmed(0.15), 0.15, "dimmed 0.15")
    // A power saver that halves the level is made up for.
    near(WallBrightness.dimmed(0.15, powerSave: true), 0.3, "saver 0.15")
    near(WallBrightness.dimmed(0.02, powerSave: true), 0.16, "saver 0.02")
    near(WallBrightness.dimmed(0.8, powerSave: true), 1, "saver capped")
    check(WallBrightness.dimmed(0, powerSave: true) * WallBrightness.powerSaveFactor >= WallBrightness.floor, "saver shown >= floor")

    // Awake keeps the iPad's own level or clamps.
    near(WallBrightness.awake(-1), WallBrightness.system, "system")
    near(WallBrightness.awake(.nan), WallBrightness.system, "NaN -> system")
    near(WallBrightness.awake(0), WallBrightness.floor, "page asked for 0")
    near(WallBrightness.awake(0.01), WallBrightness.floor, "0.01 -> floor")
    near(WallBrightness.awake(0.6), 0.6, "0.6 kept")
    near(WallBrightness.awake(1.5), 1, "1.5 -> 1")

    // Defaults are visible; the dim slider's range.
    check(WallBrightness.defaultDim >= WallBrightness.floor, "default dim visible")
    eq(WallBrightness.defaultDimMinutes, 5, "dim after 5 min")
    near(WallBrightness.floor, 0.08, "floor 8%")
    near(WallBrightness.maxDim, 0.5, "dim slider up to 50%")
    near(WallBrightness.dimMinutesSetting(.nan), 5, "minutes NaN -> 5")
    near(WallBrightness.dimMinutesSetting(500), 120, "minutes capped")
    near(WallBrightness.dimMinutesSetting(-2), 0, "minutes >= 0")

    // The shell colour from the page's theme.
    eq(WallShell.parse(background: "#0b1026", scheme: "dark"), WallShell(background: "#0b1026", dark: true), "midnight")
    eq(WallShell.parse(background: "#E6DEF5", scheme: "light"), WallShell(background: "#e6def5", dark: false), "glass")
    eq(WallShell.parse(background: " #6f9dd4 ", scheme: "DARK"), WallShell(background: "#6f9dd4", dark: true), "christmas")
    eq(WallShell.parse(background: "#fff", scheme: nil), WallShell(background: "#ffffff", dark: false), "short white")
    eq(WallShell.parse(background: "#123", scheme: ""), WallShell(background: "#112233", dark: true), "short dark")
    check(WallShell.parse(background: "#e6def5", scheme: "sepia")?.dark == false, "light from the colour")
    check(WallShell.parse(background: "#0b1026", scheme: nil)?.dark == true, "dark from the colour")
    for bad in [nil, "", "0b1026", "#0b10", "#0b1026ff", "#zzzzzz", "red", "rgb(1,2,3)", "url(x)"] as [String?] {
        check(WallShell.parse(background: bad, scheme: "dark") == nil, "refused: \(bad ?? "nil")")
    }
    eq(WallShell.saved(background: nil, scheme: nil), WallShell.midnight, "nothing saved")
    eq(WallShell.saved(background: "nope", scheme: "light"), WallShell.midnight, "bad save")
    let rgb = WallShell.midnight.rgb
    near(rgb[0], 11.0 / 255, "red"); near(rgb[1], 16.0 / 255, "green"); near(rgb[2], 38.0 / 255, "blue")
    eq(WallShell.midnight.ink, "#a9b4d6", "ink on dark")
    eq(WallShell.parse(background: "#e6def5", scheme: "light")?.ink, "#55556a", "ink on light")

    // The bridge script: the same window.NewmanWall as Android's, knowing whether it loads dimmed.
    let js = WallBridgeJS.script(version: "2026.10 (7) \"x\"", dimmed: true)
    for f in ["setBrightness", "wake", "dim", "isDimmed", "keepAwake", "voiceConfig", "voiceDone", "openSettings",
              "openServer", "setTheme", "recordClip", "stopClip"] {
        check(js.contains("\(f): function"), "bridge has \(f)")
    }
    check(js.contains("var dimmed = true;") && WallBridgeJS.script(version: "1", dimmed: false).contains("var dimmed = false;"), "loads dimmed or not")
    check(js.contains("version: \"2026.10 (7) x\""), "version without quotes")
    check(js.contains("on !== false"), "keepAwake() with no argument keeps it on, like Android")
    let ev = WallBridgeJS.dimEvent(true)
    check(ev.contains("'newmanwall-dim'") && ev.contains("cancelable:true") && ev.contains("dimmed:true") && ev.contains("defaultPrevented"), "dim event")
    check(WallBridgeJS.dimEvent(false).contains("_dimmed(false)"), "wake updates isDimmed()")
}
