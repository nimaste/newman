import Foundation

/// Muting one of a device's problems ("No AC power" on a keypad that's meant to run on its battery):
/// PUT /api/entities/{id}/mute {"issue", "for"} and DELETE /api/entities/{id}/mute/{issue}, each answered
/// with the device's health. A muted problem leaves health.ok / issues (so every count and chip skips
/// it) and stays listed in health.items with its `muted`.
enum MuteLogic {
    struct Choice: Hashable {
        var mode: String
        var label: String
    }

    /// For how long, in the order the mute sheet offers them.
    static let choices = [Choice(mode: "day", label: "For 1 day"), Choice(mode: "week", label: "For 1 week"),
                          Choice(mode: "fixed", label: "Until it’s fixed"), Choice(mode: "always", label: "Always")]

    /// The problems to show: the unmuted ones from health.items, or an older hub's plain issues (no id -
    /// those can't be muted).
    static func active(_ e: Entity) -> [Health.Item] {
        if let items = e.health?.items { return items.filter { $0.muted == nil } }
        return e.issues.map { Health.Item(id: "", text: $0, muted: nil) }
    }

    /// The muted ones (only newer hubs say).
    static func muted(_ e: Entity) -> [Health.Item] {
        (e.health?.items ?? []).filter { $0.muted != nil }
    }

    static func canMute(_ item: Health.Item) -> Bool { !item.id.isEmpty }

    /// "Muted until Sunday, Oct 25, 9:15 AM" / "Muted until it’s fixed" / "Muted always".
    static func mutedText(_ m: Health.Muted, timeZone: TimeZone = .current) -> String {
        switch m.mode {
        case "fixed": return "Muted until it’s fixed"
        case "always": return "Muted always"
        default:
            guard let until = m.until else { return "Muted" }
            let f = DateFormatter()
            f.locale = Locale(identifier: "en_US_POSIX")
            f.timeZone = timeZone
            f.dateFormat = "EEEE, MMM d, h:mm a"
            return "Muted until " + f.string(from: Date(timeIntervalSince1970: until))
        }
    }

    static func body(issue: String, mode: String) -> [String: JSONValue] {
        ["issue": .string(issue), "for": .string(mode)]
    }

    /// The device's health from the hub's {"health": …} answer; nil when it has none.
    static func health(from data: Data) -> Health? {
        (try? JSONDecoder().decode(Reply.self, from: data))?.health
    }

    private struct Reply: Decodable {
        var health: Health?
    }
}
