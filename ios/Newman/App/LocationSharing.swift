import CoreLocation
import Foundation
import UIKit

/// Sharing this phone's location with the household (GET/PUT /api/me/location, POST
/// /api/me/location/report). The hub decides whether this phone shares ("choice" lets the person
/// switch it, "required" doesn't, "off" hides it); this keeps Core Location running to match and
/// posts fixes.
///
/// Battery: in the background only significant-location changes (cell towers, roughly every
/// 500 m of movement) and visits (arriving / leaving a place) - the cheapest services iOS has,
/// which also relaunch the app after it was closed. With "Always" permission and
/// `showsBackgroundLocationIndicator = false` there is no blue bar. There is no live mode: the
/// phone never streams its location.
///
/// Tap-to-refresh: when someone taps this person on their map, the hub (at most once every 2
/// minutes per person) sends a one-shot locate - an APNs location push (LocationPushExtension, which
/// answers even when the app was swiped away) or the silent push {"newman":{"type":"locate"}}
/// (`locateNow`). Either takes ONE fix and posts it with `source: "refresh"`, then stops. When both
/// arrive for the same tap only the first answers (`LocatePush.claim`). The tracked person is never
/// told they are being viewed.
///
/// Location pushes: with sharing on and "Always", the app also starts
/// `startMonitoringLocationPushes` and gives the hub that token (POST /api/me/location/push-token).
/// Sharing off, losing "Always" or signing out stops them and tells the hub to forget the token. The
/// extension reads whether this phone shares from the App Group's defaults
/// (`LocationPushRule.sharingKey`).
@MainActor
final class LocationSharing: NSObject, ObservableObject {
    static let shared = LocationSharing()

    /// GET /api/me/location; nil = not known yet, or the hub doesn't offer it (404).
    @Published private(set) var settings: LocationSettings?
    @Published private(set) var authorization: CLAuthorizationStatus = .notDetermined

    private let manager = CLLocationManager()
    private let defaults = UserDefaults.standard
    private var monitoring = false
    private var waiters: [UUID: CheckedContinuation<CLLocation?, Never>] = [:]
    private var authWaiters: [CheckedContinuation<CLAuthorizationStatus, Never>] = []
    private var posting = false
    /// Location pushes: monitoring started in this process, its token (hex), and whether the hub has it.
    private var pushMonitoring = false
    private var locationPushToken: String?
    private var pushTokenSent = false

    private enum Key {
        static let sharing = "location.sharing"
        static let every = "location.everySeconds"
        /// From the live-follow days; only removed now.
        static let oldLiveAt = "location.liveReplyAt"
        static let lastAt = "location.lastAt"
        static let lastLat = "location.lastLat"
        static let lastLon = "location.lastLon"
        static let pushOn = "location.pushMonitoring"
    }

    private var sharing: Bool {
        get { defaults.bool(forKey: Key.sharing) }
        set {
            defaults.set(newValue, forKey: Key.sharing)
            LocationPushRule.sharedDefaults?.set(newValue, forKey: LocationPushRule.sharingKey) // for LocationPushExtension
        }
    }
    private var everySeconds: Int {
        get { defaults.object(forKey: Key.every) as? Int ?? 300 }
        set { defaults.set(newValue, forKey: Key.every) }
    }
    private var lastSent: ReportSchedule.Last? {
        let at = defaults.double(forKey: Key.lastAt)
        guard at > 0 else { return nil }
        return ReportSchedule.Last(at: at, lat: defaults.double(forKey: Key.lastLat), lon: defaults.double(forKey: Key.lastLon))
    }

    private var signedIn: Bool { ConnectionStore.shared.token != nil && ConnectionStore.shared.serverURL != nil }
    private var now: Double { Date().timeIntervalSince1970 }

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
        manager.pausesLocationUpdatesAutomatically = true
        manager.showsBackgroundLocationIndicator = false
        authorization = manager.authorizationStatus
        defaults.removeObject(forKey: Key.oldLiveAt)
    }

    var hasAlways: Bool { authorization == .authorizedAlways }
    var hasWhenInUse: Bool { authorization == .authorizedWhenInUse || authorization == .authorizedAlways }

    // MARK: settings

    /// Reads the hub's setting and starts or stops Core Location to match. Quiet on any failure.
    @discardableResult
    func refresh() async -> LocationSettings? {
        guard signedIn else { return nil }
        do {
            let s = try await APIClient.shared.myLocation()
            settings = s
            apply(sharing: s.sharing, every: s.everySeconds)
        } catch let e as APIError where e.status == 404 || e.status == 403 {
            // An older hub (or sharing off for this account): nothing to share.
            settings = nil
            apply(sharing: false, every: everySeconds)
        } catch {
            // Offline: keep doing what we were doing.
        }
        return settings
    }

    /// The Me screen's switch ("choice" accounts). Throws the hub's words.
    func setEnabled(_ on: Bool) async throws {
        let s = try await APIClient.shared.setMyLocation(enabled: on)
        settings = s
        apply(sharing: s.sharing, every: s.everySeconds)
        if s.sharing { await reportNow() }
    }

    private func apply(sharing on: Bool, every: Int) {
        sharing = on
        everySeconds = every
        if on { resume() } else { stopAll() }
    }

    /// At launch (including a background relaunch for a location event) and after permission
    /// changes: run the services that match the cached state.
    func resume() {
        guard sharing, signedIn, hasWhenInUse else {
            stopAll()
            return
        }
        manager.allowsBackgroundLocationUpdates = hasAlways
        updateLocationPushes()
        if !monitoring && CLLocationManager.significantLocationChangeMonitoringAvailable() {
            manager.startMonitoringSignificantLocationChanges()
            manager.startMonitoringVisits()
            monitoring = true
        }
    }

    private func stopAll() {
        stopLocationPushes()
        if monitoring {
            manager.stopMonitoringSignificantLocationChanges()
            manager.stopMonitoringVisits()
            monitoring = false
        }
    }

    func signedOut() {
        stopAll()
        for k in [Key.sharing, Key.every, Key.oldLiveAt, Key.lastAt, Key.lastLat, Key.lastLon, Key.pushOn] { defaults.removeObject(forKey: k) }
        LocationPushRule.sharedDefaults?.removeObject(forKey: LocationPushRule.sharingKey)
        LocationPushRule.sharedDefaults?.removeObject(forKey: LocatePush.answeredKey)
        settings = nil
    }

    // MARK: location pushes (LocationPushExtension answers them, even with the app closed)

    #if DEBUG
    private static let sandbox = true
    #else
    private static let sandbox = false
    #endif

    /// Sharing with "Always": make sure location pushes are on and the hub has the token.
    private func updateLocationPushes() {
        guard LocationPushRule.shouldMonitor(sharing: sharing, signedIn: signedIn, always: hasAlways) else {
            stopLocationPushes()
            return
        }
        guard !pushMonitoring else { return }
        pushMonitoring = true
        defaults.set(true, forKey: Key.pushOn)
        manager.startMonitoringLocationPushes { data, _ in
            // An error means no extension / entitlement on this build: the silent locate push still works.
            let hex = data.map { PushPayload.hexToken($0) }
            Task { @MainActor in LocationSharing.shared.gotLocationPushToken(hex) }
        }
    }

    private func gotLocationPushToken(_ hex: String?) {
        guard pushMonitoring else { return } // stopped meanwhile
        guard let hex, !hex.isEmpty else {
            pushMonitoring = false // try again on the next resume
            return
        }
        if hex != locationPushToken { pushTokenSent = false }
        locationPushToken = hex
        Task { await sendLocationPushToken() }
    }

    /// Gives the hub this phone's location push token (once per launch, or after it changes). A 404
    /// means the phone isn't registered yet: PushManager calls this again once it is.
    func sendLocationPushToken() async {
        guard pushMonitoring, sharing, signedIn, let token = locationPushToken, !pushTokenSent else { return }
        let body = LocationPushRule.tokenBody(deviceId: ConnectionStore.shared.deviceId, token: token, sandbox: Self.sandbox)
        do {
            try await APIClient.shared.call("POST", "api/me/location/push-token", body: body)
            pushTokenSent = locationPushToken == token
        } catch {
            // Offline, older hub, or not registered yet: the next launch / registration tries again.
        }
    }

    /// Stops location pushes (if they were on, in this or an earlier launch) and tells the hub to
    /// forget the token. The returned task finishes when the hub has been told.
    @discardableResult
    private func stopLocationPushes() -> Task<Void, Never>? {
        guard pushMonitoring || defaults.bool(forKey: Key.pushOn) else { return nil }
        manager.stopMonitoringLocationPushes()
        pushMonitoring = false
        locationPushToken = nil
        pushTokenSent = false
        defaults.set(false, forKey: Key.pushOn)
        guard signedIn else { return nil }
        let body = LocationPushRule.forgetBody(deviceId: ConnectionStore.shared.deviceId)
        return Task { _ = try? await APIClient.shared.call("POST", "api/me/location/push-token", body: body) }
    }

    /// Signing out (while the session still works): stop location pushes and wait for the hub to
    /// forget the token.
    func signingOut() async {
        await stopLocationPushes()?.value
    }

    // MARK: permission (When In Use, then Always - each explained first by the Me screen)

    func requestWhenInUse() async -> CLAuthorizationStatus {
        guard authorization == .notDetermined else { return authorization }
        return await withCheckedContinuation { cont in
            authWaiters.append(cont)
            manager.requestWhenInUseAuthorization()
        }
    }

    /// iOS shows its "Change to Always Allow?" prompt once; after that only Settings can change it.
    func requestAlways() {
        manager.requestAlwaysAuthorization()
    }

    // MARK: reporting

    /// The app came to the front: one fix, posted now (when sharing).
    func reportNow() async {
        guard sharing, signedIn, hasWhenInUse else { return }
        guard let fix = await oneFix(timeout: 15) else { return }
        await post(fix, source: "app", force: true)
    }

    /// The silent push {"newman":{"type":"locate"}}: someone tapped this person. ONE fresh fix,
    /// posted with source "refresh", then nothing more (unless the extension already answered).
    func locateNow() async {
        guard sharing, signedIn, hasWhenInUse else { return }
        guard LocatePush.claim(LocationPushRule.sharedDefaults, now: now) else { return }
        manager.desiredAccuracy = kCLLocationAccuracyNearestTenMeters
        let fix = await oneFix(timeout: 20)
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
        guard let fix else { return }
        await post(fix, source: LocatePush.source, force: true)
    }

    private func oneFix(timeout: Double) async -> CLLocation? {
        if let recent = manager.location, now - recent.timestamp.timeIntervalSince1970 < 10 { return recent }
        return await nextFix(timeout: timeout)
    }

    /// The next fix Core Location delivers, or nil after `timeout` (or on an error).
    private func nextFix(timeout: Double) async -> CLLocation? {
        let id = UUID()
        return await withCheckedContinuation { cont in
            waiters[id] = cont
            manager.requestLocation()
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                self?.finishWaiter(id, nil)
            }
        }
    }

    private func finishWaiter(_ id: UUID, _ loc: CLLocation?) {
        waiters.removeValue(forKey: id)?.resume(returning: loc)
    }

    private func finishWaiters(_ loc: CLLocation?) {
        let w = waiters
        waiters = [:]
        w.values.forEach { $0.resume(returning: loc) }
    }

    fileprivate func received(_ loc: CLLocation) {
        if !waiters.isEmpty {
            finishWaiters(loc) // the waiting caller posts it
            return
        }
        Task { await post(loc, source: nil, force: false) }
    }

    fileprivate func authorizationChanged(_ status: CLAuthorizationStatus) {
        authorization = status
        if status != .notDetermined {
            let w = authWaiters
            authWaiters = []
            w.forEach { $0.resume(returning: status) }
        }
        resume()
    }

    /// Posts a fix if it's time (or `force`), then keeps the reply's everySeconds.
    private func post(_ loc: CLLocation, source: String?, force: Bool) async {
        guard sharing, signedIn else {
            stopAll()
            return
        }
        let t = now
        guard force || ReportSchedule.shouldSend(last: lastSent, now: t, lat: loc.coordinate.latitude, lon: loc.coordinate.longitude,
                                                 everySeconds: everySeconds) else { return }
        if posting && !force { return }
        posting = true
        defer { posting = false }
        // A report from the background gets a little time to finish.
        let bg = UIApplication.shared.beginBackgroundTask(withName: "newman.location") {}
        defer { if bg != .invalid { UIApplication.shared.endBackgroundTask(bg) } }
        let body = LocationReportBody.make(
            lat: loc.coordinate.latitude, lon: loc.coordinate.longitude,
            accuracyM: loc.horizontalAccuracy >= 0 ? loc.horizontalAccuracy : nil,
            at: loc.timestamp.timeIntervalSince1970,
            speedMps: loc.speed >= 0 ? loc.speed : nil,
            source: source ?? "background")
        do {
            let reply = try await APIClient.shared.reportLocation(body)
            defaults.set(t, forKey: Key.lastAt)
            defaults.set(loc.coordinate.latitude, forKey: Key.lastLat)
            defaults.set(loc.coordinate.longitude, forKey: Key.lastLon)
            if reply.everySeconds > 0 { everySeconds = reply.everySeconds }
        } catch let e as APIError where e.status == 403 || e.status == 404 {
            // The hub says this phone isn't sharing (switched off elsewhere): stop.
            apply(sharing: false, every: everySeconds)
        } catch {
            // Offline or the hub is busy: the next fix tries again.
        }
    }
}

extension LocationSharing: CLLocationManagerDelegate {
    // Core Location calls back on the main thread (the manager was made there).
    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        MainActor.assumeIsolated { self.received(loc) }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didVisit visit: CLVisit) {
        guard CLLocationCoordinate2DIsValid(visit.coordinate) else { return }
        let when = visit.departureDate == .distantFuture ? visit.arrivalDate : visit.departureDate
        let loc = CLLocation(coordinate: visit.coordinate, altitude: 0, horizontalAccuracy: visit.horizontalAccuracy,
                             verticalAccuracy: -1, timestamp: when == .distantPast ? Date() : when)
        MainActor.assumeIsolated { self.received(loc) }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        MainActor.assumeIsolated { self.finishWaiters(nil) }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        MainActor.assumeIsolated { self.authorizationChanged(status) }
    }
}
