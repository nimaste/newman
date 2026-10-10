import Foundation

// Live camera video: the app owns the hub's live socket (api/entities/{id}/live?quality=…), the
// shared player page (mobile-shared/live-player.html) only decodes. This is the pure part -
// the page's messages, the JavaScript the app sends back, the socket address and the pool that
// caps how many tiles stream at once - so it is type-checked and tested on Linux.

enum LiveQuality: String {
    case sub, main
}

/// What the player page posts (webkit.messageHandlers.newmanLive.postMessage(obj)).
enum LivePageMessage: Equatable {
    case ready
    case codecs(String)
    case state(LivePlayerState)
    case unknown

    static func parse(_ body: Any) -> LivePageMessage {
        guard let dict = body as? [String: Any], let type = dict["type"] as? String else { return .unknown }
        switch type {
        case "ready":
            return .ready
        case "codecs":
            return .codecs(dict["value"] as? String ?? "")
        case "state":
            return .state(LivePlayerState(rawValue: dict["value"] as? String ?? "") ?? .loading)
        default:
            return .unknown
        }
    }
}

/// The page's state: failed = show the still (the app retries later); retry = reconnect now
/// (the page found it can't decode H.265 and will leave it out next time).
enum LivePlayerState: String, Equatable {
    case loading, playing, failed, retry
}

/// The JavaScript the app evaluates in the player page.
enum LiveScript {
    static let start = "NewmanLive.start()"
    static let stop = "NewmanLive.stop()"

    static func fit(cover: Bool) -> String { "NewmanLive.fit(\"\(cover ? "cover" : "contain")\")" }

    /// NewmanLive.text(<JSON string literal of the socket's text message>).
    static func text(_ message: String) -> String { "NewmanLive.text(\(jsStringLiteral(message)))" }

    /// NewmanLive.data("<base64>") for a binary socket message.
    static func data(_ bytes: Data) -> String { "NewmanLive.data(\"\(bytes.base64EncodedString())\")" }

    /// What the app sends on the socket once the page reports its codecs.
    static func mseRequest(codecs: String) -> String {
        "{\"type\":\"mse\",\"value\":\(jsStringLiteral(codecs))}"
    }

    /// A quoted, escaped string literal, valid as both JSON and JavaScript.
    static func jsStringLiteral(_ s: String) -> String {
        var out = "\""
        for u in s.unicodeScalars {
            switch u {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            case "\u{2028}": out += "\\u2028"
            case "\u{2029}": out += "\\u2029"
            default:
                if u.value < 0x20 || u == "<" {
                    // Control characters, and "<" so "</script>" can never appear.
                    out += String(format: "\\u%04x", u.value)
                } else {
                    out.unicodeScalars.append(u)
                }
            }
        }
        return out + "\""
    }

    /// wss://host/api/entities/x/live?quality=sub (ws:// for a plain-http hub at home).
    static func socketURL(base: URL, livePath: String, quality: LiveQuality) -> URL? {
        guard let u = ServerAddress.webSocketURL(base, path: livePath) else { return nil }
        return ServerAddress.with(u, "quality", quality.rawValue)
    }

    /// After "failed" (or the socket dropping) the still shows and the app tries again later.
    static let retryAfterFailure: TimeInterval = 15
}

/// At most `capacity` camera tiles stream at once (web: MAX_STREAMS in LiveVideo.jsx); the rest
/// wait in order and start when a slot frees up. The full-screen camera doesn't use the pool.
final class StreamPool {
    let capacity: Int
    private(set) var active: [String] = []
    private var waiting: [(id: String, grant: () -> Void)] = []

    init(capacity: Int) { self.capacity = capacity }

    var waitingIds: [String] { waiting.map { $0.id } }

    /// Asks for a slot. Returns true when granted now; otherwise `grant` runs later when one frees.
    @discardableResult
    func request(_ id: String, grant: @escaping () -> Void) -> Bool {
        if active.contains(id) { return true }
        if active.count < capacity {
            active.append(id)
            waiting.removeAll { $0.id == id }
            return true
        }
        if !waiting.contains(where: { $0.id == id }) { waiting.append((id, grant)) }
        return false
    }

    /// Gives a slot back (or stops waiting) and hands freed slots to the next in line.
    func release(_ id: String) {
        waiting.removeAll { $0.id == id }
        guard let i = active.firstIndex(of: id) else { return }
        active.remove(at: i)
        while active.count < capacity, !waiting.isEmpty {
            let next = waiting.removeFirst()
            active.append(next.id)
            next.grant()
        }
    }
}
