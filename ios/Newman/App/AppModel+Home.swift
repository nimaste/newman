import SwiftUI
import UIKit

/// The Home tab (GET /api/home): reloaded on start, on {"type": "home"} and when settings change; Edit
/// Home and Rooms on Climate save through here. Also the account's password and opening an alert from
/// a notification of any age.
extension AppModel {
    func reloadHome() async {
        if let h = try? await APIClient.shared.home() { home = h }
    }

    /// Read Home again shortly (live messages can come in bursts).
    func homeSoon() {
        homeReload?.cancel()
        homeReload = Task {
            try? await Task.sleep(nanoseconds: 400_000_000)
            if Task.isCancelled { return }
            await reloadHome()
        }
    }

    /// May make and change automations: what Home says, else the account (admins always).
    var canAutomate: Bool { home?.canAutomate ?? user?.canAutomate ?? (user?.role == "admin") }

    /// An admin account (house settings such as the Cameras tab's big picture).
    var isAdmin: Bool { user?.role == "admin" }

    /// The Cameras tab's big picture: latest activity, or one featured camera (PATCH /api/settings/house).
    @discardableResult
    func setCameraMode(_ mode: String, featured: String?) async -> Bool {
        do {
            try await APIClient.shared.patchHouseSettings(["cameras": .object([
                "mode": .string(mode), "featured": featured.map { JSONValue.string($0) } ?? JSONValue.null])])
            await reloadHome()
            return true
        } catch {
            show((error as? APIError)?.message ?? error.localizedDescription, error: true)
            return false
        }
    }

    /// Today's "+ Add" (admins): a new upkeep to-do, due today, coming back every so often.
    @discardableResult
    func addTodo(name: String, every: Int, unit: String) async -> Bool {
        do {
            try await APIClient.shared.addUpkeep(name: name, every: every, unit: unit,
                                                 lastDone: TodayScreenLogic.lastDoneForDueToday(every: every, unit: unit, now: Date()))
            await reloadToday()
            return true
        } catch {
            show((error as? APIError)?.message ?? error.localizedDescription, error: true)
            return false
        }
    }

    /// Edit Home: this person's cards (on/off, order) and favourites.
    @discardableResult
    func saveHome(cards: [HomeData.Card], favourites: [String]) async -> Bool {
        do {
            try await APIClient.shared.saveHome(HomeLogic.editHomeBody(cards: cards, favourites: favourites))
            await reloadHome()
            return true
        } catch {
            show((error as? APIError)?.message ?? error.localizedDescription, error: true)
            return false
        }
    }

    /// Rooms on Climate (the whole house's).
    @discardableResult
    func saveClimateRooms(_ rooms: [HomeData.Room]) async -> Bool {
        do {
            try await APIClient.shared.saveClimateRooms(HomeLogic.climateBody(rooms))
            await reloadHome()
            return true
        } catch {
            show((error as? APIError)?.message ?? error.localizedDescription, error: true)
            return false
        }
    }

    /// Change password: the hub's own words when it refuses (wrong current password, too short…).
    func changePassword(current: String, new: String) async -> String? {
        do {
            try await APIClient.shared.changePassword(current: current, new: new)
            show("Password changed. Your other devices were signed out.")
            return nil
        } catch {
            return (error as? APIError)?.message ?? error.localizedDescription
        }
    }

    /// A tapped notification of any age (its alertId = the automation run, GET /api/events?runId=):
    /// a camera alert opens its camera's screen and plays the clip through the signed-in API (never
    /// the notification's expiring link); anything else opens More › Alerts at that entry.
    func openAlert(id: String) async {
        for attempt in 0..<2 {
            if attempt > 0 { try? await Task.sleep(nanoseconds: 2_000_000_000) } // e.g. still finding the hub's address
            guard let found = try? await APIClient.shared.events(runId: id) else { continue }
            guard let entry = found.first else { break }
            switch NotificationAlert.target(entry) {
            case .camera(let cam, let alert):
                selectedTab = "cameras"
                camerasPath = [.camera(cam)]
                openCamera = CameraTarget(entityId: cam, cameraIds: [cam], alert: alert)
            case .alerts(let eventId):
                if !events.contains(where: { $0.id == eventId }) { events.append(entry) } // older than the list
                showAlerts()
                highlightedAlert = "e:" + eventId
            }
            return
        }
        showAlerts()
    }

    // MARK: the automation editor

    func loadAutomation(_ id: String) async -> AutomationDraft? {
        do {
            return AutomationDraft(raw: try await APIClient.shared.automation(id))
        } catch {
            show((error as? APIError)?.message ?? error.localizedDescription, error: true)
            return nil
        }
    }

    /// Saves it; the saved automation back (nil when the hub refused - its words shown).
    func saveAutomation(_ draft: AutomationDraft) async -> AutomationDraft? {
        do {
            let saved = AutomationDraft(raw: try await APIClient.shared.saveAutomation(draft))
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            await reloadAutomations()
            return saved
        } catch {
            show((error as? APIError)?.message ?? error.localizedDescription, error: true)
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            return nil
        }
    }

    func deleteAutomation(_ id: String) async -> Bool {
        do {
            try await APIClient.shared.deleteAutomation(id)
            await reloadAutomations()
            return true
        } catch {
            show((error as? APIError)?.message ?? error.localizedDescription, error: true)
            return false
        }
    }

    /// Runs one now by id (the editor's Run now).
    @discardableResult
    func runAutomation(id: String, name: String) async -> Bool {
        await runAutomation(AutomationSummary(id: id, name: name))
    }
}
