import Foundation

/// "Hey Newman" on the iPad itself (Android's VoiceListener.kt, same rules). While the wall is in
/// front and the page says voice is on, the microphone (WallMic, 16 kHz mono) feeds openWakeWord
/// every 80 ms. On the wake word: a chime, the screen wakes, the page shows "Listening…" and what's
/// said is recorded until the speaker stops (Endpointer: 0.8 s of quiet after speech, nobody speaking
/// for 5 s = "I didn't catch that", 8 s at most). The audio goes to the page (which sends it to the
/// hub, ducks its music and speaks the reply) and the microphone is let go until the page says the
/// reply is done (NewmanWall.voiceDone) or 30 s pass. Nothing is recorded or sent before the wake
/// word; nothing is kept. A call, FaceTime or Siri taking the audio (an interruption) lets go too,
/// until it ends.
///
/// [toPage] gets the page events as JSON (VoiceEvent: wake | level | audio | cancel | error);
/// [onWake] runs when the wake word is heard. Main thread; the samples are handled on WallMic.queue
/// by a WakeProcessor.
@MainActor
final class VoiceListener {
    static let replyTimeout: TimeInterval = 30
    static let modelRetry: TimeInterval = 60
    static let micRetry: TimeInterval = 30

    private let mic: WallMic
    private let store = WakeModelStore()
    private let toPage: (String) -> Void
    private let onWake: () -> Void

    private var config = VoicePageConfig()
    private var run = VoiceRunConditions()
    private enum State { case off, loading, listening, waitingForReply }
    private var state = State.off
    /// Bumped on every start / stop: late answers from an older run are dropped.
    private var generation = 0
    private var models: OnnxWakeModels?
    private var modelDir: String?
    private var processor: WakeProcessor?
    private var listeningVersion = ""
    private var pending: Task<Void, Never>?
    private var replyTimer: Timer?

    init(mic: WallMic, toPage: @escaping (String) -> Void, onWake: @escaping () -> Void) {
        self.mic = mic
        self.toPage = toPage
        self.onWake = onWake
    }

    /// From the page (NewmanWall.voiceConfig).
    func configure(_ c: VoicePageConfig) {
        config = c
        run.enabled = c.enabled
        processor?.threshold = c.threshold
        update()
        // A new model (another wake word, retrained, a new sensitivity): load it - not mid-request.
        if state == .listening, c.enabled, !c.version.isEmpty, c.version != listeningVersion, let p = processor {
            let g = generation
            mic.queue.async {
                let busy = p.capturing
                DispatchQueue.main.async { [weak self] in
                    MainActor.assumeIsolated {
                        guard let self, !busy, g == self.generation, self.state == .listening else { return }
                        self.stopListening(cancelIfCapturing: false)
                        self.begin()
                    }
                }
            }
        }
    }

    var wanted: Bool { config.enabled }

    func setPermitted(_ on: Bool) {
        run.permitted = on
        update()
    }

    func setForeground(_ on: Bool) {
        run.foreground = on
        update()
    }

    /// While a "Teach Hey Newman" clip is recorded (ClipRecorder) the wake word isn't listened for.
    func pause(_ on: Bool) {
        run.paused = on
        update()
    }

    /// A call, FaceTime or Siri has the audio (true) / gave it back (false).
    func setInterrupted(_ on: Bool) {
        run.interrupted = on
        update()
    }

    /// The page finished answering (spoke the reply): listen for the wake word again - or, when Newman
    /// asked a question ([listen]: "Which one: pasta or eggs?"), for the answer straight away, no wake word
    /// needed (a chime, "Listening…"; nobody speaking within followUpMs lets it go quietly).
    func replyFinished(listen: Bool = false) {
        guard state == .waitingForReply else { return }
        replyTimer?.invalidate()
        replyTimer = nil
        state = .off
        answerNext = listen
        update()
    }

    static let followUpMs = 6_000
    /// The next listening run starts by capturing the answer to a question (no wake word).
    private var answerNext = false

    /// The microphone went away under us (WallMic couldn't restart it).
    func micLost() {
        guard state == .listening else { return }
        stopListening(cancelIfCapturing: true)
        later(Self.micRetry)
    }

    // MARK: running

    private func update() {
        if !run.shouldRun {
            if state != .off { stopListening(cancelIfCapturing: true) }
            return
        }
        if state == .off { begin() }
    }

    /// Load the model if needed, then listen.
    private func begin() {
        generation += 1
        let g = generation
        state = .loading
        pending?.cancel()
        pending = Task { @MainActor [weak self] in
            guard let self else { return }
            let ok = await self.ensureModels()
            guard g == self.generation, self.run.shouldRun else { return }
            if ok {
                self.listen()
            } else {
                self.later(Self.modelRetry)
            }
        }
    }

    /// Try again in [seconds] (still "loading": a stop cancels it).
    private func later(_ seconds: TimeInterval) {
        generation += 1
        let g = generation
        state = .loading
        pending?.cancel()
        pending = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
            guard let self, g == self.generation, !Task.isCancelled else { return }
            self.state = .off
            self.update()
        }
    }

    private func ensureModels() async -> Bool {
        let want = config.version
        if models != nil, let dir = modelDir, want.isEmpty || dir == WakeConfig.directoryName(want) { return true }
        do {
            guard let got = try await store.fetch() else {
                WallMic.log.warning("No wake-word model on the hub")
                return false
            }
            let dir = got.0
            let loaded = try await Task.detached(priority: .userInitiated) { try OnnxWakeModels(directory: dir) }.value
            models = loaded
            modelDir = dir.lastPathComponent
            WallMic.log.info("Wake-word model \(dir.lastPathComponent, privacy: .public) loaded (\(loaded.featureFrames, privacy: .public) frames)")
            return true
        } catch {
            WallMic.log.warning("Couldn't get the wake-word model: \(String(describing: error), privacy: .public)")
            return false
        }
    }

    private func listen() {
        guard let m = models else { return later(Self.modelRetry) }
        let g = generation
        let p = WakeProcessor(models: m, threshold: config.threshold) { [weak self] event in
            guard let me = self else { return }
            DispatchQueue.main.async { MainActor.assumeIsolated { me.handle(event, g) } }
        }
        processor = p
        listeningVersion = config.version
        let answering = answerNext
        answerNext = false
        if answering { mic.queue.async { p.startAnswer(noSpeechMs: Self.followUpMs) } }
        if mic.start({ samples in p.push(samples) }) {
            state = .listening
            if answering {
                Chime.play()
                onWake()
                toPage(VoiceEvent.wakeFollowUp)
            }
        } else {
            processor = nil
            toPage(VoiceEvent.error("The microphone isn't available."))
            later(Self.micRetry)
        }
    }

    private func handle(_ event: WakeProcessor.Event, _ g: Int) {
        guard g == generation, state == .listening else { return }
        switch event {
        case .wake:
            Chime.play()
            onWake()
            toPage(VoiceEvent.wake)
        case .level(let v):
            toPage(VoiceEvent.level(v))
        case .audio(let pcm):
            toPage(VoiceEvent.audio(pcm))
            // The microphone is free while the hub answers and the reply is spoken.
            stopListening(cancelIfCapturing: false)
            state = .waitingForReply
            let wait = generation
            replyTimer = Timer.scheduledTimer(withTimeInterval: Self.replyTimeout, repeats: false) { [weak self] _ in
                Task { @MainActor in
                    guard let self, wait == self.generation else { return }
                    self.replyFinished()
                }
            }
        case .noSpeech(let quiet):
            toPage(quiet ? VoiceEvent.cancelQuiet : VoiceEvent.cancel)
        case .failed(let message):
            WallMic.log.error("Voice stopped: \(message, privacy: .public)")
            models = nil // load it again next time
            modelDir = nil
            stopListening(cancelIfCapturing: true)
            later(Self.modelRetry)
        }
    }

    /// Let go of the microphone. Mid-request (another app took the audio, the wall left the screen):
    /// not "Listening…" any more.
    private func stopListening(cancelIfCapturing: Bool) {
        generation += 1
        pending?.cancel()
        pending = nil
        replyTimer?.invalidate()
        replyTimer = nil
        let wasListening = state == .listening
        state = .off
        guard wasListening else {
            processor = nil
            return
        }
        mic.stop()
        if cancelIfCapturing, let p = processor {
            mic.queue.async {
                let busy = p.capturing && !p.sent
                if busy { DispatchQueue.main.async { [weak self] in MainActor.assumeIsolated { self?.toPage(VoiceEvent.cancel) } } }
            }
        }
        processor = nil
    }
}

/// One listening run's audio work, on WallMic.queue only (the body of Android's listenOnce):
/// framing, the noise floor, openWakeWord, then the request's endpointing.
final class WakeProcessor: @unchecked Sendable {
    enum Event { case wake, level(Float), audio(Data), noSpeech(quiet: Bool), failed(String) }

    /// The chime itself (its first ~0.4 s) mustn't count as someone speaking.
    static let chimeFrames = 5

    private let features: WakeFeatures
    private var trigger: WakeTrigger
    private var noise = NoiseFloor()
    private var framer = VoiceFramer()
    private var endpointer: Endpointer?
    private var utterance = VoiceUtterance()
    private var frameNo = 0
    private let emit: @Sendable (Event) -> Void
    private let lock = NSLock()
    private var wantedThreshold: Float
    private(set) var capturing = false
    private(set) var sent = false
    private var failed = false

    init(models: WakeModels, threshold: Float, emit: @escaping @Sendable (Event) -> Void) {
        features = WakeFeatures(models)
        trigger = WakeTrigger(threshold: threshold)
        wantedThreshold = threshold
        self.emit = emit
    }

    /// Set from the main thread (the page changed the sensitivity).
    var threshold: Float {
        get { lock.lock(); defer { lock.unlock() }; return wantedThreshold }
        set { lock.lock(); wantedThreshold = newValue; lock.unlock() }
    }

    /// The answer to a question Newman asked: capture from now, no wake word (WallMic.queue).
    private var answering = false

    func startAnswer(noSpeechMs: Int) {
        capturing = true
        answering = true
        frameNo = 0
        utterance.clear()
        endpointer = Endpointer(noiseFloor: noise.floor(), noSpeechMs: noSpeechMs)
    }

    func push(_ samples: [Int16]) {
        var frames: [[Int16]] = []
        framer.push(samples) { frames.append($0) }
        for f in frames { frame(f) }
    }

    private func frame(_ frame: [Int16]) {
        if sent || failed { return }
        if !capturing {
            noise.add(VoiceAudio.rms(frame))
            trigger.threshold = threshold
            do {
                if let score = try features.push(frame), trigger.push(score) {
                    answering = false
                    capturing = true
                    frameNo = 0
                    utterance.clear()
                    endpointer = Endpointer(noiseFloor: noise.floor())
                    emit(.wake)
                }
            } catch {
                failed = true
                emit(.failed(String(describing: error)))
            }
            return
        }
        utterance.add(frame)
        frameNo += 1
        if frameNo % 2 == 0 { emit(.level(VoiceAudio.level(frame))) }
        let result: Endpointer.Result = frameNo <= Self.chimeFrames ? .continue : (endpointer?.push(frame) ?? .continue)
        switch result {
        case .end, .max:
            sent = true
            emit(.audio(utterance.bytes()))
        case .noSpeech:
            emit(.noSpeech(quiet: answering))
            answering = false
            capturing = false
            trigger.reset()
        case .continue:
            break
        }
    }
}
