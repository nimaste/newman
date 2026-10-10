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
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodClientCertificate,
              let credential = CertificateStore.shared.credential() else {
            completionHandler(.performDefaultHandling, nil)
            return
        }
        completionHandler(.useCredential, credential)
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
