import Foundation

/// The hub names icons the way web/src/icons.jsx draws them ("lock", "bulb", "shield"…: page
/// icons, command icons, notification buttons). These are their SF Symbols; anything unknown
/// gets a neutral symbol, so a new icon name never breaks a screen.
enum Symbols {
    private static let map: [String: String] = [
        "home": "house", "house": "house", "shield": "shield", "bulb": "lightbulb", "light": "lightbulb",
        "bulb-off": "lightbulb.slash", "thermometer": "thermometer.medium", "lock": "lock", "unlock": "lock.open",
        "garage": "door.garage.closed", "bell": "bell", "bell-off": "bell.slash", "camera": "video", "power": "power",
        "fan": "fanblades", "up": "arrow.up", "down": "arrow.down", "pause": "pause", "play": "play", "stop": "stop",
        "sliders": "slider.horizontal.3", "refresh": "arrow.clockwise", "send": "paperplane", "pointer": "hand.tap",
        "volume": "speaker.wave.2", "speaker": "hifispeaker", "sun": "sun.max", "grid": "square.grid.2x2",
        "calendar": "calendar", "cloud": "cloud", "vacuum": "circle.circle", "server": "server.rack", "clock": "clock",
        "x": "xmark", "check": "checkmark", "alert": "exclamationmark.triangle", "people": "person.2", "person": "person",
        "door": "door.left.hand.closed", "motion": "figure.walk.motion", "gauge": "gauge.medium", "droplet": "drop",
        "flame": "flame", "toggle": "switch.2", "megaphone": "megaphone", "webhook": "link", "blinds": "blinds.horizontal.closed",
        "star": "star", "heart": "heart", "car": "car", "tv": "tv", "leaf": "leaf", "bolt": "bolt", "wifi": "wifi",
        "dot": "circle.fill", "settings": "gearshape", "user": "person.crop.circle", "map": "map", "plug": "powerplug",
    ]

    static func name(_ icon: String?) -> String {
        guard let icon, !icon.isEmpty else { return "square.grid.2x2" }
        return map[icon.lowercased()] ?? "square.grid.2x2"
    }

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
