import Foundation

// "Hey Newman" on the wall iPad: the pure parts, line for line the Android wall app's
// (android/core/.../core/voice: AudioFrames.kt, Endpointer.kt, WakeWord.kt, WakeConfig.kt,
// ClipCapture.kt) so both tablets wake, end and cut clips the same way. Tested on Linux
// (Tests/LinuxRunner/VoiceTests.swift). 16 kHz, 16-bit mono throughout.

// MARK: audio plumbing

/// Fixed-size frames, loudness and the little-endian PCM bytes the hub takes
/// (POST /api/voice/utterance, audio/L16 - sent by the page).
enum VoiceAudio {
    static let rate = 16_000
    /// openWakeWord works on 80 ms chunks.
    static let frame = 1_280
    static let frameMs = frame * 1000 / rate

    /// Root mean square in sample units (0..32768).
    static func rms(_ frame: [Int16]) -> Double {
        if frame.isEmpty { return 0 }
        var sum = 0.0
        for s in frame {
            let d = Double(s)
            sum += d * d
        }
        return (sum / Double(frame.count)).squareRoot()
    }

    /// Share of neighbouring samples that change sign (hiss is high, voices are low).
    static func zeroCrossings(_ frame: [Int16]) -> Double {
        guard frame.count >= 2 else { return 0 }
        var c = 0
        for i in 1..<frame.count where (frame[i] >= 0) != (frame[i - 1] >= 0) { c += 1 }
        return Double(c) / Double(frame.count - 1)
    }

    /// Loudness for an animation: -60 dBFS (and below) = 0, -10 dBFS (and above) = 1.
    static func level(_ frame: [Int16]) -> Float {
        let r = rms(frame)
        if r < 1 { return 0 }
        let db = 20 * log10(r / 32768)
        return Float(min(1, max(0, (db + 60) / 50)))
    }

    static func pcm16le(_ samples: [Int16]) -> Data {
        var out = Data(count: samples.count * 2)
        out.withUnsafeMutableBytes { raw in
            let p = raw.bindMemory(to: UInt8.self)
            for (i, s) in samples.enumerated() {
                let u = UInt16(bitPattern: s)
                p[2 * i] = UInt8(u & 0xFF)
                p[2 * i + 1] = UInt8(u >> 8)
            }
        }
        return out
    }
}

/// A fixed-capacity buffer that drops its oldest element when full (oldest first by index).
struct RingBuffer<Element> {
    let capacity: Int
    private var items: [Element] = []
    private var start = 0

    init(capacity: Int) {
        self.capacity = max(1, capacity)
        items.reserveCapacity(self.capacity)
    }

    var count: Int { items.count }
    var isEmpty: Bool { items.isEmpty }

    mutating func append(_ e: Element) {
        if items.count < capacity {
            items.append(e)
        } else {
            items[start] = e
            start = (start + 1) % capacity
        }
    }

    /// 0 = the oldest kept.
    subscript(i: Int) -> Element { items[(start + i) % items.count] }

    /// The newest [n] (or all there are), oldest first.
    func last(_ n: Int) -> [Element] {
        let k = min(max(0, n), count)
        return (count - k..<count).map { self[$0] }
    }

    var all: [Element] { last(count) }

    mutating func removeAll() {
        items.removeAll(keepingCapacity: true)
        start = 0
    }
}

/// Cuts a stream of reads of any size into fixed frames, keeping the remainder for next time.
struct VoiceFramer {
    let size: Int
    private var carry: [Int16] = []

    init(size: Int = VoiceAudio.frame) {
        self.size = size
        carry.reserveCapacity(size)
    }

    mutating func push(_ data: [Int16], _ out: ([Int16]) -> Void) {
        var i = 0
        while i < data.count {
            let take = min(size - carry.count, data.count - i)
            carry.append(contentsOf: data[i..<(i + take)])
            i += take
            if carry.count == size {
                out(carry)
                carry.removeAll(keepingCapacity: true)
            }
        }
    }

    mutating func reset() { carry.removeAll(keepingCapacity: true) }
}

/// What was said after the wake word, up to a limit (frames appended as they come).
struct VoiceUtterance {
    let capacity: Int
    private(set) var samples: [Int16] = []

    init(maxSeconds: Int = 10) { capacity = VoiceAudio.rate * maxSeconds }

    mutating func add(_ frame: [Int16]) {
        let take = min(frame.count, capacity - samples.count)
        if take > 0 { samples.append(contentsOf: frame.prefix(take)) }
    }

    var size: Int { samples.count }
    var seconds: Double { Double(samples.count) / Double(VoiceAudio.rate) }
    func bytes() -> Data { VoiceAudio.pcm16le(samples) }
    mutating func clear() { samples.removeAll(keepingCapacity: true) }
}

/// The room's background loudness, from the last few seconds before the wake word (so the end of
/// speech is judged against the room, not against the first word after "Hey Newman").
struct NoiseFloor {
    private var values: RingBuffer<Double>

    init(frames: Int = 40) { values = RingBuffer(capacity: frames) }

    mutating func add(_ rms: Double) { values.append(rms) }

    /// The quieter end of what was heard (20th percentile); [fallback] before anything was.
    func floor(fallback: Double = 200) -> Double {
        if values.isEmpty { return fallback }
        let sorted = values.all.sorted()
        return sorted[(sorted.count - 1) / 5]
    }
}

// MARK: end of speech

/// Decides when someone has finished speaking after the wake word: energy against the room's
/// noise floor (with a zero-crossing check so hiss doesn't count as a voice), on 80 ms frames.
///   noSpeech  nobody spoke within noSpeechMs ("I didn't catch that")
///   end       they spoke (at least minSpeechMs) and then were quiet for endSilenceMs
///   max       maxMs reached (someone kept talking, or a TV in the background)
struct Endpointer {
    enum Result: Equatable { case `continue`, end, noSpeech, max }

    let frameMs: Int
    let maxMs: Int
    let noSpeechMs: Int
    let endSilenceMs: Int
    let minSpeechMs: Int
    /// A frame is speech when it's this many times louder than the floor (about 6 dB)...
    let ratio: Double
    /// ...and never below this (quiet rooms: the floor can be near zero).
    let minRms: Double

    private var floor: Double
    private var elapsedMs = 0
    private var speechMs = 0
    private var silenceMs = 0
    private var run = 0
    private(set) var started = false

    init(noiseFloor: Double, frameMs: Int = VoiceAudio.frameMs, maxMs: Int = 8_000, noSpeechMs: Int = 5_000,
         endSilenceMs: Int = 800, minSpeechMs: Int = 240, ratio: Double = 2.0, minRms: Double = 150) {
        floor = Swift.max(noiseFloor, 30)
        self.frameMs = frameMs
        self.maxMs = maxMs
        self.noSpeechMs = noSpeechMs
        self.endSilenceMs = endSilenceMs
        self.minSpeechMs = minSpeechMs
        self.ratio = ratio
        self.minRms = minRms
    }

    var threshold: Double { Swift.max(floor * ratio, minRms) }

    func isSpeech(rms: Double, zcr: Double) -> Bool {
        rms > threshold && (zcr < 0.35 || rms > threshold * 2)
    }

    mutating func push(_ frame: [Int16]) -> Result {
        push(rms: VoiceAudio.rms(frame), zcr: VoiceAudio.zeroCrossings(frame))
    }

    mutating func push(rms: Double, zcr: Double) -> Result {
        elapsedMs += frameMs
        if isSpeech(rms: rms, zcr: zcr) {
            run += 1
            speechMs += frameMs
            silenceMs = 0
            if run >= 2 { started = true } // two frames in a row (160 ms): a voice, not a click
        } else {
            run = 0
            silenceMs += frameMs
            if !started { floor = floor * 0.9 + rms * 0.1 } // still learning the room
        }
        if started && speechMs >= minSpeechMs && silenceMs >= endSilenceMs { return .end }
        if !started && elapsedMs >= noSpeechMs { return .noSpeech }
        if elapsedMs >= maxMs { return started ? .max : .noSpeech }
        return .continue
    }
}

// MARK: wake word

/// The three openWakeWord models, behind a protocol so the buffering here can be tested without
/// ONNX Runtime (the wall app's OnnxWakeModels implements it):
///   melspectrogram  raw samples (int16 values as floats) -> frames x 32 mel bands
///   embedding       76 mel frames -> one 96-number embedding
///   score           the last featureFrames embeddings -> 0..1 ("hey newman" or not)
protocol WakeModels: AnyObject {
    var featureFrames: Int { get }
    func melspectrogram(_ samples: [Float]) throws -> [[Float]]
    func embedding(_ mel: [[Float]]) throws -> [Float]
    func score(_ features: [[Float]]) throws -> Float
}

/// openWakeWord's streaming feature pipeline (openwakeword/utils.py AudioFeatures), one 80 ms
/// frame at a time: each frame (plus the 480 samples before it) adds mel frames, every frame adds
/// an embedding from the last 76 mel frames, and the wake-word model scores the last 16 embeddings.
final class WakeFeatures {
    static let context = 160 * 3
    static let melBands = 32
    static let melWindow = 76
    static let melMax = 10 * 97
    static let featuresMax = 120

    private let models: WakeModels
    private var raw = [Float](repeating: 0, count: VoiceAudio.frame + WakeFeatures.context)
    private var mel = RingBuffer<[Float]>(capacity: WakeFeatures.melMax)
    private var features = RingBuffer<[Float]>(capacity: WakeFeatures.featuresMax)

    init(_ models: WakeModels) {
        self.models = models
        reset()
    }

    func reset() {
        for i in raw.indices { raw[i] = 0 }
        mel.removeAll()
        for _ in 0..<Self.melWindow { mel.append([Float](repeating: 1, count: Self.melBands)) } // as openWakeWord starts (ones)
        features.removeAll()
    }

    /// One 1280-sample frame in; the wake-word score once enough has been heard, else nil.
    func push(_ frame: [Int16]) throws -> Float? {
        precondition(frame.count == VoiceAudio.frame, "frames are \(VoiceAudio.frame) samples")
        let ctx = Self.context
        for i in 0..<ctx { raw[i] = raw[VoiceAudio.frame + i] }
        for i in 0..<frame.count { raw[ctx + i] = Float(frame[i]) }
        for row in try models.melspectrogram(raw) {
            mel.append(row.map { $0 / 10 + 2 }) // openWakeWord's melspec transform
        }
        if mel.count >= Self.melWindow {
            features.append(try models.embedding(mel.last(Self.melWindow)))
        }
        let n = models.featureFrames
        if features.count < n { return nil }
        return try models.score(features.last(n))
    }

    var embeddings: Int { features.count }
}

/// When a stream of scores means "Hey Newman": a frame at or over the threshold right after one
/// nearly there (two frames in a row - one stray frame isn't enough), or one well over it. Then
/// quiet for refractoryFrames (2 s) so one phrase is one wake-up. The first warmupFrames
/// scores after a (re)start are ignored, as openWakeWord does.
struct WakeTrigger {
    var threshold: Float
    let refractoryFrames: Int
    let warmupFrames: Int
    private var previous: Float = 0
    private var quietFor = 0
    private var seen = 0

    init(threshold: Float = 0.5, refractoryFrames: Int = 25, warmupFrames: Int = 5) {
        self.threshold = threshold
        self.refractoryFrames = refractoryFrames
        self.warmupFrames = warmupFrames
    }

    mutating func reset() {
        previous = 0
        quietFor = 0
        seen = 0
    }

    mutating func push(_ score: Float) -> Bool {
        seen += 1
        if seen <= warmupFrames { return false }
        if quietFor > 0 {
            quietFor -= 1
            previous = 0
            return false
        }
        let strong = min(0.95, threshold + 0.25)
        let hit = score >= strong || (score >= threshold && previous >= threshold * 0.8)
        previous = score
        if hit {
            quietFor = refractoryFrames
            previous = 0
        }
        return hit
    }
}

/// GET /api/voice/wakeword: where the wake-word model files are on the hub and how keenly to
/// listen (the tablet's sensitivity is already folded into threshold). A new version (another
/// wake word, a retrained model) is downloaded again; the same one is kept on the tablet.
struct WakeConfig: Decodable, Equatable {
    static let path = "api/voice/wakeword"
    static let files = ["melspectrogram", "embedding_model", "wakeword"]

    var name = "hey_newman"
    var phrase = "Hey Newman"
    var version = ""
    var threshold: Float = 0.5
    var files: [String: String] = [:]
    var enabled = true

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? name
        phrase = try c.decodeIfPresent(String.self, forKey: .phrase) ?? phrase
        version = try c.decodeIfPresent(String.self, forKey: .version) ?? version
        threshold = try c.decodeIfPresent(Float.self, forKey: .threshold) ?? threshold
        files = try c.decodeIfPresent([String: String].self, forKey: .files) ?? files
        enabled = try c.decodeIfPresent(Bool.self, forKey: .enabled) ?? enabled
    }

    private enum CodingKeys: String, CodingKey { case name, phrase, version, threshold, files, enabled }

    var complete: Bool {
        !version.trimmingCharacters(in: .whitespaces).isEmpty
            && Self.files.allSatisfy { !(files[$0] ?? "").trimmingCharacters(in: .whitespaces).isEmpty }
    }

    /// The folder a version is kept in (letters and digits, at most 32).
    static func directoryName(_ version: String) -> String {
        String(version.filter { $0.isLetter || $0.isNumber }.prefix(32))
    }

    static func parse(_ data: Data) -> WakeConfig? { try? JSONDecoder().decode(WakeConfig.self, from: data) }
}

/// What the wall page tells the app (NewmanWall.voiceConfig): listen or not, the threshold, the
/// model version. Anything unreadable = off.
struct VoicePageConfig: Decodable, Equatable {
    var enabled = false
    var threshold: Float = 0.5
    var version = ""

    init(enabled: Bool = false, threshold: Float = 0.5, version: String = "") {
        self.enabled = enabled
        self.threshold = threshold
        self.version = version
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        enabled = try c.decodeIfPresent(Bool.self, forKey: .enabled) ?? false
        threshold = try c.decodeIfPresent(Float.self, forKey: .threshold) ?? 0.5
        version = try c.decodeIfPresent(String.self, forKey: .version) ?? ""
    }

    private enum CodingKeys: String, CodingKey { case enabled, threshold, version }

    static func parse(_ text: String?) -> VoicePageConfig {
        guard let text, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              let c = try? JSONDecoder().decode(VoicePageConfig.self, from: Data(text.utf8)) else { return VoicePageConfig() }
        return c
    }
}

/// When the listener may hold the microphone (VoiceListener.shouldRun on Android): the page turned
/// voice on, the wall is in front, the microphone is allowed, no "Teach" clip is being recorded and
/// nothing else has the audio (a call, Siri - AVAudioSession's interruption, iOS's MicGate).
struct VoiceRunConditions: Equatable {
    var enabled = false
    var foreground = false
    var permitted = false
    var paused = false
    var interrupted = false

    var shouldRun: Bool { enabled && foreground && permitted && !paused && !interrupted }
}

/// ONNX Runtime's Objective-C API doesn't say a model's input shape; a wrong one fails with
/// "... index: 1 Got: 16 Expected: 28". The wake model's frame count is read from that.
enum OnnxShape {
    static func expectedDimension(from message: String) -> Int? {
        guard let re = try? NSRegularExpression(pattern: "Expected:\\s*(\\d+)"),
              let m = re.firstMatch(in: message, range: NSRange(message.startIndex..., in: message)),
              let r = Range(m.range(at: 1), in: message), let n = Int(message[r]), n > 0 else { return nil }
        return n
    }
}

// MARK: "Teach Hey Newman" clips

/// One recording for "Teach Hey Newman" (the page asks: NewmanWall.recordClip(maxMs, fixed)), fed
/// 80 ms frames from the microphone.
///  - speech (fixed = false): waits up to waitMs for someone to speak, then keeps from just before
///    the first word (preRollMs) to just after the pause that follows it (endSilenceMs of quiet,
///    tailMs kept), at most maxMs of speech. The first learnFrames frames measure the room.
///  - fixed (fixed = true): exactly maxMs of audio (the "just talk normally" step), or less when
///    stopped early (finishEarly).
struct ClipCapture {
    enum Result: Equatable { case `continue`, done, noSpeech }

    static let minRms = 150.0
    static let maxSpeechMs = 6_000
    static let maxFixedMs = 35_000

    /// What the page may ask for: a spoken clip 1-6 s, a fixed one 1-35 s.
    static func clampMs(_ ms: Int, fixed: Bool) -> Int { min(max(ms, 1_000), fixed ? maxFixedMs : maxSpeechMs) }

    let maxMs: Int
    let fixed: Bool
    let waitMs: Int
    let endSilenceMs: Int
    let preRollMs: Int
    let tailMs: Int
    let minSpeechMs: Int
    let learnFrames: Int
    let frameMs: Int

    private var frames: [[Int16]] = []
    private var floor = 0.0
    private var learned = 0
    private var run = 0
    private var startFrame = -1
    private var lastSpeech = -1
    private var speechMs = 0
    private var silenceMs = 0
    private(set) var result = Result.continue

    init(maxMs: Int, fixed: Bool, waitMs: Int = 10_000, endSilenceMs: Int = 640, preRollMs: Int = 240, tailMs: Int = 240,
         minSpeechMs: Int = 160, learnFrames: Int = 3, frameMs: Int = VoiceAudio.frameMs) {
        self.maxMs = Self.clampMs(maxMs, fixed: fixed)
        self.fixed = fixed
        self.waitMs = waitMs
        self.endSilenceMs = endSilenceMs
        self.preRollMs = preRollMs
        self.tailMs = tailMs
        self.minSpeechMs = minSpeechMs
        self.learnFrames = learnFrames
        self.frameMs = frameMs
    }

    var started: Bool { startFrame >= 0 }

    /// A frame is speech when well above the room (and never below minRms); hiss (many zero crossings) needs to be louder.
    func isSpeech(rms: Double, zcr: Double) -> Bool {
        let t = max(floor * 2.5, Self.minRms)
        return rms > t && (zcr < 0.35 || rms > t * 2)
    }

    @discardableResult
    mutating func push(_ frame: [Int16]) -> Result {
        if result != .continue { return result }
        frames.append(frame)
        let i = frames.count - 1
        let elapsed = frames.count * frameMs
        if fixed {
            if elapsed >= maxMs { result = .done }
            return result
        }
        let rms = VoiceAudio.rms(frame)
        if learned < learnFrames { // the room, before anyone speaks
            floor = (floor * Double(learned) + rms) / Double(learned + 1)
            learned += 1
            return result
        }
        if isSpeech(rms: rms, zcr: VoiceAudio.zeroCrossings(frame)) {
            run += 1
            silenceMs = 0
            lastSpeech = i
            if started { speechMs += frameMs }
            if !started && run >= 2 { // two frames in a row: a voice, not a click
                startFrame = i - 1
                speechMs = 2 * frameMs
            }
        } else {
            run = 0
            if started { silenceMs += frameMs } else { floor = floor * 0.9 + rms * 0.1 }
        }
        if started && speechMs >= minSpeechMs && silenceMs >= endSilenceMs {
            result = .done
        } else if started && (i - startFrame + 1) * frameMs >= maxMs {
            result = .done
        } else if !started && elapsed >= waitMs {
            result = .noSpeech
        }
        return result
    }

    /// Stop now: a fixed recording keeps what it has (if any); a speech one keeps what was said so far.
    @discardableResult
    mutating func finishEarly() -> Result {
        if result == .continue {
            if fixed { result = frames.isEmpty ? .noSpeech : .done } else { result = started ? .done : .noSpeech }
        }
        return result
    }

    /// The kept audio as 16-bit little-endian PCM (16 kHz mono).
    func pcm() -> Data {
        guard let r = keptRange() else { return Data() }
        let (a, b) = r
        var all: [Int16] = []
        for k in a...b { all.append(contentsOf: frames[k]) }
        return VoiceAudio.pcm16le(all)
    }

    var keptMs: Int {
        guard let r = keptRange() else { return 0 }
        let (a, b) = r
        return (b - a + 1) * frameMs
    }

    private func keptRange() -> (Int, Int)? {
        if frames.isEmpty { return nil }
        if fixed { return (0, frames.count - 1) }
        if !started { return nil }
        let a = max(0, startFrame - preRollMs / frameMs)
        let b = min(frames.count - 1, lastSpeech + tailMs / frameMs)
        return a > b ? nil : (a, b)
    }
}

// MARK: what the page hears

/// window.NewmanVoice.event({...}) payloads, exactly as the Android wall app sends them
/// (web/src/wall/VoiceLayer.jsx and TeachVoice.jsx read them).
enum VoiceEvent {
    static let wake = #"{"type":"wake"}"#
    /// Listening for the answer to a question Newman asked (no wake word): the card keeps the question.
    static let wakeFollowUp = #"{"type":"wake","followUp":true}"#
    static let cancel = #"{"type":"cancel"}"#
    /// Nobody answered the question: let it go quietly.
    static let cancelQuiet = #"{"type":"cancel","quiet":true}"#
    static let clipNone = #"{"type":"clip-none"}"#

    static func level(_ v: Float) -> String { #"{"type":"level","value":\#(fixed3(v))}"# }
    static func clipLevel(_ v: Float) -> String { #"{"type":"clip-level","value":\#(fixed3(v))}"# }

    static func audio(_ pcm: Data, rate: Int = VoiceAudio.rate) -> String {
        #"{"type":"audio","rate":\#(rate),"pcm":"\#(pcm.base64EncodedString())"}"#
    }

    static func clip(_ pcm: Data, ms: Int, rate: Int = VoiceAudio.rate) -> String {
        #"{"type":"clip","rate":\#(rate),"ms":\#(ms),"pcm":"\#(pcm.base64EncodedString())"}"#
    }

    static func error(_ message: String) -> String { #"{"type":"error","message":\#(quoted(message))}"# }
    static func clipError(_ message: String) -> String { #"{"type":"clip-error","message":\#(quoted(message))}"# }

    static func fixed3(_ v: Float) -> String { String(format: "%.3f", locale: Locale(identifier: "en_US_POSIX"), Double(v)) }

    /// A JSON string literal.
    static func quoted(_ s: String) -> String {
        var out = "\""
        for u in s.unicodeScalars {
            switch u {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            default:
                if u.value < 0x20 || u.value == 0x2028 || u.value == 0x2029 {
                    out += String(format: "\\u%04x", u.value)
                } else {
                    out.unicodeScalars.append(u)
                }
            }
        }
        return out + "\""
    }
}

// MARK: the chime

/// The short, soft two-note chime played when the tablet hears "Hey Newman" (made here, no files;
/// Android's Chime.kt): 880 Hz, then 1318.5 Hz 0.11 s later, 0.42 s in all, 22.05 kHz.
enum ChimeSound {
    static let rate = 22_050

    static func pcm() -> [Int16] {
        let notes: [(Double, Double)] = [(0.0, 880.0), (0.11, 1318.5)]
        let n = Int(Double(rate) * 0.42)
        var out = [Double](repeating: 0, count: n)
        for (start, freq) in notes {
            let s0 = Int(start * Double(rate))
            for i in s0..<n {
                let t = Double(i - s0) / Double(rate)
                let env = (1 - exp(-t * 400)) * exp(-t * 9)
                out[i] += sin(2 * Double.pi * freq * t) * env * 0.22 + sin(4 * Double.pi * freq * t) * env * 0.04
            }
        }
        return out.map { Int16(min(1, max(-1, $0)) * 32767) }
    }

    /// A mono 16-bit WAV file of [samples] (for AVAudioPlayer).
    static func wav(_ samples: [Int16], rate: Int = ChimeSound.rate) -> Data {
        var d = Data()
        func u32(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
        func u16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
        let bytes = UInt32(samples.count * 2)
        d.append(contentsOf: Array("RIFF".utf8)); u32(36 + bytes); d.append(contentsOf: Array("WAVE".utf8))
        d.append(contentsOf: Array("fmt ".utf8)); u32(16); u16(1); u16(1); u32(UInt32(rate)); u32(UInt32(rate * 2)); u16(2); u16(16)
        d.append(contentsOf: Array("data".utf8)); u32(bytes)
        d.append(VoiceAudio.pcm16le(samples))
        return d
    }
}
