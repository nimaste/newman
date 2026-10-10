import Foundation
import os
import UIKit
import WebKit

/// Tells the hub how the tablet's battery is doing (POST /api/tablet/battery): when the wall
/// becomes active and the page is signed in, every 5 minutes, and early when charging starts/stops
/// or the level moves 5 points. The session is the wall page's own: its cookies for the hub
/// (newman_session, and Cloudflare's CF_Authorization) are read from the web view's cookie store;
/// without a session cookie for the current address (the address changed) the tablet's token is sent
/// instead (Authorization: Bearer - kept from the page by keepToken, works on any address of the hub).
/// Requests go through NewmanURLSession, which presents the client certificate; the Cloudflare
/// service-token headers come from NewmanURLSession.request. Not signed in yet: skipped quietly. A
/// failed report (401, an older hub's 404, no network) is logged (subsystem ca.nimaste.newman.wall,
/// category NewmanBattery) and tried again an interval later.
///
/// Screen: reports say "screen": "on" while the wall is in front (one at once when it comes to the
/// front), and stop() - the wall going inactive or to the background, the screen locking - sends a
/// last one with "screen": "off" (best effort, in a short background task). iPadOS gives an app no
/// background schedule, so nothing more comes until it's woken; the hub keeps it as "Screen off" (its
/// battery unknown after 15 minutes) and only calls it offline after 12 hours.
@MainActor
final class BatteryReporter {
    private let schedule = BatterySchedule()
    private var timer: Timer?
    private var observers: [NSObjectProtocol] = []

    nonisolated init() {}

    /// The schedule decides; a cheap look once a minute keeps "every 5 minutes" close.
    private static let tick: TimeInterval = 60

    func start() {
        guard timer == nil else { return }
        UIDevice.current.isBatteryMonitoringEnabled = true
        let nc = NotificationCenter.default
        for name in [UIDevice.batteryLevelDidChangeNotification, UIDevice.batteryStateDidChangeNotification] {
            observers.append(nc.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in self?.check() }
            })
        }
        timer = Timer.scheduledTimer(withTimeInterval: Self.tick, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.check() }
        }
        schedule.forceNext()  // "screen on" for the hub straight away
        check()
    }

    func stop() {
        let wasRunning = timer != nil
        timer?.invalidate()
        timer = nil
        for o in observers { NotificationCenter.default.removeObserver(o) }
        observers = []
        if wasRunning { sendScreenOff() }
    }

    /// The last report before the iPad sleeps: "screen": "off", with a little background time to finish.
    private func sendScreenOff() {
        let device = UIDevice.current
        guard let reading = TabletBattery.fromIOS(level: device.batteryLevel, stateRaw: device.batteryState.rawValue),
              let base = ConnectionStore.shared.serverURL,
              let url = ServerAddress.url(base, TabletBattery.path) else { return }
        let app = UIApplication.shared
        // A box, not a captured var: the expiry handler and the Task are @Sendable closures.
        let task = BackgroundTask()
        task.id = app.beginBackgroundTask(withName: "Newman battery") {
            // iOS calls this on the main thread.
            MainActor.assumeIsolated { task.end(app) }
        }
        Task { @MainActor in
            _ = await send(reading.with(screenOn: false), to: url)
            task.end(app)
        }
    }

    /// The background task's id, ended once (by whichever comes first: the send or iOS's expiry).
    private final class BackgroundTask: @unchecked Sendable {
        var id = UIBackgroundTaskIdentifier.invalid

        @MainActor func end(_ app: UIApplication) {
            guard id != .invalid else { return }
            let ended = id
            id = .invalid
            app.endBackgroundTask(ended)
        }
    }

    /// Look now (e.g. the page may have just signed in).
    func check() {
        guard timer != nil else { return }
        let device = UIDevice.current
        guard let reading = TabletBattery.fromIOS(level: device.batteryLevel, stateRaw: device.batteryState.rawValue),
              schedule.shouldSend(now: Self.now, reading),
              let base = ConnectionStore.shared.serverURL,
              let url = ServerAddress.url(base, TabletBattery.path) else { return }
        schedule.started()
        Task { @MainActor in
            switch await send(reading.with(screenOn: true), to: url) {
            case .sent: schedule.succeeded(now: Self.now, reading)
            case .notSignedIn: schedule.skipped()
            case .failed: schedule.failed(now: Self.now)
            }
        }
    }

    private enum Outcome { case sent, notSignedIn, failed }

    private static let log = Logger(subsystem: "ca.nimaste.newman.wall", category: "NewmanBattery")

    /// Keeps the page's tablet token (localStorage "newman.tablet.token", set by the hub's wall page at
    /// sign-in) for reports without a session cookie. Only from the hub's own page; called when a page
    /// finishes loading.
    static func keepToken(from webView: WKWebView) {
        let store = ConnectionStore.shared
        let hubHosts = ([store.serverURL, store.hub?.localURL, store.hub?.outsideURL].compactMap { $0?.host?.lowercased() })
        guard let host = webView.url?.host?.lowercased(), hubHosts.contains(host) else { return }
        webView.evaluateJavaScript("(function(){try{return localStorage.getItem('newman.tablet.token');}catch(e){return null;}})()") { value, _ in
            guard let token = value as? String, !token.isEmpty else { return }
            Task { @MainActor in
                if ConnectionStore.shared.token != token { ConnectionStore.shared.token = token }
            }
        }
    }

    private func send(_ reading: TabletBattery, to url: URL) async -> Outcome {
        guard let host = url.host else { return .failed }
        let https = url.scheme?.lowercased() == "https"
        let all = await WKWebsiteDataStore.default().httpCookieStore.allCookies()
        let cookies = all.filter { c in
            TabletBattery.cookieDomainMatches(c.domain, host: host)
                && (!c.isSecure || https)
                && (c.expiresDate.map { $0 > Date() } ?? true)
        }
        let hasSession = cookies.contains(where: { $0.name == TabletBattery.sessionCookie && !$0.value.isEmpty })
        // The page's session cookie when there is one for this address; else the tablet's token.
        guard hasSession || ConnectionStore.shared.token != nil else { return .notSignedIn }
        var req = NewmanURLSession.request(url, method: "POST", body: reading.json, includeToken: !hasSession)
        req.httpShouldHandleCookies = false
        // Cookies only from the web view's store (Cloudflare's too, with the token).
        if !cookies.isEmpty {
            for (k, v) in HTTPCookie.requestHeaderFields(with: cookies) { req.setValue(v, forHTTPHeaderField: k) }
        }
        let how = hasSession ? "cookie" : "token"
        do {
            let (_, response) = try await NewmanURLSession.shared.data(for: req)
            guard let http = response as? HTTPURLResponse else { return .failed }
            if (200..<300).contains(http.statusCode) { return .sent }
            Self.log.warning("battery report to \(host, privacy: .public) failed (\(how, privacy: .public)): HTTP \(http.statusCode)")
            return .failed
        } catch {
            Self.log.warning("battery report to \(host, privacy: .public) failed: \(error.localizedDescription, privacy: .public)")
            return .failed
        }
    }

    private static var now: TimeInterval { ProcessInfo.processInfo.systemUptime }
}
