import SwiftUI
import UIKit
import WebKit

/// A camera picture that plays live video when it can: the refreshing still stays up until the
/// video is actually playing (and comes back if it fails). Cameras without `live` just refresh
/// their still every 2 s, as before.
struct LiveCameraView<Placeholder: View>: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    let entity: Entity
    var quality: LiveQuality = .sub
    /// The full-screen camera: always streams, outside the 4-tile pool.
    var priority = false
    var stillHeight = 480
    var contentMode: ContentMode = .fill
    var onPlaying: ((Bool) -> Void)? = nil
    @ViewBuilder var placeholder: () -> Placeholder

    @State private var visible = false
    @State private var playing = false

    var body: some View {
        ZStack {
            RemoteImage(url: model.stillURL(entity, height: stillHeight), refresh: playing ? nil : 2,
                        contentMode: contentMode, placeholder: placeholder)
            if entity.live != nil {
                LiveVideoView(entity: entity, quality: quality, priority: priority,
                              active: visible && scenePhase == .active, cover: contentMode == .fill) { isPlaying in
                    playing = isPlaying
                    onPlaying?(isPlaying)
                }
                .opacity(playing ? 1 : 0)
                .allowsHitTesting(false)
                .accessibilityHidden(true) // the player page; the camera is labelled by its container
            }
        }
        .onAppear { visible = true }
        .onDisappear { visible = false }
    }
}

/// The shared player page (mobile-shared/live-player.html) in a web view, fed by a live socket
/// the app opens itself through the mTLS session (certificate, Bearer token, Cloudflare headers).
/// Protocol: see the header comment of live-player.html.
struct LiveVideoView: UIViewRepresentable {
    let entity: Entity
    var quality: LiveQuality = .sub
    var priority = false
    /// On screen and the app in the foreground.
    var active: Bool
    /// Fill the frame (tiles) rather than letterbox (full screen).
    var cover = true
    var onPlaying: (Bool) -> Void

    func makeCoordinator() -> LivePlayerController {
        LivePlayerController(entityId: entity.id)
    }

    func makeUIView(context: Context) -> WKWebView {
        let c = context.coordinator
        c.update(livePath: entity.live, quality: quality, priority: priority, cover: cover, onPlaying: onPlaying)
        let web = c.makeWebView()
        c.setActive(active)
        return web
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {
        let c = context.coordinator
        c.update(livePath: entity.live, quality: quality, priority: priority, cover: cover, onPlaying: onPlaying)
        c.setActive(active)
    }

    static func dismantleUIView(_ uiView: WKWebView, coordinator: LivePlayerController) {
        coordinator.teardown()
    }
}

/// The 4-tile cap, shared by every camera tile (the full-screen camera is exempt).
enum LiveStreamPool {
    static let maxTileStreams = 4
    static let shared = StreamPool(capacity: maxTileStreams)
}

/// Runs one live session: page ready -> start -> codecs -> socket -> frames into the page.
final class LivePlayerController: NSObject, WKScriptMessageHandler {
    private let entityId: String
    private let poolId = UUID().uuidString
    private var livePath: String?
    private var quality: LiveQuality = .sub
    private var priority = false
    private var cover = true
    private var onPlaying: ((Bool) -> Void)?

    private weak var web: WKWebView?
    private var pageReady = false
    private var wantActive = false
    private var hasSlot = false
    private var running = false
    private var socket: URLSessionWebSocketTask?
    private var generation = 0
    private var retryTimer: Timer?

    init(entityId: String) {
        self.entityId = entityId
        super.init()
    }

    func update(livePath: String?, quality: LiveQuality, priority: Bool, cover: Bool, onPlaying: @escaping (Bool) -> Void) {
        let changed = livePath != self.livePath || quality != self.quality
        self.livePath = livePath
        self.quality = quality
        self.priority = priority
        self.onPlaying = onPlaying
        if cover != self.cover {
            self.cover = cover
            if pageReady { eval(LiveScript.fit(cover: cover)) }
        }
        if changed && running {
            stopSession()
            evaluateRun()
        }
    }

    func makeWebView() -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []
        let ucc = WKUserContentController()
        ucc.add(WeakLiveMessageProxy(self), name: "newmanLive")
        config.userContentController = ucc

        let web = WKWebView(frame: .zero, configuration: config)
        web.isOpaque = false
        web.backgroundColor = .black
        web.scrollView.backgroundColor = .black
        web.scrollView.isScrollEnabled = false
        web.scrollView.contentInsetAdjustmentBehavior = .never
        web.isUserInteractionEnabled = false
        self.web = web
        if let page = Bundle.main.url(forResource: "live-player", withExtension: "html") {
            web.loadFileURL(page, allowingReadAccessTo: page.deletingLastPathComponent())
        }
        return web
    }

    // MARK: wanting to run (visible, foreground, a pool slot)

    func setActive(_ active: Bool) {
        guard active != wantActive else { return }
        wantActive = active
        if active {
            if priority {
                hasSlot = true
            } else {
                hasSlot = LiveStreamPool.shared.request(poolId) { [weak self] in
                    DispatchQueue.main.async {
                        guard let self else { return }
                        self.hasSlot = true
                        self.evaluateRun()
                    }
                }
            }
        } else {
            releaseSlot()
        }
        evaluateRun()
    }

    private func releaseSlot() {
        if !priority { LiveStreamPool.shared.release(poolId) }
        hasSlot = false
    }

    private func evaluateRun() {
        let should = wantActive && hasSlot && pageReady && livePath != nil
        if should && !running {
            running = true
            eval(LiveScript.start)
        } else if !should && running {
            stopSession()
        }
    }

    private func stopSession() {
        running = false
        generation += 1
        retryTimer?.invalidate()
        retryTimer = nil
        socket?.cancel(with: .goingAway, reason: nil)
        socket = nil
        eval(LiveScript.stop)
        notify(false)
    }

    /// Tell SwiftUI after the current update pass (never mid-update).
    private func notify(_ playing: Bool) {
        let cb = onPlaying
        DispatchQueue.main.async { cb?(playing) }
    }

    func teardown() {
        stopSession()
        wantActive = false
        releaseSlot()
        web?.configuration.userContentController.removeScriptMessageHandler(forName: "newmanLive")
    }

    // MARK: page -> app

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        switch LivePageMessage.parse(message.body) {
        case .ready:
            pageReady = true
            eval(LiveScript.fit(cover: cover))
            evaluateRun()
        case .codecs(let codecs):
            guard running else { return }
            openSocket(codecs: codecs)
        case .state(let state):
            switch state {
            case .playing:
                notify(true)
            case .failed:
                notify(false)
                closeSocket()
                scheduleRetry(after: LiveScript.retryAfterFailure)
            case .retry:
                closeSocket()
                if running { eval(LiveScript.start) }
            case .loading:
                break
            }
        case .unknown:
            break
        }
    }

    private func scheduleRetry(after seconds: TimeInterval) {
        retryTimer?.invalidate()
        guard running else { return }
        retryTimer = Timer.scheduledTimer(withTimeInterval: seconds, repeats: false) { [weak self] _ in
            guard let self, self.running else { return }
            self.eval(LiveScript.start)
        }
    }

    // MARK: the live socket

    private func openSocket(codecs: String) {
        closeSocket()
        guard let base = ConnectionStore.shared.serverURL, let path = livePath,
              let url = LiveScript.socketURL(base: base, livePath: path, quality: quality) else { return }
        generation += 1
        let gen = generation
        var req = NewmanURLSession.request(url)
        req.timeoutInterval = 20
        let task = NewmanURLSession.shared.webSocketTask(with: req)
        task.maximumMessageSize = 16 * 1024 * 1024
        socket = task
        task.resume()
        task.send(.string(LiveScript.mseRequest(codecs: codecs))) { _ in }
        receive(task, gen)
    }

    private func closeSocket() {
        generation += 1
        socket?.cancel(with: .goingAway, reason: nil)
        socket = nil
    }

    /// One receive at a time, each delivered to the page in order on the main queue.
    private func receive(_ task: URLSessionWebSocketTask, _ gen: Int) {
        task.receive { [weak self] result in
            DispatchQueue.main.async {
                guard let self, gen == self.generation, self.running else { return }
                switch result {
                case .success(.string(let text)):
                    self.eval(LiveScript.text(text))
                    self.receive(task, gen)
                case .success(.data(let data)):
                    self.eval(LiveScript.data(data))
                    self.receive(task, gen)
                case .success:
                    self.receive(task, gen)
                case .failure:
                    // The socket dropped: show the still and try again later.
                    self.eval(LiveScript.stop)
                    self.notify(false)
                    self.socket = nil
                    self.scheduleRetry(after: LiveScript.retryAfterFailure)
                }
            }
        }
    }

    private func eval(_ js: String) {
        web?.evaluateJavaScript(js, completionHandler: nil)
    }
}

/// WKUserContentController keeps its message handlers strongly; this breaks the cycle.
final class WeakLiveMessageProxy: NSObject, WKScriptMessageHandler {
    weak var target: WKScriptMessageHandler?

    init(_ target: WKScriptMessageHandler) {
        self.target = target
        super.init()
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.userContentController(userContentController, didReceive: message)
    }
}
