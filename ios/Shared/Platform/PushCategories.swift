import Foundation
import UserNotifications

/// The notification categories iOS shows a push's buttons from: a push names its category, and
/// iOS shows no buttons unless that category is registered on the phone. Only the app registers
/// them - every button set the hub can foresee (GET /api/push/categories, PushManager), at launch,
/// in the foreground and on background wakes. Never from the Notification Service Extension: on
/// iOS 26, an extension that registered a push's category stopped every later Newman
/// notification from showing until the phone restarted or the app was reinstalled (2026-10-10).
enum PushCategories {
    /// Category changes run here, never inside UserNotifications' own completion handlers.
    private static let queue = DispatchQueue(label: "ca.nimaste.newman.push-categories")

    static func action(_ b: PushButton) -> UNNotificationAction {
        let style = PushPayload.actionStyle(b)
        var options: UNNotificationActionOptions = []
        if style.authenticationRequired { options.insert(.authenticationRequired) } // unlock/open: Face ID or passcode first
        if style.foreground { options.insert(.foreground) }
        if style.destructive { options.insert(.destructive) }
        let icon = UNNotificationActionIcon(systemImageName: Symbols.name(b.icon))
        return UNNotificationAction(identifier: b.id, title: b.label, options: options, icon: icon)
    }

    static func category(_ cat: PushCategory) -> UNNotificationCategory {
        UNNotificationCategory(identifier: cat.id, actions: cat.buttons.map(action), intentIdentifiers: [], options: [])
    }

    /// Exactly these categories, replacing every other one (the hub's full list): one call, no
    /// read-modify-write, and nothing left over from earlier builds.
    static func replaceAll(_ cats: [PushCategory]) {
        let all = Set(cats.map(category))
        queue.async { UNUserNotificationCenter.current().setNotificationCategories(all) }
    }
}
