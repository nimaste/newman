import Foundation

/// The wall iPad's screen levels (UIScreen.brightness, 0...1) - the same rules as the Android wall
/// (android/core .../core/wall/WallBrightness.kt): a dimmed wall must still look ON, so every level the
/// app sets is at least `floor` (8 %); old saves below it (0.05 and 0 were offered once) read as the floor.
enum WallBrightness {
    /// Never below this, whatever the setting or the page asks for.
    static let floor = 0.08
    /// Dimmed brightness on a new install: low but obviously on.
    static let defaultDim = 0.15
    /// Minutes without a touch before dimming, on a new install.
    static let defaultDimMinutes = 5
    /// The longest "dim after" the settings offer (minutes).
    static let maxDimMinutes = 60
    /// A low-power mode's brightness factor (Android's Battery Saver halves the level; iPadOS doesn't).
    static let powerSaveFactor = 0.5
    /// Largest dimmed level the settings offer.
    static let maxDim = 0.5
    /// "Use the iPad's own brightness" while awake.
    static let system = -1.0

    /// A saved or chosen dimmed level, kept in floor...1.
    static func dimSetting(_ level: Double) -> Double {
        level.isNaN ? defaultDim : min(1, max(floor, level))
    }

    /// The screen level while dimmed: the setting, raised back up while a power saver halves it.
    static func dimmed(_ level: Double, powerSave: Bool = false) -> Double {
        let l = dimSetting(level)
        return powerSave ? min(1, max(floor, l / powerSaveFactor)) : l
    }

    /// The level while awake: `system` stays the iPad's own, anything else floor...1.
    static func awake(_ level: Double) -> Double {
        level.isNaN || level < 0 ? system : min(1, max(floor, level))
    }

    /// Minutes before dimming, kept in 0 (never)...120 like the Android wall's setting.
    static func dimMinutesSetting(_ minutes: Double) -> Double {
        minutes.isNaN ? Double(defaultDimMinutes) : min(120, max(0, minutes.rounded()))
    }
}

/// The colour the wall paints behind the hub's page while it loads or can't be reached, so a light theme
/// doesn't flash Midnight blue. The page reports it with NewmanWall.setTheme({background, scheme})
/// (web/src/wall/theme.js SHELL); kept for the next start. Same rules as android/core WallShell.kt.
struct WallShell: Equatable {
    /// "#rrggbb", lower case.
    let background: String
    let dark: Bool

    /// Midnight, the original look: before any page has said otherwise.
    static let midnight = WallShell(background: "#0b1026", dark: true)

    /// Red, green, blue 0...1 of `background`.
    var rgb: [Double] {
        let n = Int(background.dropFirst(), radix: 16) ?? 0
        return [Double((n >> 16) & 0xFF) / 255, Double((n >> 8) & 0xFF) / 255, Double(n & 0xFF) / 255]
    }

    /// Text on this background ("can't reach Newman"): "#rrggbb".
    var ink: String { dark ? "#a9b4d6" : "#55556a" }
    /// Headings on this background.
    var strongInk: String { dark ? "#ffffff" : "#1c1c22" }

    /// "#rgb" / "#rrggbb" (any case) and "light" / "dark"; without a known scheme, dark when the colour
    /// is. Nil for anything else.
    static func parse(background: String?, scheme: String?) -> WallShell? {
        guard let hex = normalize(background) else { return nil }
        let dark: Bool
        switch scheme?.trimmingCharacters(in: .whitespaces).lowercased() {
        case "dark": dark = true
        case "light": dark = false
        default: dark = luminance(hex) < 0.5
        }
        return WallShell(background: hex, dark: dark)
    }

    /// What was saved, else Midnight.
    static func saved(background: String?, scheme: String?) -> WallShell {
        parse(background: background, scheme: scheme) ?? midnight
    }

    private static func normalize(_ v: String?) -> String? {
        guard let s = v?.trimmingCharacters(in: .whitespaces).lowercased(), s.hasPrefix("#") else { return nil }
        let h = s.dropFirst()
        guard h.allSatisfy({ ("0"..."9").contains($0) || ("a"..."f").contains($0) }) else { return nil }
        switch h.count {
        case 6: return "#" + h
        case 3: return "#" + h.map { "\($0)\($0)" }.joined()
        default: return nil
        }
    }

    /// Relative luminance 0...1 (sRGB weights, no gamma: only light vs dark matters).
    private static func luminance(_ hex: String) -> Double {
        let c = WallShell(background: hex, dark: true).rgb
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    }
}

/// JavaScript the iPad wall runs in the hub's page (the same window.NewmanWall as the Android wall's
/// WallBridge.SCRIPT; messages go to the app's "newmanWall" handler).
enum WallBridgeJS {
    /// window.NewmanWall at document start; `dimmed`: whether the screen is dimmed as the page loads.
    static func script(version: String, dimmed: Bool) -> String {
        let v = version.replacingOccurrences(of: "\\", with: "").replacingOccurrences(of: "\"", with: "")
        return """
        (function () {
          if (window.NewmanWall) return;
          var post = function (m) {
            try { window.webkit.messageHandlers.newmanWall.postMessage(m); } catch (e) {}
          };
          var dimmed = \(dimmed ? "true" : "false");
          window.NewmanWall = Object.freeze({
            native: true,
            version: "\(v)",
            platform: "ios",
            setBrightness: function (v) { post({ action: "setBrightness", value: Math.max(0, Math.min(1, Number(v))) }); },
            wake: function () { post({ action: "wake" }); },
            dim: function () { post({ action: "dim" }); },
            isDimmed: function () { return dimmed; },
            keepAwake: function (on) { post({ action: "keepAwake", value: on !== false }); },
            voiceConfig: function (json) { post({ action: "voiceConfig", value: String(json || "") }); },
            voiceDone: function () { post({ action: "voiceDone" }); },
            openSettings: function () { post({ action: "openSettings" }); },
            openServer: function () { post({ action: "openServer" }); },
            setTheme: function (t) { t = t || {}; post({ action: "setTheme", background: String(t.background || ""), scheme: String(t.scheme || "") }); },
            recordClip: function (ms, fixed) { post({ action: "recordClip", ms: Math.round(Number(ms) || 5000), fixed: fixed === true }); },
            stopClip: function () { post({ action: "stopClip" }); },
            _dimmed: function (on) { dimmed = on === true; }
          });
          try { window.dispatchEvent(new Event("newmanwall")); } catch (e) {}
        })();
        """
    }

    /// Tells the page the screen dimmed or woke: a cancelable "newmanwall-dim" event (detail.dimmed).
    /// Evaluates to true when the page drew its own dimmed screen (preventDefault()).
    static func dimEvent(_ on: Bool) -> String {
        let b = on ? "true" : "false"
        return "(function(){try{var n=window.NewmanWall;if(n&&n._dimmed)n._dimmed(\(b));" +
            "var e=new CustomEvent('newmanwall-dim',{detail:{dimmed:\(b)},cancelable:true});" +
            "window.dispatchEvent(e);return e.defaultPrevented;}catch(x){return false;}})()"
    }
}
