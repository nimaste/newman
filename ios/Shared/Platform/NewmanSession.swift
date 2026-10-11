import Foundation

/// Answers the TLS client-certificate challenge with the stored certificate (GChat's
/// MTLSPinningDelegate). A no-op when there is none - at home the proxy doesn't ask.
/// Also used as the WebSocket task's delegate so it sees open/close events.
final class ClientCertificateDelegate: NSObject, URLSessionDelegate, URLSessionTaskDelegate, URLSessionWebSocketDelegate {
    static let shared = ClientCertificateDelegate()

    /// Set by the live connection to hear when its socket opens or closes.
    var onSocketOpen: ((URLSessionWebSocketTask) -> Void)?
    var onSocketClose: ((URLSessionWebSocketTask) -> Void)?

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        handle(challenge, completionHandler)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        handle(challenge, completionHandler)
    }

    private func handle(_ challenge: URLAuthenticationChallenge,
                        _ completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        ClientCertificateDelegate.answer(challenge, completionHandler)
    }

    /// What answering a challenge did (press reports): a client-certificate challenge with or
    /// without a stored certificate, or another kind (answered by iOS's default handling).
    enum Seen {
        case clientCertificate(available: Bool)
        case other(String)
    }

    /// Answers one challenge the way every hub request does.
    @discardableResult
    static func answer(_ challenge: URLAuthenticationChallenge,
                       _ completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) -> Seen {
        let method = challenge.protectionSpace.authenticationMethod
        guard method == NSURLAuthenticationMethodClientCertificate else {
            completionHandler(.performDefaultHandling, nil)
            return .other(method)
        }
        guard let credential = CertificateStore.shared.credential() else {
            completionHandler(.performDefaultHandling, nil)
            return .clientCertificate(available: false)
        }
        completionHandler(.useCredential, credential)
        return .clientCertificate(available: true)
    }

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask, didOpenWithProtocol protocol: String?) {
        onSocketOpen?(webSocketTask)
    }

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                    didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?) {
        onSocketClose?(webSocketTask)
    }
}

/// The one URLSession for everything that talks to the hub - API, live WebSocket, pictures,
/// notification images - so the client certificate is presented on every connection.
enum NewmanURLSession {
    static let shared: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 20
        config.waitsForConnectivity = false
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.httpCookieStorage = HTTPCookieStorage.shared  // Cloudflare's CF_Authorization cookie
        return URLSession(configuration: config, delegate: ClientCertificateDelegate.shared, delegateQueue: nil)
    }()

    /// A request to the hub with its headers (Cloudflare Access, Bearer token).
    static func request(_ url: URL, method: String = "GET", body: Data? = nil, includeToken: Bool = true,
                        accept: String = "application/json") -> URLRequest {
        var r = URLRequest(url: url)
        r.httpMethod = method
        r.setValue(accept, forHTTPHeaderField: "Accept")
        for (k, v) in ConnectionStore.shared.headers(includeToken: includeToken) { r.setValue(v, forHTTPHeaderField: k) }
        if let body {
            r.httpBody = body
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return r
    }
}

/// One try at sending a notification button, on its own short-lived ephemeral session - as the
/// Home Assistant app sends its button webhooks: a fresh connection that can't wait behind (or
/// reuse a dead one of) the shared session's live sockets, video and pictures. Same client
/// certificate and headers. Records the challenges and URLSessionTaskMetrics for the press report.
final class PressProbe: NSObject, URLSessionTaskDelegate {
    struct Outcome {
        var data: Data?
        var status: Int?
        var error: Error?
        var attempt: PressAttempt
    }

    private let lock = NSLock()
    private var certChallenges = 0
    private var certAvailable: Bool?
    private var other: [String] = []
    private var metrics: PressMetrics?

    /// `request` must carry its timeoutInterval (the time this try is allowed).
    static func send(_ request: URLRequest, appState: String, backgroundLeft: Double?) async -> Outcome {
        let probe = PressProbe()
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = request.timeoutInterval
        config.timeoutIntervalForResource = request.timeoutInterval + 1
        config.waitsForConnectivity = false
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.urlCache = nil
        config.httpCookieStorage = HTTPCookieStorage.shared // Cloudflare's CF_Authorization cookie, as the shared session
        let session = URLSession(configuration: config, delegate: probe, delegateQueue: nil)
        let started = Date()
        var out = Outcome(attempt: PressAttempt(at: started.timeIntervalSince1970,
                                                url: request.url.map { PressReport.redact($0) } ?? "?",
                                                appState: appState, backgroundLeft: backgroundLeft,
                                                timeoutSeconds: request.timeoutInterval, ms: 0))
        do {
            let (data, resp) = try await session.data(for: request)
            out.data = data
            out.status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        } catch {
            out.error = error
            out.attempt.error = PressError.of(error)
        }
        out.attempt.ms = Int((Date().timeIntervalSince(started) * 1000).rounded())
        out.attempt.status = out.status
        // The metrics arrive on the delegate queue, usually just before the task completes.
        var waited = 0
        while probe.collected() == nil && waited < 20 {
            try? await Task.sleep(nanoseconds: 25_000_000)
            waited += 1
        }
        probe.fill(&out.attempt)
        session.finishTasksAndInvalidate()
        return out
    }

    private func collected() -> PressMetrics? {
        lock.lock()
        defer { lock.unlock() }
        return metrics
    }

    private func fill(_ a: inout PressAttempt) {
        lock.lock()
        defer { lock.unlock() }
        a.certChallenges = certChallenges
        a.certAvailable = certAvailable
        a.otherChallenges = other.isEmpty ? nil : other
        a.metrics = metrics
    }

    private func note(_ seen: ClientCertificateDelegate.Seen) {
        lock.lock()
        defer { lock.unlock() }
        switch seen {
        case .clientCertificate(let available):
            certChallenges += 1
            certAvailable = available
        case .other(let method):
            other.append(method.replacingOccurrences(of: "NSURLAuthenticationMethod", with: ""))
        }
    }

    // Client-certificate and server-trust challenges come here (session level); others task level.
    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        note(ClientCertificateDelegate.answer(challenge, completionHandler))
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        note(ClientCertificateDelegate.answer(challenge, completionHandler))
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didFinishCollecting metrics: URLSessionTaskMetrics) {
        let flat = PressProbe.flatten(metrics)
        lock.lock()
        defer { lock.unlock() }
        self.metrics = flat
    }

    static func flatten(_ m: URLSessionTaskMetrics) -> PressMetrics {
        var out = PressMetrics(transactions: m.transactionMetrics.count)
        guard let t = m.transactionMetrics.last else { return out }
        let start = t.fetchStartDate
        out.fetchStartAt = start?.timeIntervalSince1970
        out.dnsStart = PressMetrics.offset(t.domainLookupStartDate, from: start)
        out.dnsEnd = PressMetrics.offset(t.domainLookupEndDate, from: start)
        out.connectStart = PressMetrics.offset(t.connectStartDate, from: start)
        out.connectEnd = PressMetrics.offset(t.connectEndDate, from: start)
        out.secureStart = PressMetrics.offset(t.secureConnectionStartDate, from: start)
        out.secureEnd = PressMetrics.offset(t.secureConnectionEndDate, from: start)
        out.requestStart = PressMetrics.offset(t.requestStartDate, from: start)
        out.requestEnd = PressMetrics.offset(t.requestEndDate, from: start)
        out.responseStart = PressMetrics.offset(t.responseStartDate, from: start)
        out.responseEnd = PressMetrics.offset(t.responseEndDate, from: start)
        out.remoteAddress = t.remoteAddress
        out.remotePort = t.remotePort
        out.localAddress = t.localAddress
        out.networkProtocol = t.networkProtocolName
        out.reusedConnection = t.isReusedConnection
        out.proxyConnection = t.isProxyConnection
        out.cellular = t.isCellular
        out.expensive = t.isExpensive
        out.constrained = t.isConstrained
        return out
    }
}
