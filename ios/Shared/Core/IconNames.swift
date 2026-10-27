import Foundation

/// Icon names as the hub and the web use them ("lock", "bulb", "shield"…: kinds' icons, command icons,
/// the icons a device can be given in Newman - GET /api/meta "icons", which also lists every kind's id)
/// and the SF Symbol each is drawn with. Anything unknown gets a neutral symbol, so a new name never
/// breaks a screen. The one table every target uses (Symbols.name in the app, widgets and wall).
enum IconNames {
    static let symbols: [String: String] = [
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
        // Icons a device can be given in Newman, and kinds' own ids (a device can be drawn as another kind).
        "appliance": "washer", "outlet": "powerplug", "lamp": "lamp.desk", "heater": "heater.vertical",
        "pump": "drop.circle", "pool": "figure.pool.swim", "sprinkler": "sprinkler.and.droplets", "garden": "leaf",
        "printer": "printer", "router": "wifi.router", "battery": "battery.75", "water": "drop",
        "window": "window.vertical.closed", "sensor": "sensor", "button": "button.programmable",
        "switch": "switch.2", "contact": "door.left.hand.closed", "leak": "drop.triangle", "smoke": "smoke",
        "temperature": "thermometer.medium", "thermostat": "thermometer.medium", "tablet": "ipad", "remote": "av.remote",
        "weather": "cloud.sun", "alarm": "shield", "doorbell": "bell", "zone": "square.dashed", "container": "shippingbox",
        "disk": "internaldrive", "netcheck": "network", "recipe_book": "book", "music_player": "music.note",
        "vehicle": "car",
    ]

    /// The neutral symbol for a name nobody knows.
    static let fallback = "square.grid.2x2"

    static func knows(_ name: String?) -> Bool {
        guard let name, !name.isEmpty else { return false }
        return symbols[name.lowercased()] != nil
    }

    static func symbol(_ name: String?) -> String {
        guard let name, !name.isEmpty else { return fallback }
        return symbols[name.lowercased()] ?? fallback
    }
}
