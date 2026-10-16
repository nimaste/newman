import SwiftUI
import UIKit

/// More › Automations: the list (GET /api/automations), switching one on or off (PATCH) and running
/// one now (POST …/run). Only admin accounts may change them: the hub answers 403 with its own
/// words, which show as a toast while the switch goes back.
extension AppModel {
    func reloadAutomations() async {
        do {
            automations = try await APIClient.shared.automations()
        } catch {
            if automations == nil { show((error as? APIError)?.message ?? error.localizedDescription, error: true) }
        }
    }

    /// Switches it at once; puts it back with the hub's error if the hub refuses.
    func setAutomation(_ id: String, enabled: Bool) async {
        guard let before = automations else { return }
        automations = AutomationList.setting(before, id: id, enabled: enabled)
        do {
            let saved = try await APIClient.shared.setAutomation(id, enabled: enabled)
            automations = AutomationList.setting(automations ?? before, id: id, enabled: saved.enabled)
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        } catch {
            automations = AutomationList.setting(automations ?? before, id: id, enabled: !enabled)
            show((error as? APIError)?.message ?? error.localizedDescription, error: true)
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        }
    }

    /// Runs it now (its conditions are still checked by the hub); true when the hub started it.
    @discardableResult
    func runAutomation(_ a: AutomationSummary) async -> Bool {
        do {
            let run = try await APIClient.shared.runAutomation(a.id)
            let status = AutomationList.statusLabel(run?.status)
            show(status.isEmpty ? "Ran \(a.name)" : "\(a.name): \(status.lowercased())")
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            await reloadAutomations()
            return true
        } catch {
            show((error as? APIError)?.message ?? error.localizedDescription, error: true)
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            return false
        }
    }
}
