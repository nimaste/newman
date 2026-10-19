import Foundation

/// The hub names icons the way web/src/icons.jsx draws them ("lock", "bulb", "shield"…: page
/// icons, command icons, notification buttons). These are their SF Symbols; anything unknown
/// gets a neutral symbol, so a new icon name never breaks a screen.
enum Symbols {

    /// The SF Symbol for an icon name (IconNames, the one table in Shared/Core).
    static func name(_ icon: String?) -> String { IconNames.symbol(icon) }

    /// Weather conditions (the weather device's states) as SF Symbols, day or night.
    static func weather(_ condition: String?, isDay: Bool = true) -> String {
        switch condition ?? "" {
        case "clear": return isDay ? "sun.max" : "moon.stars"
        case "mostly-clear", "partly-cloudy": return isDay ? "cloud.sun" : "cloud.moon"
        case "cloudy": return "cloud"
        case "fog": return "cloud.fog"
        case "drizzle": return "cloud.drizzle"
        case "rain", "showers": return "cloud.rain"
        case "freezing-rain": return "cloud.sleet"
        case "snow", "snow-showers": return "cloud.snow"
        case "thunderstorm": return "cloud.bolt.rain"
        default: return "cloud"
        }
    }
}
