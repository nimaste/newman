import AVFoundation
import Foundation
import os

/// The wall's microphone as voice uses it (VoiceListener.openVoiceMic on Android): 16 kHz mono
/// 16-bit samples from an AVAudioEngine input tap (the hardware format converted with
/// AVAudioConverter), handed to one consumer at a time on [queue] - the wake-word listener or a
/// "Teach Hey Newman" recording, never both.
///
/// Audio session: .playAndRecord, mode .measurement, options .defaultToSpeaker (the speaker, not
/// a receiver), .mixWithOthers and .allowBluetoothA2DP. .measurement turns off iOS's voice
/// processing (no AGC / noise suppression / echo cancellation), which is what the Android wall
/// records (VOICE_RECOGNITION, unprocessed) and what openWakeWord was trained on; the wall's own
/// music and timer beeps play on through the speaker, and the page ducks its music the moment the
/// wake word is heard, so the command itself is recorded over a quiet room. .voiceChat was not
/// chosen: its echo cancellation would help over loud music, but it ducks all other audio, turns
/// playback into "phone call" sound and changes the mic level the wake model was tuned for.
/// (If playback sounds too quiet on a given iPad, [mode] = .default is the one-line alternative.)
///
/// iOS takes the audio away for calls, FaceTime and Siri (an interruption): [onInterruption] says
/// so (true = began) and the owner lets go until it ends - iOS's version of Android's MicGate. A
/// route or configuration change (headphones, the web view changing the session for its own media)
/// stops the engine: it is set up again with the new format; a tap that goes quiet for 3 s is
/// restarted (at most every 5 s). Main thread, except the consumer (on [queue]).
@MainActor
final class WallMic {
    static let shared = WallMic()
    /// Used from any thread (the ONNX models log from WallMic.queue).
    nonisolated static let log = Logger(subsystem: "ca.nimaste.newman.wall", category: "NewmanVoice")

    static let mode: AVAudioSession.Mode = .measurement
    static let options: AVAudioSession.CategoryOptions = [.defaultToSpeaker, .mixWithOthers, .allowBluetoothA2DP]

    /// Where every sample is handled (ONNX, endpointing, clips) - off the main thread and off the audio thread.
    nonisolated let queue = DispatchQueue(label: "ca.nimaste.newman.wall.voice", qos: .userInitiated)

    /// true = another app / a call / Siri took the audio; false = it's back.
    var onInterruption: ((Bool) -> Void)?
    /// The engine stopped and couldn't be started again (the current consumer has lost the microphone).
    var onLost: (() -> Void)?

    private var engine = AVAudioEngine()
    /// The consumer: @Sendable so it is never taken for a main-actor closure (it runs on [queue]).
    typealias Consumer = @Sendable ([Int16]) -> Void
    private var consumer: Consumer?
    private var configObserver: NSObjectProtocol?
    private var observers: [NSObjectProtocol] = []
    private var watchdog: Timer?
    private var lastRestart = Date.distantPast
    private let tapState = TapState()

    var isRunning: Bool { consumer != nil }

    private init() {
        let nc = NotificationCenter.default
        let session = AVAudioSession.sharedInstance()
        observers.append(nc.addObserver(forName: AVAudioSession.interruptionNotification, object: session, queue: .main) { [weak self] n in
            let raw = n.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
            let began = raw.flatMap(AVAudioSession.InterruptionType.init(rawValue:)) == .began
            Task { @MainActor in self?.interrupted(began) }
        })
        observers.append(nc.addObserver(forName: AVAudioSession.mediaServicesWereResetNotification, object: session, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.mediaServicesReset() }
        })
    }

    // MARK: the session

    /// .playAndRecord / .measurement, only changed when it differs (changing it while the page plays
    /// music can glitch that sound for a moment).
    static func configureSession() throws {
        let s = AVAudioSession.sharedInstance()
        if s.category != .playAndRecord || s.mode != mode || s.categoryOptions != options {
            try s.setCategory(.playAndRecord, mode: mode, options: options)
        }
        try s.setActive(true)
    }

    // MARK: start / stop

    /// Start handing samples to [consumer] (on [queue]); false when the microphone isn't available.
    /// A consumer already running is replaced (its later samples are dropped).
    @discardableResult
    func start(_ consumer: @escaping Consumer) -> Bool {
        stopEngine()
        self.consumer = consumer
        if startEngine() {
            startWatchdog()
            return true
        }
        self.consumer = nil
        return false
    }

    /// Let go of the microphone. Samples already on their way are dropped.
    func stop() {
        consumer = nil
        watchdog?.invalidate()
        watchdog = nil
        stopEngine()
    }

    private func startEngine() -> Bool {
        guard let consumer else { return false }
        do {
            try Self.configureSession()
        } catch {
            Self.log.warning("Audio session: \(error.localizedDescription, privacy: .public)")
            return false
        }
        let input = engine.inputNode
        let hw = input.outputFormat(forBus: 0)
        guard hw.sampleRate > 0, hw.channelCount > 0,
              let target = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: Double(VoiceAudio.rate), channels: 1, interleaved: true),
              let converter = AVAudioConverter(from: hw, to: target) else {
            Self.log.warning("Microphone: no input format (\(hw.sampleRate, privacy: .public) Hz, \(hw.channelCount, privacy: .public) ch)")
            return false
        }
        converter.downmix = true
        let token = tapState.next()
        input.removeTap(onBus: 0)
        // ~100 ms per buffer at 48 kHz; the converter keeps the resampler's state between buffers.
        input.installTap(onBus: 0, bufferSize: 4_800, format: hw,
                         block: Self.tap(state: tapState, token: token, converter: converter, target: target, queue: queue, consumer: consumer))
        engine.prepare()
        do {
            try engine.start()
        } catch {
            Self.log.warning("Microphone: \(error.localizedDescription, privacy: .public)")
            input.removeTap(onBus: 0)
            tapState.invalidate()
            return false
        }
        tapState.heard()
        configObserver = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.configurationChanged() }
        }
        return true
    }

    /// The tap block, made outside the main actor: it runs on the audio engine's own thread.
    nonisolated private static func tap(state: TapState, token: Int, converter: AVAudioConverter, target: AVAudioFormat,
                                        queue: DispatchQueue, consumer: @escaping Consumer) -> AVAudioNodeTapBlock {
        return { buffer, _ in
            guard state.isCurrent(token), let samples = convert(buffer, with: converter, to: target) else { return }
            state.heard()
            queue.async {
                if state.isCurrent(token) { consumer(samples) }
            }
        }
    }

    private func stopEngine() {
        tapState.invalidate()
        engine.inputNode.removeTap(onBus: 0)
        if engine.isRunning { engine.stop() }
        // The configuration-change observer belongs to this run of the engine.
        if let o = configObserver { NotificationCenter.default.removeObserver(o) }
        configObserver = nil
    }

    /// The hardware's buffer -> 16 kHz mono Int16. Runs on the tap's thread.
    nonisolated private static func convert(_ buffer: AVAudioPCMBuffer, with converter: AVAudioConverter, to target: AVAudioFormat) -> [Int16]? {
        let ratio = target.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 64)
        guard let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity) else { return nil }
        // A box, not a captured var: the SDK may declare the input block @Sendable.
        let given = GivenFlag()
        var error: NSError?
        let status = converter.convert(to: out, error: &error) { _, inputStatus in
            if given.value {
                inputStatus.pointee = .noDataNow
                return nil
            }
            given.value = true
            inputStatus.pointee = .haveData
            return buffer
        }
        guard status != .error, let ch = out.int16ChannelData, out.frameLength > 0 else { return nil }
        return Array(UnsafeBufferPointer(start: ch[0], count: Int(out.frameLength)))
    }

    // MARK: keeping it going

    private func restart(_ why: String) {
        guard consumer != nil else { return }
        guard Date().timeIntervalSince(lastRestart) >= 5 else { return }
        lastRestart = Date()
        Self.log.info("Microphone: restarting (\(why, privacy: .public))")
        stopEngine()
        if !startEngine() {
            Self.log.warning("Microphone: couldn't restart")
            let lost = onLost
            stop()
            lost?()
        }
    }

    private func startWatchdog() {
        watchdog?.invalidate()
        watchdog = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.consumer != nil else { return }
                if self.tapState.quietFor() > 3 { self.restart("no audio for 3 s") }
            }
        }
    }

    private func configurationChanged() {
        guard consumer != nil else { return }
        lastRestart = .distantPast // a real change: restart now
        restart("the audio configuration changed")
    }

    private func interrupted(_ began: Bool) {
        Self.log.info("Audio interruption \(began ? "began" : "ended", privacy: .public)")
        onInterruption?(began)
    }

    private func mediaServicesReset() {
        Self.log.warning("Media services were reset")
        let had = consumer
        stop()
        engine = AVAudioEngine()
        if let had, !start(had) { onLost?() }
    }
}

/// Whether the converter's input block already handed over this buffer (one call, one thread).
private final class GivenFlag: @unchecked Sendable {
    var value = false
}

/// Which tap is current (a restarted or stopped one's samples are dropped) and when it last
/// delivered audio. Shared by the main thread, the tap's thread and [WallMic.queue].
final class TapState: @unchecked Sendable {
    private let lock = NSLock()
    private var token = 0
    private var current = -1
    private var last = Date()

    func next() -> Int {
        lock.lock(); defer { lock.unlock() }
        token += 1
        current = token
        last = Date()
        return token
    }

    func invalidate() {
        lock.lock(); defer { lock.unlock() }
        current = -1
    }

    func isCurrent(_ t: Int) -> Bool {
        lock.lock(); defer { lock.unlock() }
        return current == t
    }

    func heard() {
        lock.lock(); defer { lock.unlock() }
        last = Date()
    }

    func quietFor() -> TimeInterval {
        lock.lock(); defer { lock.unlock() }
        return Date().timeIntervalSince(last)
    }
}
