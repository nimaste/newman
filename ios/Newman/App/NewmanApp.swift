import SwiftUI
import UIKit
import UserNotifications

@main
struct NewmanApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = AppModel.shared
    @Environment(\.scenePhase) private var scenePhase

    init() {
        // Larger Text, Increase Contrast and Reduce Transparency reach the shared look (Theme,
        // AppFont) on the phone; the wall keeps its fixed sizes.
        Theme.followsAccessibilitySettings = true
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .environmentObject(PushManager.shared)
                .preferredColorScheme(.dark)
                .task {
                    await HubRoute.shared.check() // the hub's home or outside address, before anything loads
                    await model.start()
                }
                .onOpenURL { model.open($0) } // newman://connect?... and newman://device?id=... (widgets)
        }
        .onChange(of: scenePhase) { _, phase in
            // Re-probes of the hub's home address (while it fails, or while outside) run only in the foreground.
            HubRoute.shared.setActive(phase == .active)
            if phase == .active {
                model.foreground()
                Task { await PushManager.shared.resendPressReports() } // reports a background press couldn't send
            }
            if phase == .background { WidgetRefresh.reloadAll() } // the widgets show what the app last changed
        }
    }
}

extension AppModel {
    static let shared = AppModel()
}

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    /// When this process started (press reports: launched for the button, or already running).
    static var launchedAt: Date?

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        AppDelegate.launchedAt = Date()
        LocalNetworkProbe.run()
        UNUserNotificationCenter.current().delegate = self
        let background = application.applicationState == .background // a location event or a silent push woke us
        Task { @MainActor in
            PushManager.shared.restoreCachedCategories()
            await PushManager.shared.refreshAuthorization()
            if background { await PushManager.shared.refreshCategoriesIfStale() } // the foreground start() refreshes them anyway
        }
        // Location sharing: iOS may have launched us in the background for a location event
        // (significant change / visit); the services must be running again to receive it.
        MainActor.assumeIsolated {
            LocationSharing.shared.resume()
            HubRoute.shared.startMonitoring() // home <-> away: switch the hub address
        }
        return true
    }

    /// Silent pushes. {"newman":{"type":"locate"}}: someone tapped this person on their map - ONE
    /// fresh fix (source "refresh"), nothing shown to anyone.
    func application(_ application: UIApplication, didReceiveRemoteNotification userInfo: [AnyHashable: Any],
                     fetchCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void) {
        // Any wake also refreshes the notification button sets (at most every 15 min), so new
        // automations' buttons show without the app being opened.
        guard LocatePush.isLocate(userInfo) else {
            Task { @MainActor in
                await PushManager.shared.refreshCategoriesIfStale()
                completionHandler(.noData)
            }
            return
        }
        Task { @MainActor in
            await LocationSharing.shared.locateNow()
            completionHandler(.newData)
        }
        Task { @MainActor in await PushManager.shared.refreshCategoriesIfStale() }
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        Task { @MainActor in PushManager.shared.didRegister(deviceToken: deviceToken) }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        Task { @MainActor in PushManager.shared.didFailToRegister(error) }
    }

    // Show Newman's notifications while the app is open too.
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .list, .sound])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let action = response.actionIdentifier
        let info = response.notification.request.content.userInfo
        if action == UNNotificationDefaultActionIdentifier || action == "open" {
            let alert = PushPayload.alertClip(userInfo: info) // a camera alert opens on its video
            Task { @MainActor in
                AppModel.shared.showAlerts()
                completionHandler()
                if let alert { await AppModel.shared.openAlert(clip: alert.clip, reviewId: alert.reviewId) }
            }
            return
        }
        if action == UNNotificationDismissActionIdentifier {
            completionHandler()
            return
        }
        Task { @MainActor in
            // Not a .foreground button: iOS runs (or launches) the app in the background for it and
            // may suspend it mid-request unless it asks for background time - as the Home Assistant
            // app does around its button webhooks. Held until the press and its report are done.
            let state = PushManager.appStateName()
            let work = BackgroundWork("newman.press")
            await PushManager.shared.perform(button: action, userInfo: info, launchState: state, work: work)
            completionHandler()
            work.end()
        }
    }
}
