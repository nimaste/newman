import Foundation

// "Hey Newman" on the wall iPad (Shared/Core/Voice.swift): the same cases as the Android wall
// app's VoiceTest.kt and ClipCaptureTest.kt, so both tablets behave alike. Called from main.swift.

/// A small deterministic random source (the tests' "room noise").
struct TestRandom {
    private var state: UInt64
    init(_ seed: Int) { state = UInt64(truncatingIfNeeded: seed) &* 6364136223846793005 &+ 1442695040888963407 }
    mutating func next(_ lo: Int, _ hi: Int) -> Int {
        state = state &* 6364136223846793005 &+ 1442695040888963407
        let span = UInt64(hi - lo + 1)
        return lo + Int((state >> 33) % span)
    }
}

private func tone(_ n: Int = VoiceAudio.frame, _ amp: Double, freq: Double = 220) -> [Int16] {
    (0..<n).map { Int16(sin(2 * Double.pi * freq * Double($0) / Double(VoiceAudio.rate)) * amp) }
}

private func noise(_ n: Int = VoiceAudio.frame, _ amp: Int, seed: Int = 1) -> [Int16] {
    var r = TestRandom(seed)
    return (0..<n).map { _ in Int16(r.next(-amp, amp)) }
}

/// Fake models: mel = 8 frames of the frame's mean level; embedding = the window's last; score = last feature.
private final class FakeWakeModels: WakeModels {
    let featureFrames: Int
    var melInputSizes: [Int] = []
    var embeddingSizesOK = true
    var scoreSizesOK = true

    init(featureFrames: Int = 16) { self.featureFrames = featureFrames }

    func melspectrogram(_ samples: [Float]) throws -> [[Float]] {
        melInputSizes.append(samples.count)
        let tail = samples.suffix(VoiceAudio.frame)
        let level = tail.map { abs($0) }.reduce(0, +) / Float(tail.count)
        return Array(repeating: Array(repeating: level, count: 32), count: 8)
    }

    func embedding(_ mel: [[Float]]) throws -> [Float] {
        if mel.count != 76 { embeddingSizesOK = false }
        return Array(repeating: mel[mel.count - 1][0], count: 96)
    }

    func score(_ features: [[Float]]) throws -> Float {
        if features.count != featureFrames { scoreSizesOK = false }
        return features[features.count - 1][0]
    }
}

func runVoiceTests() {
    // Frames come out whole whatever the read sizes.
    var framer = VoiceFramer(size: 1280)
    var frames: [[Int16]] = []
    let data = (0..<5000).map { Int16($0) }
    var at = 0
    for size in [100, 1500, 7, 2000, 1393] {
        framer.push(Array(data[at..<(at + size)])) { frames.append($0) }
        at += size
    }
    eq(frames.count, 3, "voice: 5000 samples = 3 frames + 1160 kept")
    eq(frames[1][0], 1280, "voice: second frame starts at 1280")
    eq(frames[2][1279], 3839, "voice: third frame ends at 3839")
    framer.push([Int16](repeating: 7, count: 120)) { frames.append($0) }
    eq(frames.count, 4, "voice: the carry completes a frame")
    eq(frames[3][0], 3840, "voice: carry first")
    eq(frames[3][1279], 7, "voice: new data last")

    // PCM, levels, utterance cap.
    eq([UInt8](VoiceAudio.pcm16le([0x1234, -1])), [0x34, 0x12, 0xFF, 0xFF], "voice: PCM is little-endian")
    eq(VoiceAudio.level([Int16](repeating: 0, count: 1280)), 0, "voice: silence is level 0")
    check(VoiceAudio.level(tone(1280, 16000)) > 0.8, "voice: loud tone level")
    let quiet = VoiceAudio.level(tone(1280, 300))
    check(quiet >= 0.1 && quiet <= 0.4, "voice: quiet tone level (\(quiet))")
    check(VoiceAudio.zeroCrossings(noise(1280, 3000)) > 0.4, "voice: noise crosses zero a lot")
    check(VoiceAudio.zeroCrossings(tone(1280, 3000)) < 0.05, "voice: a voice-like tone doesn't")
    var u = VoiceUtterance(maxSeconds: 1)
    for _ in 0..<20 { u.add([Int16](repeating: 1, count: 1280)) }
    eq(u.size, 16000, "voice: utterance capped")
    eq(u.bytes().count, 32000, "voice: utterance bytes")

    // Ring buffer.
    var ring = RingBuffer<Int>(capacity: 3)
    for i in 1...5 { ring.append(i) }
    eq(ring.all, [3, 4, 5], "ring: keeps the newest, oldest first")
    eq(ring.last(2), [4, 5], "ring: last 2")
    eq(ring.last(9), [3, 4, 5], "ring: last more than there are")
    eq(ring[0], 3, "ring: index 0 is the oldest")
    ring.removeAll()
    check(ring.isEmpty && ring.last(1).isEmpty, "ring: emptied")

    // Noise floor: the quiet end of the room.
    var nf = NoiseFloor(frames: 10)
    eq(nf.floor(), 200, "noise floor: fallback")
    for v in [100.0, 120, 90, 3000, 2500, 110, 95, 4000, 105, 115] { nf.add(v) }
    check(nf.floor() >= 90 && nf.floor() <= 110, "noise floor: 20th percentile (\(nf.floor()))")

    // Endpointing: speech then 800 ms of silence ends it.
    var e = Endpointer(noiseFloor: 150)
    var results: [Endpointer.Result] = []
    for i in 0..<3 { results.append(e.push(noise(1280, 200, seed: i))) }
    for _ in 0..<15 { results.append(e.push(tone(1280, 4000))) }
    check(results.allSatisfy { $0 == .continue }, "endpointer: still going while speaking")
    check(e.started, "endpointer: speech started")
    var r = Endpointer.Result.continue
    var quietFrames = 0
    while r == .continue && quietFrames < 50 {
        r = e.push(noise(1280, 200, seed: quietFrames))
        quietFrames += 1
    }
    eq(r, .end, "endpointer: end of speech")
    eq(quietFrames, 10, "endpointer: after 800 ms of quiet")

    // Nobody speaks (5 s) / someone never stops (8 s).
    var q = Endpointer(noiseFloor: 150)
    r = .continue
    var n = 0
    while r == .continue {
        r = q.push(noise(1280, 200, seed: n))
        n += 1
    }
    eq(r, .noSpeech, "endpointer: didn't catch that")
    eq(n, 63, "endpointer: after 5 s")
    var talker = Endpointer(noiseFloor: 150)
    r = .continue
    n = 0
    while r == .continue {
        r = talker.push(tone(1280, 5000))
        n += 1
    }
    eq(r, .max, "endpointer: max length")
    eq(n, 100, "endpointer: 8 s")

    // Clicks and hiss aren't speech.
    var c = Endpointer(noiseFloor: 150)
    for i in 0..<6 { _ = c.push(i % 3 == 0 ? tone(1280, 6000) : noise(1280, 150, seed: i)) }
    check(!c.started, "endpointer: one loud frame is a click")
    check(!c.isSpeech(rms: c.threshold * 1.3, zcr: 0.6), "endpointer: hiss")
    check(c.isSpeech(rms: c.threshold * 1.3, zcr: 0.1), "endpointer: voice")
    check(c.isSpeech(rms: c.threshold * 3, zcr: 0.6), "endpointer: loud hiss counts")
    check(Endpointer(noiseFloor: 2000).threshold >= 4000, "endpointer: loud room")
    eq(Endpointer(noiseFloor: 10).threshold, 150, "endpointer: minimum threshold")

    // The trigger needs two frames in a row, or one strong one.
    var t = WakeTrigger(threshold: 0.5, refractoryFrames: 3, warmupFrames: 2)
    check(!t.push(0.99) && !t.push(0.99), "trigger: warm-up ignored")
    check(!t.push(0.6), "trigger: one frame over isn't enough")
    check(t.push(0.55), "trigger: two in a row")
    check(!t.push(0.99) && !t.push(0.99) && !t.push(0.99), "trigger: refractory")
    check(t.push(0.9), "trigger: one strong frame")
    for _ in 0..<3 { _ = t.push(0) }
    check(!t.push(0.3), "trigger: low")
    check(!t.push(0.6), "trigger: previous 0.3 < 0.4")
    for _ in 0..<3 { _ = t.push(0) }
    t.threshold = 0.35 // a "high" sensitivity tablet
    check(!t.push(0.3), "trigger: high sensitivity, below")
    check(t.push(0.36), "trigger: high sensitivity, two in a row")

    // openWakeWord's buffering.
    let models = FakeWakeModels()
    let w = WakeFeatures(models)
    var scores: [Float?] = []
    do {
        for _ in 0..<20 { scores.append(try w.push([Int16](repeating: 100, count: 1280))) }
        check(scores.prefix(15).allSatisfy { $0 == nil }, "wake features: no score before 16 embeddings")
        check(scores.dropFirst(15).allSatisfy { $0 != nil }, "wake features: a score from the 16th frame")
        check(models.melInputSizes.allSatisfy { $0 == 1280 + 480 }, "wake features: each frame with the 30 ms before it")
        check(models.embeddingSizesOK && models.scoreSizesOK, "wake features: window sizes")
        let lastScore: Float = scores.last.flatMap { $0 } ?? 0
        check(abs(lastScore - 12) < 0.001, "wake features: x/10 + 2 transform (100 -> 12)")
        check(try WakeFeatures(FakeWakeModels(featureFrames: 28)).push([Int16](repeating: 0, count: 1280)) == nil, "wake features: 28-frame model")
    } catch {
        check(false, "wake features threw \(error)")
    }
    w.reset()
    eq(w.embeddings, 0, "wake features: reset")

    // Configs.
    let cfgJSON = #"""
    {"name":"hey_newman","version":"ab12","threshold":0.45,"enabled":true,
     "files":{"melspectrogram":"api/voice/wakeword/melspectrogram.onnx","embedding_model":"api/voice/wakeword/embedding_model.onnx",
     "wakeword":"api/voice/wakeword/hey_newman.onnx"},"extra":1}
    """#
    if let cfg = WakeConfig.parse(Data(cfgJSON.utf8)) {
        check(cfg.complete, "wake config: complete")
        eq(cfg.threshold, 0.45, "wake config: threshold")
        eq(cfg.files["wakeword"], "api/voice/wakeword/hey_newman.onnx", "wake config: model file")
    } else {
        check(false, "wake config parses")
    }
    check(WakeConfig.parse(Data(#"{"version":"x","files":{}}"#.utf8))?.complete == false, "wake config: incomplete")
    eq(WakeConfig.directoryName("ab-12/../cd"), "ab12cd", "wake config: folder name")
    eq(WakeConfig.directoryName(String(repeating: "a", count: 40)).count, 32, "wake config: folder name capped")
    eq(VoicePageConfig.parse(#"{"enabled":true,"threshold":0.6,"version":"v1"}"#), VoicePageConfig(enabled: true, threshold: 0.6, version: "v1"), "page config")
    eq(VoicePageConfig.parse("not json"), VoicePageConfig(), "page config: junk = off")
    eq(VoicePageConfig.parse(nil), VoicePageConfig(), "page config: nothing = off")
    eq(VoicePageConfig.parse(#"{"enabled":true}"#), VoicePageConfig(enabled: true), "page config: defaults")

    // When the listener may hold the microphone.
    var run = VoiceRunConditions(enabled: true, foreground: true, permitted: true)
    check(run.shouldRun, "run: all set")
    run.interrupted = true
    check(!run.shouldRun, "run: a call / Siri has the audio")
    run.interrupted = false
    run.paused = true
    check(!run.shouldRun, "run: teaching")
    run.paused = false
    run.foreground = false
    check(!run.shouldRun, "run: backgrounded")

    eq(OnnxShape.expectedDimension(from: "Got invalid dimensions for input: x for the following indices\n index: 1 Got: 16 Expected: 28\n"), 28, "onnx: expected dimension")
    eq(OnnxShape.expectedDimension(from: "something else"), nil, "onnx: no dimension")

    // Page events.
    eq(VoiceEvent.level(0.12345), #"{"type":"level","value":0.123}"#, "event: level")
    eq(VoiceEvent.clipLevel(1), #"{"type":"clip-level","value":1.000}"#, "event: clip level")
    eq(VoiceEvent.audio(Data([1, 2, 3])), #"{"type":"audio","rate":16000,"pcm":"AQID"}"#, "event: audio")
    eq(VoiceEvent.clip(Data([1, 2, 3]), ms: 800), #"{"type":"clip","rate":16000,"ms":800,"pcm":"AQID"}"#, "event: clip")
    eq(VoiceEvent.error("The \"mic\"\n"), #"{"type":"error","message":"The \"mic\"\n"}"#, "event: error escaped")
    if let parsed = try? JSONSerialization.jsonObject(with: Data(VoiceEvent.clipError("Allow it\u{2028}first").utf8)) as? [String: Any] {
        eq(parsed["message"] as? String, "Allow it\u{2028}first", "event: clip error round-trips")
    } else {
        check(false, "event: clip error is JSON")
    }

    // The chime.
    let chime = ChimeSound.pcm()
    eq(chime.count, 9261, "chime: 0.42 s at 22.05 kHz")
    let peak = chime.map { abs(Int($0)) }.max() ?? 0
    check(peak > 3000 && peak < 32767, "chime: soft (\(peak))")
    let wav = ChimeSound.wav(chime)
    eq(wav.count, 44 + chime.count * 2, "chime: WAV size")
    eq(String(decoding: wav.prefix(4), as: UTF8.self), "RIFF", "chime: RIFF")

    runClipCaptureTests()
}

private func clipVoice(_ amp: Double = 3000) -> [Int16] { tone(VoiceAudio.frame, amp) }
private func room(_ seed: Int = 1) -> [Int16] { noise(VoiceAudio.frame, 40, seed: seed) }

private func feed(_ c: inout ClipCapture, _ frames: [[Int16]]) -> (ClipCapture.Result, Int) {
    var n = 0
    for f in frames {
        n += 1
        let r = c.push(f)
        if r != .continue { return (r, n) }
    }
    return (.continue, n)
}

private func runClipCaptureTests() {
    // A spoken clip is trimmed to the words and ends at the pause.
    var c = ClipCapture(maxMs: 5000, fixed: false)
    let (r, pushed) = feed(&c, (0..<12).map { room($0) } + (0..<10).map { _ in clipVoice() } + (0..<20).map { room(100 + $0) })
    eq(r, .done, "clip: done")
    eq(pushed, 12 + 10 + 8, "clip: ended after 640 ms of quiet")
    eq(c.keptMs, 240 + 800 + 240, "clip: kept 240 ms either side")
    eq(c.pcm().count, c.keptMs * VoiceAudio.rate / 1000 * 2, "clip: pcm size")

    // Nobody speaking.
    var none = ClipCapture(maxMs: 5000, fixed: false, waitMs: 2000)
    let (nr, np) = feed(&none, (0..<100).map { room($0) })
    eq(nr, .noSpeech, "clip: nobody spoke")
    eq(np, 25, "clip: waited 2 s")
    eq(none.pcm().count, 0, "clip: nothing kept")

    // A click isn't speech; a steady hum is learned as the room.
    var click = ClipCapture(maxMs: 5000, fixed: false, waitMs: 1600)
    eq(feed(&click, (0..<5).map { room($0) } + [clipVoice(8000)] + (0..<20).map { room(50 + $0) }).0, .noSpeech, "clip: a click")
    var hum = ClipCapture(maxMs: 5000, fixed: false, waitMs: 1600)
    eq(feed(&hum, (0..<30).map { _ in clipVoice(400) }).0, .noSpeech, "clip: a hum")

    // Speech is capped at max.
    var long = ClipCapture(maxMs: 1000, fixed: false)
    let (lr, lp) = feed(&long, (0..<3).map { room($0) } + (0..<40).map { _ in clipVoice() })
    eq(lr, .done, "clip: capped")
    check(lp <= 3 + 1 + 13, "clip: capped early enough (\(lp))")
    check(long.keptMs >= 1000 && long.keptMs <= 1400, "clip: capped length (\(long.keptMs))")

    // The talking clip is a fixed length and can stop early.
    var fixed = ClipCapture(maxMs: 2000, fixed: true)
    let (fr, fp) = feed(&fixed, (0..<40).map { room($0) })
    eq(fr, .done, "clip: fixed done")
    eq(fp, 25, "clip: fixed 2 s")
    eq(fixed.keptMs, 2000, "clip: fixed kept")
    var early = ClipCapture(maxMs: 30000, fixed: true)
    _ = feed(&early, (0..<10).map { _ in clipVoice() })
    eq(early.finishEarly(), .done, "clip: stopped early")
    eq(early.keptMs, 800, "clip: what it had")
    var unstarted = ClipCapture(maxMs: 5000, fixed: false)
    eq(unstarted.finishEarly(), .noSpeech, "clip: stopped before speech")

    // Limits the page can ask for.
    eq(ClipCapture.clampMs(10, fixed: false), 1000, "clip: at least 1 s")
    eq(ClipCapture.clampMs(60000, fixed: false), 6000, "clip: spoken at most 6 s")
    eq(ClipCapture.clampMs(60000, fixed: true), 35000, "clip: fixed at most 35 s")
    eq(ClipCapture(maxMs: 30000, fixed: true).maxMs, 30000, "clip: 30 s fixed")
}
