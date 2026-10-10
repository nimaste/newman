import CoreLocation
import Foundation

/// Answers the hub's APNs location pushes (`apns-push-type: location`, sent when someone taps this
/// person on their map - at most once every 2 minutes). iOS starts this extension even when the app
/// was swiped away: it takes ONE fix and posts it to POST /api/me/location/report with `source:
/// "refresh"`, then completes - all within `LocationPushRule.budgetSeconds` (15 s). If the app
/// already answered the same tap (silent push), it does nothing (`LocatePush.claim`).
///
/// It reaches the hub like the notification extension: the address in use (with the hub's other
/// saved address as a fallback, see `send`), session token,
/// Cloudflare headers and the mTLS client certificate come from the Keychain shared with the app
/// (group.ca.nimaste.newman, see ConnectionStore / CertificateStore / NewmanURLSession). Whether
/// this phone shares at all is the app's flag in the App Group's defaults
/// (`LocationPushRule.sharingKey`); with it off (or signed out) it does nothing.
///
/// Everything runs on the main queue: Core Location delivers to the run loop of the thread that
/// made the manager.
final class LocationPushService: NSObject, CLLocationPushServiceExtension, CLLocationManagerDelegate {
    private var completion: (() -> Void)?
    private var manager: CLLocationManager?
    private var task: URLSessionDataTask?
    private var timers: [DispatchWorkItem] = []
    private var startedAt = Date()
    private var posted = false
    /// Bumped per push, so a late callback from an earlier push can't complete a newer one.
    private var generation = 0

    func didReceiveLocationPushPayload(_ payload: [String: Any], completion: @escaping () -> Void) {
        DispatchQueue.main.async { self.start(completion) }
    }

    func serviceExtensionWillTerminate() {
        // iOS is about to stop us: give up on whatever is still running and complete now.
        if Thread.isMainThread {
            finish()
        } else {
            DispatchQueue.main.sync { self.finish() }
        }
    }

    // MARK: steps

    private func start(_ completion: @escaping () -> Void) {
        if let previous = self.completion {
            // A new push while one is still being answered: this one's answer covers both.
            previous()
        }
        self.completion = completion
        startedAt = Date()
        posted = false
        generation += 1
        timers.forEach { $0.cancel() }
        timers = []
        let sharing = LocationPushRule.sharedDefaults?.bool(forKey: LocationPushRule.sharingKey) ?? false
        let signedIn = ConnectionStore.shared.token != nil && ConnectionStore.shared.serverURL != nil
        let m = manager ?? CLLocationManager()
        let status = m.authorizationStatus
        guard LocationPushRule.shouldRespond(sharing: sharing, signedIn: signedIn,
                                             locationDenied: status == .denied || status == .restricted) else {
            finish()
            return
        }
        guard LocatePush.claim(LocationPushRule.sharedDefaults, now: Date().timeIntervalSince1970) else {
            finish() // this tap was already answered
            return
        }
        manager = m
        m.delegate = self
        m.desiredAccuracy = kCLLocationAccuracyNearestTenMeters
        after(LocationPushRule.budgetSeconds) { [weak self] in self?.finish() }
        after(LocationPushRule.fixWaitSeconds) { [weak self] in self?.noFreshFix() }
        m.requestLocation()
    }

    private func after(_ seconds: Double, _ work: @escaping () -> Void) {
        let item = DispatchWorkItem(block: work)
        timers.append(item)
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: item)
    }

    /// No fresh fix in time (or Core Location failed): Core Location's newest one if it's recent.
    private func noFreshFix() {
        guard !posted, completion != nil else { return }
        if let cached = manager?.location,
           LocationPushRule.usableCachedFix(age: Date().timeIntervalSince(cached.timestamp), accuracy: cached.horizontalAccuracy) {
            post(cached)
        } else {
            finish()
        }
    }

    private func post(_ loc: CLLocation) {
        guard !posted, completion != nil else { return }
        posted = true
        manager?.stopUpdatingLocation()
        let body = LocationPushRule.reportBody(
            lat: loc.coordinate.latitude, lon: loc.coordinate.longitude,
            accuracyM: loc.horizontalAccuracy >= 0 ? loc.horizontalAccuracy : nil,
            at: loc.timestamp.timeIntervalSince1970,
            speedMps: loc.speed >= 0 ? loc.speed : nil)
        send(JSONBody.encode(body), to: ConnectionStore.shared.addresses)
    }

    /// Posts the report to the hub address the app chose last (ConnectionStore.serverURL); if
    /// that can't be reached at all - the phone left home since - the hub's other saved address,
    /// while there's time left.
    private func send(_ body: Data, to addresses: [URL]) {
        guard let base = addresses.first, let url = ServerAddress.url(base, "api/me/location/report") else {
            finish()
            return
        }
        var req = NewmanURLSession.request(url, method: "POST", body: body)
        let left = LocationPushRule.postTimeout(elapsed: Date().timeIntervalSince(startedAt))
        req.timeoutInterval = addresses.count > 1 ? max(LocationPushRule.minPostSeconds, left / 2) : left
        let gen = generation
        task = NewmanURLSession.shared.dataTask(with: req) { [weak self] _, response, _ in
            let http = response as? HTTPURLResponse
            let status = http?.statusCode ?? 0
            DispatchQueue.main.async {
                guard let self, self.generation == gen else { return }
                if http == nil, addresses.count > 1, self.completion != nil,
                   LocationPushRule.budgetSeconds - 0.5 - Date().timeIntervalSince(self.startedAt) >= LocationPushRule.minPostSeconds {
                    self.send(body, to: Array(addresses.dropFirst()))
                    return
                }
                if status == 403 {
                    // The hub says this account isn't sharing (switched off elsewhere): stop answering.
                    LocationPushRule.sharedDefaults?.set(false, forKey: LocationPushRule.sharingKey)
                }
                self.finish()
            }
        }
        task?.resume()
    }

    private func finish() {
        timers.forEach { $0.cancel() }
        timers = []
        if !posted { task?.cancel() }
        task = nil
        manager?.stopUpdatingLocation()
        guard let done = completion else { return }
        completion = nil
        done()
    }

    // MARK: CLLocationManagerDelegate (main queue)

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        post(loc)
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        noFreshFix()
    }
}
