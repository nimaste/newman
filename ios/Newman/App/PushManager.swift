import Foundation
import os
import UIKit
import UserNotifications

/// Background time for work iOS started in the background (a notification button: not a
/// .foreground action, so iOS may suspend the app as soon as it looks idle - mid-request). Begun
/// before the work, ended once: when the work is done, or when iOS says the time is up.
@MainActor
final class BackgroundWork {
    private var id: UIBackgroundTaskIdentifier = .invalid
    private(set) var expired = false

    init(_ name: String) {
        id = UIApplication.shared.beginBackgroundTask(withName: name) { [weak self] in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.expired = true
                PushManager.log.error("background time for \(name, privacy: .public) ran out")
                self.end()
            }
        }
    }

    func end() {
        guard id != .invalid else { return }
        UIApplication.shared.endBackgroundTask(id)
        id = .invalid
    }
}

/// APNs: permission, registering this phone with the hub (POST /api/devices/register), the
/// notification categories and their buttons (GET /api/push/categories), and sending a tapped
/// button to the hub (POST /api/public/actions, signed by the push itself).
@MainActor
final class PushManager: ObservableObject {
    static let shared = PushManager()

    @Published var authorization: UNAuthorizationStatus = .notDetermined
    @Published var registered = false
    @Published var personId: String?
    @Published var lastError: String?

    private let categoriesKey = "pushCategoriesJSON"
    private let categoriesRefreshedKey = "pushCategoriesRefreshedAt"

    func requestAndRegister() {
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
            Task { @MainActor in
                await self.refreshAuthorization()
                if granted { UIApplication.shared.registerForRemoteNotifications() }
            }
        }
    }

    func refreshAuthorization() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        authorization = settings.authorizationStatus
    }

    /// APNs gave us a token: tell the hub which phone this is.
    func didRegister(deviceToken: Data) {
        guard ConnectionStore.shared.token != nil else { return }
        #if DEBUG
        let sandbox = true
        #else
        let sandbox = false
        #endif
        let body = PushPayload.registerBody(token: PushPayload.hexToken(deviceToken), deviceId: ConnectionStore.shared.deviceId,
                                            friendlyName: UIDevice.current.name, deviceModel: PushManager.modelIdentifier,
                                            sandbox: sandbox)
        Task {
            do {
                let reply = try await APIClient.shared.register(body: body)
                registered = true
                personId = reply.personId
                lastError = nil
                await LocationSharing.shared.sendLocationPushToken() // needs the phone registered first
            } catch {
                lastError = (error as? APIError)?.message ?? error.localizedDescription
            }
        }
    }

    func didFailToRegister(_ error: Error) {
        lastError = error.localizedDescription
    }

    // MARK: categories

    /// Registers every button set the hub may send (exactly those: PushCategories.replaceAll), and
    /// keeps a copy for the next cold start. Runs at launch, each time the app comes to the
    /// foreground, and on background wakes (refreshCategoriesIfStale).
    func refreshCategories() async {
        guard let data = try? await APIClient.shared.call("GET", "api/push/categories"),
              let cats = try? JSONDecoder().decode([PushCategory].self, from: data) else { return }
        UserDefaults.standard.set(data, forKey: categoriesKey)
        UserDefaults.standard.set(Date().timeIntervalSince1970, forKey: categoriesRefreshedKey)
        PushCategories.replaceAll(cats)
    }

    /// iOS woke the app in the background (a silent push, a location event): refresh the button
    /// sets too, at most every PushPayload.categoryRefreshMinSeconds, so new automations' buttons
    /// show without the app being opened.
    func refreshCategoriesIfStale() async {
        guard ConnectionStore.shared.token != nil else { return }
        let last = UserDefaults.standard.object(forKey: categoriesRefreshedKey) as? Double
        guard PushPayload.shouldRefreshCategories(lastAt: last, now: Date().timeIntervalSince1970) else { return }
        await refreshCategories()
    }

    func restoreCachedCategories() {
        guard let data = UserDefaults.standard.data(forKey: categoriesKey),
              let cats = try? JSONDecoder().decode([PushCategory].self, from: data) else { return }
        PushCategories.replaceAll(cats)
    }

    // MARK: buttons

    /// A notification button was tapped: send it to the hub with the push's own signed token -
    /// to the address the app uses right now (HubRoute's pick), then the hub's other address, and
    /// the push's fixed `action_url` last (PushPayload.actionURLs). Each try on its own fresh
    /// connection (PressProbe), with the client certificate and Cloudflare headers; the first up to
    /// 20 s, the rest up to 8 s, all within iOS's background time (PushPayload.attemptSeconds).
    /// Failures come back as a local notification with the hub's words. Every press is reported to
    /// the hub afterwards (POST /api/public/press-report): each try in full when it failed.
    /// `launchState`: how the app was running when the button arrived; `work`: the background time
    /// the caller holds for this press.
    func perform(button: String, userInfo: [AnyHashable: Any], launchState: String = "?", work: BackgroundWork? = nil) async {
        if PushPayload.isLocalButton(button) { return }
        guard let req = PushPayload.actionRequest(userInfo: userInfo, button: button, deviceId: ConnectionStore.shared.deviceId) else { return }
        let body = JSONBody.encode(req.body)
        let urls = PushPayload.actionURLs(actionURL: req.url, addresses: ConnectionStore.shared.addresses)
        let started = Date()
        var problem: String?
        var outcome = "failed"
        var ok = false
        var attempts: [PressAttempt] = []
        for (i, url) in urls.enumerated() {
            let left = Self.backgroundLeft()
            guard let seconds = PushPayload.attemptSeconds(index: i, elapsed: Date().timeIntervalSince(started), backgroundLeft: left) else {
                Self.log.error("no time left for try \(i + 1, privacy: .public) of \(urls.count, privacy: .public)")
                break
            }
            var r = NewmanURLSession.request(url, method: "POST", body: body, includeToken: false)
            r.timeoutInterval = seconds
            let res = await PressProbe.send(r, appState: Self.appStateName(), backgroundLeft: left)
            attempts.append(res.attempt)
            let line = PressReport.line(res.attempt, index: i, of: urls.count)
            if let status = res.status {
                if (200..<300).contains(status) {
                    Self.log.notice("\(line, privacy: .public)")
                    problem = nil
                    ok = true
                    outcome = "pressed"
                    break
                }
                Self.log.error("\(line, privacy: .public)")
                let words = res.data.flatMap { JSONBody.errorText($0) }
                problem = words ?? (status == 401 ? "That notification has expired." : "The hub refused (\(status)).")
                outcome = "refused \(status): \(problem ?? "")"
                if !PushPayload.tryNextAddress(status: status) { break }
            } else {
                Self.log.error("\(line, privacy: .public)")
                problem = APIError.network(res.error ?? URLError(.unknown)).message
                outcome = "failed: \(problem ?? "")"
            }
        }
        if attempts.isEmpty { problem = problem ?? "There was no time left to reach the Newman hub." }
        if let problem { await notifyFailure(problem, userInfo: userInfo) }
        let report = PressReport(
            id: UUID().uuidString.lowercased(), button: button, subject: req.body["subject"] ?? "",
            deviceId: req.body["deviceId"] ?? "", exp: req.body["exp"] ?? "", token: req.body["token"] ?? "",
            ok: ok, outcome: outcome, summary: PressReport.summary(ok: ok, outcome: outcome, attempts: attempts, tried: urls.count),
            appVersion: Self.appVersion, launchState: launchState,
            sinceLaunchSeconds: AppDelegate.launchedAt.map { (Date().timeIntervalSince($0) * 10).rounded() / 10 },
            backgroundExpired: work?.expired ?? false, actionURL: PressReport.redact(req.url),
            at: started.timeIntervalSince1970, attempts: ok ? [] : attempts)
        Self.log.notice("press \(report.summary, privacy: .public)")
        await sendReport(report)
    }

    // MARK: press reports

    nonisolated static let log = Logger(subsystem: "ca.nimaste.newman", category: "press")
    private static let pendingReportsKey = "pressReportsPending"
    private var resending = false

    /// Queued first (so a report survives the app being suspended mid-send), then sent at once to
    /// every hub address; kept for the next time the app comes to the foreground when none answers.
    func sendReport(_ report: PressReport) async {
        guard let data = try? JSONEncoder().encode(report) else { return }
        let q = UserDefaults.standard.array(forKey: Self.pendingReportsKey) as? [Data] ?? []
        UserDefaults.standard.set(PressReport.queued(q, adding: data), forKey: Self.pendingReportsKey)
        if await postReport(data, actionURL: URL(string: report.actionURL)) { dropReports([data]) }
    }

    /// Reports that couldn't be sent before (on coming to the foreground).
    func resendPressReports() async {
        guard !resending else { return }
        let q = UserDefaults.standard.array(forKey: Self.pendingReportsKey) as? [Data] ?? []
        guard !q.isEmpty else { return }
        resending = true
        defer { resending = false }
        var sent: [Data] = []
        for data in q {
            let actionURL = (try? JSONDecoder().decode(PressReport.self, from: data)).flatMap { URL(string: $0.actionURL) }
            if await postReport(data, actionURL: actionURL) { sent.append(data) }
        }
        dropReports(sent)
    }

    private func dropReports(_ sent: [Data]) {
        guard !sent.isEmpty else { return }
        let q = UserDefaults.standard.array(forKey: Self.pendingReportsKey) as? [Data] ?? []
        UserDefaults.standard.set(q.filter { !sent.contains($0) }, forKey: Self.pendingReportsKey)
    }

    /// To all of the hub's addresses at once (short timeouts); true when one took it. The hub
    /// keeps a report once (by its id) if two addresses deliver it.
    private func postReport(_ data: Data, actionURL: URL?) async -> Bool {
        let urls = PressReport.urls(addresses: ConnectionStore.shared.addresses, actionURL: actionURL)
        guard !urls.isEmpty else { return false }
        return await withTaskGroup(of: Bool.self) { group in
            for url in urls {
                group.addTask {
                    var r = NewmanURLSession.request(url, method: "POST", body: data, includeToken: true)
                    r.timeoutInterval = PressReport.sendSeconds
                    guard let pair = try? await NewmanURLSession.shared.data(for: r),
                          let status = (pair.1 as? HTTPURLResponse)?.statusCode else { return false }
                    if !(200..<300).contains(status) {
                        PushManager.log.error("press report to \(PressReport.redact(url), privacy: .public): HTTP \(status, privacy: .public)")
                    }
                    return (200..<300).contains(status)
                }
            }
            var delivered = false
            for await ok in group where ok {
                delivered = true
                group.cancelAll()
            }
            return delivered
        }
    }

    static func appStateName() -> String {
        switch UIApplication.shared.applicationState {
        case .active: return "active"
        case .inactive: return "inactive"
        case .background: return "background"
        @unknown default: return "unknown"
        }
    }

    /// iOS background time left, nil in the foreground (iOS reports a huge number then).
    static func backgroundLeft() -> Double? {
        let t = UIApplication.shared.backgroundTimeRemaining
        return t < 10_000 ? (t * 10).rounded() / 10 : nil
    }

    static var appVersion: String {
        let info = Bundle.main.infoDictionary
        return "\(info?["CFBundleShortVersionString"] as? String ?? "?") (\(info?["CFBundleVersion"] as? String ?? "?"))"
    }

    private func notifyFailure(_ text: String, userInfo: [AnyHashable: Any]) async {
        let content = UNMutableNotificationContent()
        content.title = "Couldn't do that"
        content.body = text
        if let thread = userInfo["thread"] as? String { content.threadIdentifier = thread }
        let req = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
        try? await UNUserNotificationCenter.current().add(req)
    }

    static var modelIdentifier: String {
        var info = utsname()
        uname(&info)
        let mirror = Mirror(reflecting: info.machine)
        return mirror.children.reduce(into: "") { s, el in
            if let v = el.value as? Int8, v != 0 { s.append(Character(UnicodeScalar(UInt8(v)))) }
        }
    }
}
