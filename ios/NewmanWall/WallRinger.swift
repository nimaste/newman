import AVFoundation
import Foundation

/// Newman's alarms and timers ringing on the iPad (Android's AlarmRinger): the sound chosen in the wall's
/// settings (Newman's own, WallTones - iOS lets no app use the iPad's alarm tones) looping at full
/// volume through the app's audio, until stopped or for the "Stop ringing after" time the page passes.
/// The iPad has no separate alarm volume: it rings at the iPad's volume, ignoring the silent switch.
/// It rings only while the wall is in front (iOS lets an app ring by itself in the background only
/// through notifications); the page rings an alarm itself when the hub doesn't say.
@MainActor
final class WallRinger {
    static let shared = WallRinger()

    private var player: AVAudioPlayer?
    private var cap: Timer?
    private var previewStop: Timer?
    private(set) var current: WallRinging.Ring?
    /// Heard when ringing starts or stops (WallModel: the screen fully bright).
    var onChange: (() -> Void)?

    var isRinging: Bool { current != nil }

    func start(_ ring: WallRinging.Ring, tone: String) {
        stopNow()
        current = ring
        play(tone, looping: true)
        cap = Timer.scheduledTimer(withTimeInterval: ring.capSeconds, repeats: false) { [weak self] _ in
            Task { @MainActor in self?.stop() }
        }
        onChange?()
    }

    func stop() {
        let was = current != nil
        stopNow()
        if was { onChange?() }
    }

    /// The settings' ▶ Play: [tone] for 2 seconds (not over a real ring).
    func preview(_ tone: String) {
        guard current == nil else { return }
        stopPlayer()
        play(tone, looping: false)
        previewStop?.invalidate()
        previewStop = Timer.scheduledTimer(withTimeInterval: 2, repeats: false) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.current == nil else { return }
                self.stopPlayer()
            }
        }
    }

    private func play(_ tone: String, looping: Bool) {
        let session = AVAudioSession.sharedInstance()
        // With "Hey Newman" on, the session already plays and records (the silent switch doesn't mute it);
        // otherwise play as playback, so the silent switch doesn't silence an alarm.
        if session.category != .playAndRecord {
            try? session.setCategory(.playback, options: [.mixWithOthers])
        }
        try? session.setActive(true)
        do {
            let p = try AVAudioPlayer(data: WallTones.wav(tone))
            p.numberOfLoops = looping ? -1 : 0
            p.volume = 1
            p.prepareToPlay()
            p.play()
            player = p
        } catch {
            WallMic.log.warning("Couldn't play the alarm sound: \(String(describing: error), privacy: .public)")
        }
    }

    private func stopPlayer() {
        player?.stop()
        player = nil
    }

    private func stopNow() {
        cap?.invalidate()
        cap = nil
        previewStop?.invalidate()
        previewStop = nil
        stopPlayer()
        current = nil
    }
}
