import Foundation

/// The wall tablet's battery as the hub wants it: POST /api/tablet/battery with
/// {"level": 0-100, "charging": bool, "plugged": "ac"|"usb"|"wireless"|"none",
///  "temperatureC": float|null, "voltageMv": int|null}, plus "screen": "on"|"off" when set - the iPad
/// wall says "off" as it goes inactive (its last report until it's woken: iPadOS gives no background
/// time, so no "background": true), and the hub shows "Screen off" rather than offline.
struct TabletBattery: Equatable, Encodable {
    let level: Int
    let charging: Bool
    let plugged: String
    let temperatureC: Double?
    let voltageMv: Int?
    var screen: String? = nil

    /// The same reading, saying the screen is on or off.
    func with(screenOn: Bool) -> TabletBattery {
        var b = self
        b.screen = screenOn ? "on" : "off"
        return b
    }

    static let path = "api/tablet/battery"
    static let sessionCookie = "newman_session"

    /// From UIDevice: `batteryLevel` (0...1, or -1 when unknown, e.g. monitoring off or the
    /// simulator) and `batteryState.rawValue` (0 unknown, 1 unplugged, 2 charging, 3 full). iOS
    /// can't tell the power source, so plugged is "ac" while charging or full; no temperature or
    /// voltage. Nil when the level is unknown.
    static func fromIOS(level: Float, stateRaw: Int) -> TabletBattery? {
        guard level >= 0, level.isFinite else { return nil }
        let charging = stateRaw == 2 || stateRaw == 3
        let pct = Int((Double(level) * 100).rounded())
        return TabletBattery(level: min(100, max(0, pct)), charging: charging, plugged: charging ? "ac" : "none",
                             temperatureC: nil, voltageMv: nil)
    }

    private enum CodingKeys: String, CodingKey { case level, charging, plugged, temperatureC, voltageMv, screen }

    /// Unknown values are sent as null (not left out).
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(level, forKey: .level)
        try c.encode(charging, forKey: .charging)
        try c.encode(plugged, forKey: .plugged)
        if let temperatureC { try c.encode(temperatureC, forKey: .temperatureC) } else { try c.encodeNil(forKey: .temperatureC) }
        if let voltageMv { try c.encode(voltageMv, forKey: .voltageMv) } else { try c.encodeNil(forKey: .voltageMv) }
        if let screen { try c.encode(screen, forKey: .screen) }
    }

    var json: Data { (try? JSONEncoder().encode(self)) ?? Data("{}".utf8) }

    /// Whether a cookie set for `cookieDomain` ("home.garito.ca", or ".garito.ca" for a domain
    /// cookie) is sent to `host`.
    static func cookieDomainMatches(_ cookieDomain: String, host: String) -> Bool {
        let h = host.lowercased()
        var d = cookieDomain.lowercased()
        guard !d.isEmpty else { return false }
        if d.hasPrefix(".") {
            d.removeFirst()
            return h == d || h.hasSuffix("." + d)
        }
        return h == d
    }
}

/// When to send: at once the first time, then every `interval`, and early when charging starts or
/// stops or the level has moved `levelStep` points since the last report. After a failed send
/// (signed out, an older hub, no network) it waits a full interval before trying again.
final class BatterySchedule {
    private let interval: TimeInterval
    private let levelStep: Int
    /// Timer ticks aren't exact: a report due within this much counts as due.
    private let slack: TimeInterval

    private(set) var lastSent: TabletBattery?
    private var lastSentAt: TimeInterval = 0
    private var lastFailureAt: TimeInterval?
    private(set) var inFlight = false

    init(interval: TimeInterval = 300, levelStep: Int = 5, slack: TimeInterval = 15) {
        self.interval = interval
        self.levelStep = levelStep
        self.slack = slack
    }

    func shouldSend(now: TimeInterval, _ current: TabletBattery) -> Bool {
        if inFlight { return false }
        if let f = lastFailureAt, now - f < interval - slack { return false }
        guard let last = lastSent else { return true }
        if now - lastSentAt >= interval - slack { return true }
        if current.charging != last.charging { return true }
        return abs(current.level - last.level) >= levelStep
    }

    func started() { inFlight = true }

    /// The next look sends at once (the wall just came to the front: "screen on").
    func forceNext() {
        lastSent = nil
        lastFailureAt = nil
    }

    /// Nothing was sent (e.g. not signed in yet): look again at the next tick, no back-off.
    func skipped() { inFlight = false }

    func succeeded(now: TimeInterval, _ sent: TabletBattery) {
        inFlight = false
        lastSent = sent
        lastSentAt = now
        lastFailureAt = nil
    }

    func failed(now: TimeInterval) {
        inFlight = false
        lastFailureAt = now
    }
}
