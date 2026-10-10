import Foundation

/// GET /api/hello (no sign-in) through the mTLS session with the Cloudflare headers: tests an
/// address on the Server screen and tells the app which of the hub's addresses answers.
enum HubProbe {
    static func hello(_ base: URL, timeout: TimeInterval = 10) async -> HubCheck.Outcome {
        guard let url = ServerAddress.url(base, "api/hello") else { return .failed(HubCheck.notAddress) }
        let hasCertificate = CertificateStore.shared.isConfigured
        do {
            let (status, finalHost, data) = try await get(url, timeout: timeout)
            let outcome = HubCheck.reply(status: status, finalHost: finalHost, body: data,
                                         hasCloudflare: ConnectionStore.shared.hasCloudflareToken,
                                         hasCertificate: hasCertificate)
            if status == 404, case .failed = outcome, await isOlderHub(base, timeout: timeout) {
                return .failed(olderHub)
            }
            return outcome
        } catch {
            let ns = error as NSError
            guard ns.domain == NSURLErrorDomain else { return .failed(HubCheck.unreachable) }
            return .failed(HubCheck.network(code: ns.code, hasCertificate: hasCertificate))
        }
    }

    /// Marks a hub from before /api/hello (it has no id to remember); the Server screen saves
    /// just its address, as older versions of the app did.
    static let olderHub = "older-newman-hub"

    /// Whether `base` answers as the hub `id` within a short time (choosing the home address).
    static func answers(_ base: URL, id: String, timeout: TimeInterval = 2.5) async -> Bool {
        if case .found(let h) = await hello(base, timeout: timeout) { return h.id == id }
        return false
    }

    /// A hub without /api/hello still answers the open GET /api/auth/state.
    private static func isOlderHub(_ base: URL, timeout: TimeInterval) async -> Bool {
        guard let url = ServerAddress.url(base, "api/auth/state"),
              let reply = try? await get(url, timeout: timeout),
              (200..<300).contains(reply.0),
              let state = try? JSONDecoder().decode(AuthState.self, from: reply.2) else { return false }
        return state.signedIn != nil || state.where != nil || state.setupNeeded != nil
    }

    private static func get(_ url: URL, timeout: TimeInterval) async throws -> (Int, String?, Data) {
        var req = NewmanURLSession.request(url, includeToken: false)
        req.timeoutInterval = timeout
        let (data, resp) = try await NewmanURLSession.shared.data(for: req)
        let http = resp as? HTTPURLResponse
        return (http?.statusCode ?? 0, http?.url?.host ?? resp.url?.host, data)
    }
}

/// Testing a hub on the Server screen: the home address first (briefly), then the outside one.
enum HubConnect {
    enum TestResult {
        /// `hub` is nil for a hub from before /api/hello (only its address is kept).
        case ok(hub: SavedHub?, active: URL, name: String)
        case failed(String)
    }

    static func test(_ candidate: HubCandidate) async -> TestResult {
        var lastError: String?
        var tries: [(URL, TimeInterval)] = []
        if let l = candidate.local { tries.append((l, 5)) }
        if let o = candidate.outside, o != candidate.local { tries.append((o, 15)) }
        for (url, timeout) in tries {
            switch await HubProbe.hello(url, timeout: timeout) {
            case .found(let hello):
                if let want = candidate.id, want != hello.id {
                    lastError = "That address answers as a different Newman hub."
                    continue
                }
                let hub = HubPlan.saved(tested: url, hello: hello, candidate: candidate)
                return .ok(hub: hub, active: url, name: hub.name)
            case .failed(let message):
                if message == HubProbe.olderHub {
                    return .ok(hub: nil, active: url, name: candidate.name ?? url.host ?? "Newman")
                }
                lastError = message
            }
        }
        return .failed(lastError ?? HubCheck.unreachable)
    }
}
