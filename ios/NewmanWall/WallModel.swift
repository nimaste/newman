import AVFoundation
import SwiftUI
import UIKit
import WebKit

/// The wall tablet's state: settings, dimming after N idle minutes, waking on touch, the
/// JavaScript bridge's requests (window.NewmanWall), battery reports to the hub and "Hey Newman".
/// Dimming, brightness and the bridge behave like the Android wall's (WallActivity / WallBridge).
@MainActor
final class WallModel: ObservableObject {
    static let shared = WallModel()

    // Settings (this tablet only).
    /// Minutes without a touch before dimming; 0 = never.
    @Published var dimMinutes: Double {
        didSet { defaults.set(WallBrightness.dimMinutesSetting(dimMinutes), forKey: "dimMinutes") }
    }
    /// Brightness while awake, WallBrightness.floor...1, or WallBrightness.system for the iPad's own.
    @Published var awakeBrightness: Double {
        didSet {
            defaults.set(WallBrightness.awake(awakeBrightness), forKey: "awakeBrightness")
            if !dimmed { applyBrightness() }
        }
    }
    /// Brightness while dimmed: never below WallBrightness.floor (older saves of 0.05 read as the floor).
    @Published var dimBrightness: Double {
        didSet {
            defaults.set(WallBrightness.dimSetting(dimBrightness), forKey: "dimBrightness")
            if dimmed { applyBrightness() }
        }
    }

    /// Newman's alarms ringing: one of Newman's own sounds (WallTones.names).
    @Published var alarmTone: String {
        didSet { defaults.set(WallTones.named(alarmTone), forKey: "alarmTone") }
    }
    /// Newman's timers ringing: the same choice, its own.
    @Published var timerTone: String {
        didSet { defaults.set(WallTones.named(timerTone), forKey: "timerTone") }
    }
    /// "Snooze for" (minutes, WallRinging.snoozeChoices).
    @Published var snoozeMinutes: Int {
        didSet {
            defaults.set(WallRinging.snoozeSetting(snoozeMinutes), forKey: "snoozeMinutes")
            pageState()
        }
    }
    /// "Stop ringing after" (minutes, WallRinging.ringChoices).
    @Published var ringMinutes: Int {
        didSet {
            defaults.set(WallRinging.ringSetting(ringMinutes), forKey: "ringMinutes")
            pageState()
        }
    }

    // State.
    @Published private(set) var dimmed = false
    /// Dimmed, and the page didn't draw its own dimmed screen (an error, an older hub): darken it here.
    @Published private(set) var fallbackDim = false
    /// The page's NewmanWall.keepAwake: the screen stays on (default), or the iPad's Auto-Lock applies.
    @Published private(set) var keepAwake = true
    @Published var settingsOpen = false
    @Published var loadError: String?
    @Published var serverURL: URL? = ConnectionStore.shared.serverURL
    /// The Server screen over the wall (from settings, the page's own Server button, or a link).
    @Published var serverOpen = false
    /// A newman://connect link waiting for the Server screen.
    @Published var incomingLink: HubCandidate?
    /// The page's theme colour (NewmanWall.setTheme), behind it while it loads or can't be reached.
    @Published private(set) var shell: WallShell

    /// A level the page asked for (NewmanWall.setBrightness) - until the screen next dims.
    private var pageBrightness: Double?
    /// The iPad's own level before the wall changed it: back on "the iPad's own brightness", and when leaving.
    private var ownBrightness: Double?
    private var lastTouch = Date()
    private var timer: Timer?
    private var active = false
    private let defaults = UserDefaults.standard
    weak var webView: WKWebView?
    /// Reports the tablet's battery to the hub while the wall is showing.
    let battery = BatteryReporter()
    /// "Hey Newman" on this iPad (Voice/), while the wall is in front and the page turns it on.
    private(set) lazy var voice = VoiceListener(mic: .shared, toPage: { [weak self] in self?.toPage($0) },
                                                onWake: { [weak self] in self?.wake() })
    /// "Teach Hey Newman" clips, recorded for the page.
    private(set) lazy var clips = ClipRecorder(mic: .shared, listener: voice, toPage: { [weak self] in self?.toPage($0) })

    init() {
        let d = UserDefaults.standard
        dimMinutes = WallBrightness.dimMinutesSetting(d.object(forKey: "dimMinutes") as? Double ?? Double(WallBrightness.defaultDimMinutes))
        // A new install keeps the iPad's own brightness while awake (as the Android wall does).
        awakeBrightness = WallBrightness.awake(d.object(forKey: "awakeBrightness") as? Double ?? WallBrightness.system)
        dimBrightness = WallBrightness.dimSetting(d.object(forKey: "dimBrightness") as? Double ?? WallBrightness.defaultDim)
        shell = WallShell.saved(background: d.string(forKey: "shellBackground"), scheme: d.string(forKey: "shellScheme"))
        alarmTone = WallTones.named(d.string(forKey: "alarmTone") ?? WallTones.defaultName)
        timerTone = WallTones.named(d.string(forKey: "timerTone") ?? WallTones.defaultName)
        snoozeMinutes = WallRinging.snoozeSetting(d.object(forKey: "snoozeMinutes") as? Int ?? WallRinging.defaultSnooze)
        ringMinutes = WallRinging.ringSetting(d.object(forKey: "ringMinutes") as? Int ?? WallRinging.defaultRing)
        WallRinger.shared.onChange = { [weak self] in self?.ringChanged() }
        NotificationCenter.default.addObserver(forName: .newmanServerChanged, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.serverChanged() }
        }
        WallMic.shared.onInterruption = { [weak self] began in
            guard let self else { return }
            if began { self.clips.interrupted() }
            self.voice.setInterrupted(began)
        }
        WallMic.shared.onLost = { [weak self] in
            guard let self else { return }
            self.clips.interrupted()
            self.voice.micLost()
        }
    }

    var version: String {
        let v = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
        let b = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0"
        return "\(v) (\(b))"
    }

    // MARK: showing

    func becameActive() {
        active = true
        applyKeepAwake()
        wake()
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
        battery.start()
        // The microphone is only used while the wall is in front (iOS allows no background
        // listening for this); it may have been allowed in Settings meanwhile.
        voice.setPermitted(Self.micGranted)
        voice.setForeground(true)
        // The address stays the one chosen on the Server screen (no home/away switching on the
        // wall: a hub restart must not move it outside); WallWebView retries a failed load every 30 s.
        pageState() // (the microphone may have been allowed in Settings meanwhile)
    }

    func resignedActive() {
        active = false
        UIApplication.shared.isIdleTimerDisabled = false
        timer?.invalidate()
        timer = nil
        battery.stop()
        voice.setForeground(false)
        clips.cancel()
        WallRinger.shared.stop() // (iOS: no ringing from the background)
        restoreOwnBrightness()
    }

    /// keepAwake (the default): the screen stays on while the wall shows; off, the iPad's Auto-Lock
    /// turns it off as usual (it wakes fully bright when it comes back on).
    private func applyKeepAwake() {
        if active { UIApplication.shared.isIdleTimerDisabled = keepAwake }
    }

    // MARK: dimming

    func touched() {
        lastTouch = Date()
        if dimmed { wake() }
    }

    func wake() {
        lastTouch = Date()
        let was = dimmed
        dimmed = false
        fallbackDim = false
        applyBrightness()
        if was {
            refreshBridgeScript()
            dimToPage(false)
        }
    }

    /// Dims now: after the idle time, or from the page's moon button (NewmanWall.dim()).
    func dim() {
        guard !dimmed else { return }
        dimmed = true
        pageBrightness = nil
        fallbackDim = false
        applyBrightness()
        refreshBridgeScript()
        dimToPage(true)
    }

    private func tick() {
        guard !dimmed, !settingsOpen, !serverOpen, serverURL != nil, dimMinutes > 0, !WallRinger.shared.isRinging else { return }
        if Date().timeIntervalSince(lastTouch) >= dimMinutes * 60 { dim() }
    }

    /// Tells the page (a cancelable "newmanwall-dim" event). The hub's page draws its own dimmed screen
    /// (the time, softly) and calls preventDefault(); a page that doesn't is darkened here instead.
    private func dimToPage(_ on: Bool) {
        guard let w = webView, isHubPage, loadError == nil else {
            if on { fallbackDim = true }
            return
        }
        w.evaluateJavaScript(WallBridgeJS.dimEvent(on)) { [weak self] result, _ in
            let pageDims = (result as? Bool) == true // a Bool, not the Any?, crosses into the Task
            Task { @MainActor in
                guard let self, on, self.dimmed else { return }
                if !pageDims { self.fallbackDim = true }
            }
        }
    }

    private func applyBrightness() {
        if dimmed {
            setScreen(WallBrightness.dimmed(dimBrightness))
            return
        }
        // Something ringing: fully bright.
        let level = WallRinger.shared.isRinging ? 1 : (pageBrightness ?? WallBrightness.awake(awakeBrightness))
        if level < 0 {
            restoreOwnBrightness()
        } else {
            setScreen(level)
        }
    }

    private func setScreen(_ level: Double) {
        if ownBrightness == nil { ownBrightness = Double(UIScreen.main.brightness) }
        UIScreen.main.brightness = CGFloat(max(0, min(1, level)))
    }

    private func restoreOwnBrightness() {
        if let b = ownBrightness { UIScreen.main.brightness = CGFloat(b) }
        ownBrightness = nil
    }

    // MARK: JavaScript bridge

    func bridge(_ body: Any) {
        guard let msg = body as? [String: Any], let action = msg["action"] as? String else { return }
        switch action {
        case "setBrightness":
            if let v = (msg["value"] as? NSNumber)?.doubleValue, v.isFinite {
                pageBrightness = WallBrightness.awake(max(0, min(1, v)))
                if !dimmed { applyBrightness() }
            }
        case "wake":
            wake()
        case "dim":
            guard isHubPage else { return }
            dim()
        case "keepAwake":
            keepAwake = (msg["value"] as? Bool) ?? ((msg["value"] as? NSNumber)?.boolValue ?? true)
            applyKeepAwake()
        case "openSettings":
            guard isHubPage else { return }
            askForSettings()
        case "openServer":
            guard isHubPage else { return }
            serverOpen = true
        case "setTheme":
            guard isHubPage, let s = WallShell.parse(background: msg["background"] as? String, scheme: msg["scheme"] as? String) else { return }
            setShell(s)
        case "voiceConfig":
            guard isHubPage else { return }
            let cfg = VoicePageConfig.parse(msg["value"] as? String)
            voice.configure(cfg)
            if cfg.enabled && !Self.micGranted { askForMic() }
        case "voiceDone":
            guard isHubPage else { return }
            voice.replyFinished(listen: (msg["listen"] as? Bool) ?? ((msg["listen"] as? NSNumber)?.boolValue ?? false))
        case "ring":
            guard isHubPage, let r = WallRinging.parseRing(msg["value"] as? String) else { return }
            WallRinger.shared.start(r, tone: r.isTimer ? timerTone : alarmTone)
        case "stopRing":
            guard isHubPage else { return }
            WallRinger.shared.stop()
        case "openMicSettings":
            guard isHubPage, let url = URL(string: UIApplication.openSettingsURLString) else { return }
            UIApplication.shared.open(url)
        case "recordClip":
            guard isHubPage else { return }
            if !Self.micGranted {
                askForMic()
                toPage(VoiceEvent.clipError("Allow the microphone for Newman Wall in the iPad's Settings first."))
                return
            }
            let ms = (msg["ms"] as? NSNumber)?.intValue ?? 5000
            let fixed = (msg["fixed"] as? Bool) ?? ((msg["fixed"] as? NSNumber)?.boolValue ?? false)
            clips.start(maxMs: ms, fixed: fixed)
        case "stopClip":
            guard isHubPage else { return }
            clips.stop()
        default:
            break
        }
    }

    /// Settings need the iPad's passcode / Face ID / Touch ID (the corner hold, or the page's "Tablet settings").
    func askForSettings() {
        Task { @MainActor in
            switch await DeviceAuth.authenticate(reason: "Open the wall's settings") {
            case .ok: settingsOpen = true
            case .cancelled, .failed: break
            }
        }
    }

    // MARK: shell colour

    private func setShell(_ s: WallShell) {
        if s != shell {
            shell = s
            defaults.set(s.background, forKey: "shellBackground")
            defaults.set(s.dark ? "dark" : "light", forKey: "shellScheme")
        }
        if let w = webView {
            w.backgroundColor = Self.uiColor(s.background)
            w.scrollView.backgroundColor = w.backgroundColor
        }
    }

    /// "#rrggbb" as a colour.
    static func color(_ hex: String) -> Color {
        let c = WallShell(background: hex, dark: true).rgb
        return Color(red: c[0], green: c[1], blue: c[2])
    }

    static func uiColor(_ hex: String) -> UIColor {
        let c = WallShell(background: hex, dark: true).rgb
        return UIColor(red: c[0], green: c[1], blue: c[2], alpha: 1)
    }

    // MARK: alarms and timers ringing

    /// Ringing started or stopped: woken and fully bright while it rings, the usual level after.
    private func ringChanged() {
        if WallRinger.shared.isRinging {
            wake()
        } else {
            applyBrightness()
        }
    }

    /// What NewmanWall.micAllowed() / ringSettings() say, sent to the page (with 'newmanwall-resume').
    private func pageState() {
        let js = WallBridgeJS.state(micAllowed: Self.micNotRefused, ringSettings: ringSettingsJSON)
        refreshBridgeScript()
        guard isHubPage, let w = webView else { return }
        w.evaluateJavaScript(js, completionHandler: nil)
    }

    var ringSettingsJSON: String { WallRinging.settingsJSON(snooze: snoozeMinutes, ring: ringMinutes) }

    // MARK: voice

    static var micGranted: Bool { AVAudioApplication.shared.recordPermission == .granted }
    /// Not refused (still to be asked counts as allowed: the page shows "can't hear you" only once refused).
    static var micNotRefused: Bool { AVAudioApplication.shared.recordPermission != .denied }

    /// The microphone is asked for once (iOS itself never asks twice: Settings from then on).
    private func askForMic() {
        switch AVAudioApplication.shared.recordPermission {
        case .granted:
            voice.setPermitted(true)
        case .undetermined:
            AVAudioApplication.requestRecordPermission { granted in
                Task { @MainActor in
                    let m = WallModel.shared
                    m.voice.setPermitted(granted)
                    m.pageState()
                    if !granted { m.toPage(VoiceEvent.error("Allow the microphone for Newman Wall in the iPad's Settings to use voice.")) }
                }
            }
        default:
            break
        }
    }

    /// True while the hub's own page is showing (the bridge's requests are answered only for it).
    var isHubPage: Bool {
        guard let host = webView?.url?.host?.lowercased(), let hub = ConnectionStore.shared.serverURL?.host?.lowercased() else { return false }
        return host == hub
    }

    /// window.NewmanVoice.event(json) on the hub's page.
    func toPage(_ json: String) {
        guard isHubPage, let w = webView else { return }
        w.evaluateJavaScript("window.NewmanVoice && window.NewmanVoice.event(\(json)); void 0;", completionHandler: nil)
    }

    /// A page (re)load started: voice is off until the signed-in page turns it on again.
    func pageStarted() {
        voice.configure(VoicePageConfig())
        clips.cancel()
    }

    /// Injected at document start in the main frame: window.NewmanWall (WallBridgeJS), knowing
    /// whether the screen is dimmed as the page loads (NewmanWall.isDimmed()).
    var bridgeScript: String {
        WallBridgeJS.script(version: version, dimmed: dimmed, micAllowed: Self.micNotRefused, ringSettings: ringSettingsJSON)
    }

    /// The next page load gets the current dimmed state.
    private func refreshBridgeScript() {
        guard let ucc = webView?.configuration.userContentController else { return }
        ucc.removeAllUserScripts()
        ucc.addUserScript(WKUserScript(source: bridgeScript, injectionTime: .atDocumentStart, forMainFrameOnly: true))
    }

    // MARK: page

    func reload() {
        loadError = nil
        if let w = webView, w.url != nil {
            w.reload()
        } else {
            NotificationCenter.default.post(name: .wallLoadHome, object: nil)
        }
    }

    func loadHome() {
        loadError = nil
        NotificationCenter.default.post(name: .wallLoadHome, object: nil)
    }

    func setServer(_ url: URL) {
        ConnectionStore.shared.serverURL = url
        serverURL = url
        loadHome()
    }

    /// The address in use changed (saved on the Server screen): show the wall from there.
    func serverChanged() {
        serverURL = ConnectionStore.shared.serverURL
        loadHome()
    }

    /// newman://connect?... - opens the Server screen with that hub filled in.
    func open(_ url: URL) {
        guard let link = ConnectLink.parse(url.absoluteString) else { return }
        incomingLink = link
        if serverURL != nil { serverOpen = true }
    }

    /// Signs the wall out of the page: forgets its cookies and storage.
    func clearWebsiteData() async {
        let store = WKWebsiteDataStore.default()
        let types = WKWebsiteDataStore.allWebsiteDataTypes()
        await store.removeData(ofTypes: types, modifiedSince: .distantPast)
        loadHome()
    }
}

extension Notification.Name {
    static let wallLoadHome = Notification.Name("ca.nimaste.newman.wall.loadHome")
}
