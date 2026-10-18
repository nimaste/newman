import SwiftUI
import UIKit

/// Muting one of a device's problems from the To do card or its sheet; the hub answers with the
/// device's health, which replaces ours at once (the live update follows), then To do is read again.
extension AppModel {
    func mute(_ entityId: String, issue: String, mode: String) async {
        do {
            let health = try await APIClient.shared.mute(entityId, issue: issue, mode: mode)
            if let health { entities[entityId]?.health = health }
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            await reloadToday()
        } catch {
            show((error as? APIError)?.message ?? error.localizedDescription, error: true)
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        }
    }

    func unmute(_ entityId: String, issue: String) async {
        do {
            let health = try await APIClient.shared.unmute(entityId, issue: issue)
            if let health { entities[entityId]?.health = health }
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            await reloadToday()
        } catch {
            show((error as? APIError)?.message ?? error.localizedDescription, error: true)
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        }
    }
}
