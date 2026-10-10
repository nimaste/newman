import Foundation

/// Apple's Location Push Service Extension (LocationPushExtension): the hub sends an APNs location
/// push when someone taps this person on their map (at most one per 2 minutes), and the extension -
/// started by iOS even when the app was swiped away - takes ONE fix and posts it (`source:
/// "refresh"`), then stops. The pure rules and bodies
/// live here so the app, the extension and the Linux checks share them.
enum LocationPushRule {
    /// The App Group shared by the app and its extensions.
    static let appGroup = "group.ca.nimaste.newman"
    /// Bool in the App Group's defaults: this phone shares its location (the app keeps it current).
    static let sharingKey = "location.sharing"

    /// The App Group's defaults (nil only when the entitlement is missing).
    static var sharedDefaults: UserDefaults? { UserDefaults(suiteName: appGroup) }

    /// The whole answer must be done within this (iOS gives the extension little time).
    static let budgetSeconds = 15.0
    /// How long to wait for a fresh fix before falling back to Core Location's newest one.
    static let fixWaitSeconds = 9.0
    /// The newest cached fix is still worth sending when no fresh one comes, up to this age.
    static let cachedMaxAgeSeconds = 120.0
    /// Never give the report less than this, nor more than what's left of the budget.
    static let minPostSeconds = 2.0

    /// Whether the extension should answer at all: only while this phone shares, is signed in to a
    /// hub, and location access hasn't been denied (the app needs "Always" to have got a token).
    static func shouldRespond(sharing: Bool, signedIn: Bool, locationDenied: Bool) -> Bool {
        sharing && signedIn && !locationDenied
    }

    /// A fix the extension may send when no fresh one came in time (`age` in seconds, from its
    /// timestamp; `accuracy` < 0 means invalid).
    static func usableCachedFix(age: Double, accuracy: Double) -> Bool {
        accuracy >= 0 && age > -5 && age <= cachedMaxAgeSeconds
    }

    /// Seconds the report may take, given how long the extension has already been running.
    static func postTimeout(elapsed: Double) -> Double {
        max(minPostSeconds, budgetSeconds - 0.5 - elapsed)
    }

    /// POST /api/me/location/report body for the extension's fix.
    static func reportBody(lat: Double, lon: Double, accuracyM: Double?, at: Double, speedMps: Double?) -> [String: JSONValue] {
        LocationReportBody.make(lat: lat, lon: lon, accuracyM: accuracyM, at: at, speedMps: speedMps, source: LocatePush.source)
    }

    /// POST /api/me/location/push-token body: this phone (its /api/devices/register deviceId), the
    /// token from `startMonitoringLocationPushes` (hex) and the APNs environment it belongs to.
    static func tokenBody(deviceId: String, token: String, sandbox: Bool) -> [String: JSONValue] {
        ["deviceId": .string(deviceId), "token": .string(token), "env": .string(sandbox ? "sandbox" : "production")]
    }

    /// The same endpoint with a null token: the hub forgets this phone's location push token.
    static func forgetBody(deviceId: String) -> [String: JSONValue] {
        ["deviceId": .string(deviceId), "token": .null]
    }

    /// Whether the app should keep location pushes running (startMonitoringLocationPushes) - only
    /// while sharing, signed in and with "Always" (the extension only runs with it).
    static func shouldMonitor(sharing: Bool, signedIn: Bool, always: Bool) -> Bool {
        sharing && signedIn && always
    }
}
