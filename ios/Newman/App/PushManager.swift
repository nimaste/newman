import Foundation
import UIKit
import UserNotifications

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
    /// the push's fixed `action_url` last (PushPayload.actionURLs). Same session as every other call:
    /// client certificate and Cloudflare headers. Failures come back as a local notification with
    /// the hub's words.
    func perform(button: String, userInfo: [AnyHashable: Any]) async {
        if PushPayload.isLocalButton(button) { return }
        guard let req = PushPayload.actionRequest(userInfo: userInfo, button: button, deviceId: ConnectionStore.shared.deviceId) else { return }
        let body = JSONBody.encode(req.body)
        var problem: String?
        for url in PushPayload.actionURLs(actionURL: req.url, addresses: ConnectionStore.shared.addresses) {
            var r = NewmanURLSession.request(url, method: "POST", body: body, includeToken: false)
            r.timeoutInterval = PushPayload.actionAttemptSeconds
            do {
                let (data, resp) = try await NewmanURLSession.shared.data(for: r)
                let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
                if (200..<300).contains(status) {
                    problem = nil
                    break
                }
                problem = JSONBody.errorText(data) ?? (status == 401 ? "That notification has expired." : "The hub refused (\(status)).")
                if !PushPayload.tryNextAddress(status: status) { break }
            } catch {
                problem = APIError.network(error).message
            }
        }
        if let problem { await notifyFailure(problem, userInfo: userInfo) }
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
