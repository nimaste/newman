import Foundation

/// Newman's alarms and timers ringing on the iPad wall (Android: core/wall/WallRinging.kt): the
/// settings' "Snooze for" / "Stop ringing after" choices, what the page asks to ring (NewmanWall.ring),
/// and Newman's own alarm sounds - iOS lets no app use the iPad's alarm tones, so the wall makes four
/// of its own here (no files): Beeps, Chimes, Bell and Rising.
enum WallRinging {
    static let snoozeChoices = [5, 9, 10, 15]
    static let ringChoices = [5, 10, 15, 30]
    static let defaultSnooze = 9
    static let defaultRing = 10

    static func snoozeSetting(_ v: Int) -> Int { snoozeChoices.contains(v) ? v : defaultSnooze }
    static func ringSetting(_ v: Int) -> Int { ringChoices.contains(v) ? v : defaultRing }

    /// NewmanWall.ringSettings() for the page (which passes them to the hub).
    static func settingsJSON(snooze: Int, ring: Int) -> String {
        #"{"snoozeMinutes":\#(snoozeSetting(snooze)),"ringMinutes":\#(ringSetting(ring))}"#
    }

    /// NewmanWall.ring(json): {"kind": "alarm" | "timer", "id", "title", "ringMs"}.
    struct Ring: Decodable, Equatable {
        var kind = "alarm"
        var id = ""
        var title = ""
        var ringMs: Double = Double(WallRinging.defaultRing * 60_000)

        var isTimer: Bool { kind == "timer" }
        /// How long it may ring: what the page says, kept between 1 and 60 minutes.
        var capSeconds: Double { Swift.min(3600, Swift.max(60, ringMs / 1000)) }

        enum CodingKeys: String, CodingKey { case kind, id, title, ringMs }

        init(kind: String = "alarm", id: String = "", title: String = "", ringMs: Double = Double(WallRinging.defaultRing * 60_000)) {
            self.kind = kind
            self.id = id
            self.title = title
            self.ringMs = ringMs
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            kind = (try? c.decode(String.self, forKey: .kind)) ?? "alarm"
            id = (try? c.decode(String.self, forKey: .id)) ?? ""
            title = (try? c.decode(String.self, forKey: .title)) ?? ""
            ringMs = (try? c.decode(Double.self, forKey: .ringMs)) ?? Double(WallRinging.defaultRing * 60_000)
        }
    }

    static func parseRing(_ text: String?) -> Ring? {
        guard let text, let data = text.data(using: .utf8), let r = try? JSONDecoder().decode(Ring.self, from: data), !r.id.isEmpty else { return nil }
        return r
    }
}

/// The wall's own alarm sounds: one loop each (a few seconds, its pause included), 22.05 kHz mono.
enum WallTones {
    static let names = ["Beeps", "Chimes", "Bell", "Rising"]
    static let defaultName = "Chimes"
    static let rate = ChimeSound.rate

    static func named(_ name: String) -> String { names.contains(name) ? name : defaultName }

    /// One loop of [name]'s sound.
    static func pcm(_ name: String) -> [Int16] {
        switch named(name) {
        case "Beeps": return beeps()
        case "Bell": return bell()
        case "Rising": return rising()
        default: return chimes()
        }
    }

    /// One loop as a WAV file (for AVAudioPlayer).
    static func wav(_ name: String) -> Data { ChimeSound.wav(pcm(name), rate: rate) }

    private static func buffer(_ seconds: Double) -> [Double] { [Double](repeating: 0, count: Int(Double(rate) * seconds)) }

    private static func finish(_ out: [Double]) -> [Int16] { out.map { Int16(Swift.min(1, Swift.max(-1, $0)) * 32767) } }

    /// A note at [start] s: [partials] (frequency ratio, level) over [freq], a soft attack and a [decay].
    private static func note(_ out: inout [Double], start: Double, freq: Double, length: Double, level: Double,
                             decay: Double, partials: [(Double, Double)] = [(1, 1), (2, 0.25)]) {
        let s0 = Int(start * Double(rate))
        let n = Swift.min(out.count, s0 + Int(length * Double(rate)))
        guard s0 < n else { return }
        for i in s0..<n {
            let t = Double(i - s0) / Double(rate)
            let env = (1 - exp(-t * 300)) * exp(-t * decay)
            var v = 0.0
            for (ratio, l) in partials { v += sin(2 * Double.pi * freq * ratio * t) * l }
            out[i] += v * env * level
        }
    }

    /// Three short bright beeps, then a pause (like the web wall's beeps).
    static func beeps() -> [Int16] {
        var out = buffer(1.2)
        for k in 0..<3 {
            note(&out, start: Double(k) * 0.18, freq: 1046.5, length: 0.14, level: 0.5, decay: 4,
                 partials: [(1, 1), (3, 0.3), (5, 0.15)])
        }
        return finish(out)
    }

    /// Three rising chime notes, then a pause.
    static func chimes() -> [Int16] {
        var out = buffer(2.0)
        for (k, f) in [880.0, 1108.7, 1318.5].enumerated() {
            note(&out, start: Double(k) * 0.24, freq: f, length: 1.2, level: 0.45, decay: 3.5)
        }
        return finish(out)
    }

    /// One round bell stroke (inharmonic partials) ringing out.
    static func bell() -> [Int16] {
        var out = buffer(2.0)
        note(&out, start: 0, freq: 659.3, length: 2.0, level: 0.42, decay: 2.2,
             partials: [(1, 1), (2.76, 0.45), (5.4, 0.25), (8.93, 0.12)])
        return finish(out)
    }

    /// Two quick upward sweeps, then a pause.
    static func rising() -> [Int16] {
        var out = buffer(2.2)
        for k in 0..<2 {
            let s0 = Int(Double(k) * 0.7 * Double(rate))
            let len = Int(0.55 * Double(rate))
            var phase = 0.0
            for i in 0..<len where s0 + i < out.count {
                let t = Double(i) / Double(rate)
                let f = 600 + (1250 - 600) * (t / 0.55)
                phase += 2 * Double.pi * f / Double(rate)
                let env = Swift.min(1, t * 60) * Swift.min(1, (0.55 - t) * 40)
                out[s0 + i] += (sin(phase) + 0.2 * sin(2 * phase)) * env * 0.45
            }
        }
        return finish(out)
    }
}
