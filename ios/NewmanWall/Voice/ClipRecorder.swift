import Foundation

/// "Teach Hey Newman" recordings (the page: NewmanWall.recordClip(maxMs, fixed) / stopClip()),
/// as Android's ClipRecorder.kt. One recording at a time: the wake-word listener is paused (it lets
/// go of the microphone first), the same microphone records (WallMic, 16 kHz mono), ClipCapture
/// decides when it ends (a spoken "Hey Newman": first word to the pause after it; the talking step:
/// a fixed length), and the page gets {"type":"clip","pcm","rate","ms"} - or "clip-none" (nobody
/// spoke) / "clip-error" - plus "clip-level" for its meter. The page uploads each clip to the hub
/// (POST api/voice/teach/clips). The microphone is always released, and the listener resumed,
/// afterwards. Nothing is kept on the tablet. Main thread.
@MainActor
final class ClipRecorder {
    private let mic: WallMic
    private let listener: VoiceListener
    private let toPage: (String) -> Void
    private var generation = 0
    private var session: ClipSession?
    private var opening: Task<Void, Never>?

    init(mic: WallMic, listener: VoiceListener, toPage: @escaping (String) -> Void) {
        self.mic = mic
        self.listener = listener
        self.toPage = toPage
    }

    var recording: Bool { session != nil || opening != nil }

    /// A recording already under way is dropped first.
    func start(maxMs: Int, fixed: Bool) {
        drop()
        generation += 1
        let g = generation
        listener.pause(true) // lets go of the microphone at once
        opening = Task { @MainActor [weak self] in
            // The microphone may take a moment to come free: 6 tries, 250 ms apart.
            for attempt in 0..<6 {
                guard let self, g == self.generation else { return }
                if attempt > 0 { try? await Task.sleep(nanoseconds: 250_000_000) }
                guard g == self.generation, !Task.isCancelled else { return }
                if self.open(g, maxMs: maxMs, fixed: fixed) {
                    self.opening = nil
                    return
                }
            }
            guard let self, g == self.generation else { return }
            self.opening = nil
            self.toPage(VoiceEvent.clipError("The microphone isn't available."))
            self.finished(g)
        }
    }

    private func open(_ g: Int, maxMs: Int, fixed: Bool) -> Bool {
        let s = ClipSession(maxMs: maxMs, fixed: fixed) { [weak self] event in
            guard let me = self else { return }
            DispatchQueue.main.async { MainActor.assumeIsolated { me.handle(event, g) } }
        }
        guard mic.start({ samples in s.push(samples) }) else { return false }
        session = s
        return true
    }

    /// The page's "stop": a fixed-length recording hands over what it has; a spoken one is dropped.
    func stop() {
        guard let s = session else { return }
        mic.queue.async { s.stopAsked() }
    }

    /// Drop any recording (the wall left the screen, another page loaded).
    func cancel() {
        drop()
        listener.pause(false)
    }

    /// The microphone was taken away (a call, Siri) mid-recording.
    func interrupted() {
        guard session != nil else { return }
        let g = generation
        mic.stop()
        session = nil
        toPage(VoiceEvent.clipError("The microphone stopped."))
        finished(g)
    }

    private func drop() {
        opening?.cancel()
        opening = nil
        if session != nil {
            generation += 1
            mic.stop()
            session = nil
        }
    }

    private func handle(_ event: ClipSession.Event, _ g: Int) {
        guard g == generation, session != nil else { return }
        switch event {
        case .level(let v):
            toPage(VoiceEvent.clipLevel(v))
            return
        case .clip(let pcm, let ms):
            toPage(VoiceEvent.clip(pcm, ms: ms))
        case .nobody:
            toPage(VoiceEvent.clipNone)
        case .dropped:
            break
        }
        mic.stop()
        session = nil
        finished(g)
    }

    private func finished(_ g: Int) {
        if g == generation { listener.pause(false) }
    }
}

/// One clip's audio work, on WallMic.queue only.
final class ClipSession: @unchecked Sendable {
    enum Event { case level(Float), clip(Data, Int), nobody, dropped }

    private var cap: ClipCapture
    private var framer = VoiceFramer()
    private var frames = 0
    private var done = false
    private let emit: @Sendable (Event) -> Void

    init(maxMs: Int, fixed: Bool, emit: @escaping @Sendable (Event) -> Void) {
        cap = ClipCapture(maxMs: maxMs, fixed: fixed)
        self.emit = emit
    }

    func push(_ samples: [Int16]) {
        if done { return }
        var got: [[Int16]] = []
        framer.push(samples) { got.append($0) }
        for f in got where cap.result == .continue {
            cap.push(f)
            frames += 1
            if frames % 2 == 0 { emit(.level(VoiceAudio.level(f))) }
        }
        if cap.result != .continue { finish() }
    }

    func stopAsked() {
        if done { return }
        if cap.fixed {
            cap.finishEarly()
            finish()
        } else {
            done = true
            emit(.dropped)
        }
    }

    private func finish() {
        guard !done else { return }
        done = true
        if cap.result == .done && cap.keptMs > 0 {
            emit(.clip(cap.pcm(), cap.keptMs))
        } else {
            emit(.nobody)
        }
    }
}
