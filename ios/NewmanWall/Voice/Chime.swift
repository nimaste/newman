import AVFoundation

/// The short, soft two-note chime played when the tablet hears "Hey Newman" (ChimeSound in
/// Shared/Core makes it; the same notes as Android's Chime.kt). Plays through the wall's
/// .playAndRecord session, so the microphone keeps running under it (the first 0.4 s after the
/// wake word don't count as speech). Main thread.
@MainActor
enum Chime {
    private static let wav = ChimeSound.wav(ChimeSound.pcm())
    private static var player: AVAudioPlayer?

    static func play() {
        do {
            let p = try AVAudioPlayer(data: wav)
            p.volume = 1
            player = p // kept until the next chime
            p.play()
        } catch {
            // no sound this time; the card on screen still shows it's listening
        }
    }
}
