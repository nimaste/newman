import SwiftUI
import UIKit
import UIKit.UIGestureRecognizerSubclass
import WebKit

/// The hub's own wall screen (<server>/wall) full screen. The tablet signs in inside the page,
/// as in a browser (its session cookie lives in the persistent website data store).
///
/// - Client certificate: answered in webView(_:didReceive:completionHandler:) from the Keychain.
/// - Cloudflare Access: WKWebView can't add headers to sub-requests (scripts, pictures,
///   WebSockets), so before loading, the app calls the hub once through its own URLSession with
///   the service-token headers; Cloudflare answers with its CF_Authorization cookie, which is
///   copied into the web view's cookie store so every later request carries it. The main page
///   load also carries the headers. Limitation: when that cookie expires (the Access
///   application's session duration) sub-requests fail until the next page load, which renews it -
///   the app reloads after a failed load and on every "Reload".
struct WallWebView: UIViewRepresentable {
    @ObservedObject var model: WallModel

    func makeCoordinator() -> Coordinator { Coordinator(model: model) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []
        let ucc = WKUserContentController()
        ucc.addUserScript(WKUserScript(source: model.bridgeScript, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        ucc.add(WeakScriptHandler(context.coordinator), name: "newmanWall")
        config.userContentController = ucc

        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = context.coordinator
        web.uiDelegate = context.coordinator
        web.isOpaque = false
        // The page's theme colour (NewmanWall.setTheme, kept from the last run) until it paints.
        web.backgroundColor = WallModel.uiColor(model.shell.background)
        web.scrollView.backgroundColor = web.backgroundColor
        web.scrollView.contentInsetAdjustmentBehavior = .never
        web.scrollView.bounces = false
        web.allowsBackForwardNavigationGestures = false
        #if DEBUG
        if #available(iOS 16.4, *) { web.isInspectable = true }
        #endif

        // Any touch counts as activity (never interferes with the page).
        let m = model
        let activity = ActivityRecognizer { Task { @MainActor in m.touched() } }
        activity.delegate = context.coordinator
        web.addGestureRecognizer(activity)

        // The way into settings: hold the top-left corner for 3 seconds.
        let hold = UILongPressGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.cornerHold(_:)))
        hold.minimumPressDuration = 3
        hold.cancelsTouchesInView = false
        hold.delegate = context.coordinator
        web.addGestureRecognizer(hold)
        context.coordinator.hold = hold

        context.coordinator.web = web
        model.webView = web
        context.coordinator.observe()
        context.coordinator.loadHome()
        return web
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}

    static func dismantleUIView(_ uiView: WKWebView, coordinator: Coordinator) {
        uiView.configuration.userContentController.removeScriptMessageHandler(forName: "newmanWall")
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler, UIGestureRecognizerDelegate {
        let model: WallModel
        weak var web: WKWebView?
        weak var hold: UILongPressGestureRecognizer?
        private var retry: Timer?
        private var observer: NSObjectProtocol?

        init(model: WallModel) { self.model = model }

        deinit {
            if let observer { NotificationCenter.default.removeObserver(observer) }
        }

        func observe() {
            observer = NotificationCenter.default.addObserver(forName: .wallLoadHome, object: nil, queue: .main) { [weak self] _ in
                self?.loadHome()
            }
        }

        // MARK: loading

        func loadHome() {
            retry?.invalidate()
            guard let base = ConnectionStore.shared.serverURL, let url = ServerAddress.wallURL(base) else { return }
            Task { @MainActor in
                await prepareCloudflareCookie(base)
                var req = URLRequest(url: url)
                for (k, v) in ConnectionStore.shared.cloudflareHeaders { req.setValue(v, forHTTPHeaderField: k) }
                web?.load(req)
            }
        }

        /// Fetch the hub once with the service token so Cloudflare sets CF_Authorization, then
        /// hand that cookie (and any others for the hub) to the web view.
        @MainActor
        private func prepareCloudflareCookie(_ base: URL) async {
            guard ConnectionStore.shared.hasCloudflareToken, let web else { return }
            let req = NewmanURLSession.request(base, includeToken: false, accept: "text/html")
            _ = try? await NewmanURLSession.shared.data(for: req)
            let cookies = HTTPCookieStorage.shared.cookies(for: base) ?? []
            let store = web.configuration.websiteDataStore.httpCookieStore
            for c in cookies { await store.setCookie(c) }
        }

        private func scheduleRetry() {
            retry?.invalidate()
            retry = Timer.scheduledTimer(withTimeInterval: 30, repeats: false) { [weak self] _ in self?.loadHome() }
        }

        // MARK: WKNavigationDelegate

        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            let req = navigationAction.request
            // Main-frame loads of the hub carry the Cloudflare headers too.
            if navigationAction.targetFrame?.isMainFrame == true, let base = ConnectionStore.shared.serverURL,
               req.url?.host == base.host, ConnectionStore.shared.hasCloudflareToken,
               req.value(forHTTPHeaderField: "CF-Access-Client-Id") == nil, ["http", "https"].contains(req.url?.scheme ?? "") {
                decisionHandler(.cancel)
                var r = req
                for (k, v) in ConnectionStore.shared.cloudflareHeaders { r.setValue(v, forHTTPHeaderField: k) }
                webView.load(r)
                return
            }
            // Links away from the hub (a map, a help page) don't replace the wall.
            if navigationAction.targetFrame?.isMainFrame == true, let host = req.url?.host,
               let base = ConnectionStore.shared.serverURL, host != base.host, navigationAction.navigationType == .linkActivated {
                decisionHandler(.cancel)
                return
            }
            decisionHandler(.allow)
        }

        func webView(_ webView: WKWebView, didReceive challenge: URLAuthenticationChallenge,
                     completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
            if challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodClientCertificate,
               let credential = CertificateStore.shared.credential() {
                completionHandler(.useCredential, credential)
            } else {
                completionHandler(.performDefaultHandling, nil)
            }
        }

        func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
            // A page (re)load: voice is off until the signed-in page turns it on again.
            Task { @MainActor in model.pageStarted() }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            Task { @MainActor in
                BatteryReporter.keepToken(from: webView)  // battery reports on another address of the hub
                model.loadError = nil
            }
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            failed(error)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            failed(error)
        }

        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            webView.reload()
        }

        private func failed(_ error: Error) {
            let ns = error as NSError
            if ns.domain == NSURLErrorDomain && ns.code == NSURLErrorCancelled { return }
            if ns.domain == "WebKitErrorDomain" && ns.code == 102 { return }  // frame load interrupted (our own reload)
            Task { @MainActor in model.loadError = APIError.network(error).message }
            scheduleRetry()
        }

        // MARK: WKUIDelegate (the page's own alert/confirm)

        func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo,
                     completionHandler: @escaping () -> Void) {
            present(message, cancel: false) { _ in completionHandler() }
        }

        func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo,
                     completionHandler: @escaping (Bool) -> Void) {
            present(message, cancel: true, completionHandler)
        }

        private func present(_ message: String, cancel: Bool, _ done: @escaping (Bool) -> Void) {
            guard let vc = web?.window?.rootViewController else { return done(false) }
            let top = vc.presentedViewController ?? vc
            let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            if cancel { alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in done(false) }) }
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in done(true) })
            top.present(alert, animated: true)
        }

        // MARK: bridge

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.frameInfo.isMainFrame else { return }
            let body = message.body
            Task { @MainActor in model.bridge(body) }
        }

        // MARK: gestures

        @objc func cornerHold(_ g: UILongPressGestureRecognizer) {
            guard g.state == .began else { return }
            Task { @MainActor in model.askForSettings() }
        }

        func gestureRecognizer(_ g: UIGestureRecognizer, shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool { true }

        func gestureRecognizerShouldBegin(_ g: UIGestureRecognizer) -> Bool {
            guard g === hold, let view = g.view else { return true }
            let p = g.location(in: view)
            return p.x <= 90 && p.y <= 90
        }
    }
}

/// Hears every touch on the page and lets it through untouched.
final class ActivityRecognizer: UIGestureRecognizer {
    private let onTouch: () -> Void

    init(onTouch: @escaping () -> Void) {
        self.onTouch = onTouch
        super.init(target: nil, action: nil)
        cancelsTouchesInView = false
        delaysTouchesBegan = false
        delaysTouchesEnded = false
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
        onTouch()
        state = .failed
    }
}

/// WKUserContentController keeps its handlers strongly; this breaks the cycle.
final class WeakScriptHandler: NSObject, WKScriptMessageHandler {
    weak var target: WKScriptMessageHandler?

    init(_ target: WKScriptMessageHandler) { self.target = target }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.userContentController(userContentController, didReceive: message)
    }
}
