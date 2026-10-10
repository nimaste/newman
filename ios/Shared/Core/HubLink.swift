import Foundation

// Finding and remembering a Newman hub, before anyone signs in (the same contract as the hub and
// the Android app): the `GET /api/hello` reply, the `newman://connect` link / QR code, and which
// of a hub's addresses to use. Pure Foundation, so the Linux checks cover it.

/// GET /api/hello (no sign-in): {"app": "newman", "id", "name", "version", "outsideUrl", "localUrls"}.
struct HubHello: Decodable, Equatable {
    var app: String
    var id: String
    var name: String?
    var version: String?
    var outsideUrl: String?
    var localUrls: [String]?

    /// The reply, when it really is a Newman hub's.
    static func parse(_ data: Data) -> HubHello? {
        guard let h = try? JSONDecoder().decode(HubHello.self, from: data),
              h.app.lowercased() == "newman", !h.id.isEmpty else { return nil }
        return h
    }

    var displayName: String? {
        guard let n = name?.trimmingCharacters(in: .whitespacesAndNewlines), !n.isEmpty else { return nil }
        return n
    }
}

/// The hub this phone uses: its id and name, its address at home and its address from outside.
/// Which one is in use right now is `ConnectionStore.serverURL`.
struct SavedHub: Equatable {
    var id: String
    var name: String
    var localURL: URL?
    var outsideURL: URL?

    /// Home address first.
    var addresses: [URL] {
        var list: [URL] = []
        for u in [localURL, outsideURL].compactMap({ $0 }) where !list.contains(u) { list.append(u) }
        return list
    }
}

/// What the phone knows about a hub before testing it: from Bonjour, a QR code / link, or a typed
/// address (then only one of the two addresses).
struct HubCandidate: Equatable {
    var local: URL?
    var outside: URL?
    var id: String?
    var name: String?

    /// The address shown in the address field.
    var shown: URL? { local ?? outside }

    /// A typed address: a private one is the home address, anything else the outside one.
    static func typed(_ url: URL) -> HubCandidate {
        ServerAddress.isPrivateHost(url.host ?? "") ? HubCandidate(local: url) : HubCandidate(outside: url)
    }
}

/// `newman://connect?v=1&id=<id>&name=<name>&local=<url>&outside=<url or empty>` - the hub's QR
/// code and share link. No secrets in it.
enum ConnectLink {
    static func parse(_ text: String) -> HubCandidate? {
        guard let c = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              c.scheme?.lowercased() == "newman", c.host?.lowercased() == "connect" else { return nil }
        let items = c.queryItems ?? []
        func value(_ name: String) -> String? {
            guard let v = items.first(where: { $0.name == name })?.value?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !v.isEmpty else { return nil }
            return v
        }
        // A form-encoded name may carry "+" for spaces.
        let name = value("name").map { $0.replacingOccurrences(of: "+", with: " ") }
        let link = HubCandidate(local: value("local").flatMap { ServerAddress.normalize($0) },
                                outside: value("outside").flatMap { ServerAddress.normalize($0) },
                                id: value("id"), name: name)
        guard link.local != nil || link.outside != nil else { return nil }
        return link
    }
}

enum HubPlan {
    /// The hub to save after `tested` answered with `hello`: the candidate's addresses first, then
    /// the tested one by kind (private = home), then what the hub says about itself.
    static func saved(tested: URL, hello: HubHello, candidate: HubCandidate) -> SavedHub {
        let testedIsLocal = ServerAddress.isPrivateHost(tested.host ?? "")
        var local = candidate.local ?? (testedIsLocal ? tested : nil)
        var outside = candidate.outside ?? (testedIsLocal ? nil : tested)
        if local == nil { local = (hello.localUrls ?? []).lazy.compactMap { ServerAddress.normalize($0) }.first }
        if outside == nil { outside = hello.outsideUrl.flatMap { ServerAddress.normalize($0) } }
        if outside == local { outside = nil }
        let name = hello.displayName ?? candidate.name ?? tested.host ?? "Newman"
        return SavedHub(id: hello.id, name: name, localURL: local, outsideURL: outside)
    }

    /// The address to use: home when it answered as this hub, else the outside one.
    static func route(_ hub: SavedHub, localAnswers: Bool) -> URL? {
        if localAnswers, let l = hub.localURL { return l }
        return hub.outsideURL ?? hub.localURL
    }

    /// Whether a newly saved hub is the one this phone was already signed in to (else sign out first).
    static func isSameHub(oldHub: SavedHub?, oldAddress: URL?, newHub: SavedHub?, newAddress: URL) -> Bool {
        if let o = oldHub, let n = newHub { return o.id == n.id }
        guard let a = oldAddress else { return true }
        if a == newAddress { return true }
        if let n = newHub, n.addresses.contains(a) { return true }
        if let o = oldHub, o.addresses.contains(newAddress) { return true }
        return false
    }
}

/// Plain-English answers for "Test connection".
enum HubCheck {
    enum Outcome: Equatable {
        case found(HubHello)
        case failed(String)
    }

    static let unreachable = "Can't reach that address."
    static let offline = "This phone is offline."
    static let timedOut = "That address took too long to answer."
    static let needsCertificate = "This address needs a client certificate."
    static let certificateRejected = "This address didn't accept the client certificate."
    static let untrusted = "This address's security certificate isn't trusted."
    static let needsCloudflare = "This address needs a Cloudflare Access token."
    static let cloudflareRejected = "Cloudflare Access didn't accept this token."
    static let hubDown = "That address answered, but the hub behind it isn't responding."
    static let notNewman = "That isn't a Newman hub."
    static let notAddress = "That doesn't look like a web address."

    /// A request that never got an HTTP reply (NSURLErrorDomain code).
    static func network(code: Int, hasCertificate: Bool) -> String {
        switch URLError.Code(rawValue: code) {
        case .notConnectedToInternet: return offline
        case .timedOut: return timedOut
        case .clientCertificateRequired, .clientCertificateRejected:
            return hasCertificate ? certificateRejected : needsCertificate
        // A refused TLS handshake: what an mTLS proxy does to a phone without a certificate.
        case .secureConnectionFailed:
            return hasCertificate ? certificateRejected : needsCertificate
        case .serverCertificateUntrusted, .serverCertificateHasBadDate, .serverCertificateNotYetValid,
             .serverCertificateHasUnknownRoot:
            return untrusted
        default: return unreachable
        }
    }

    /// An HTTP reply to GET /api/hello (`finalHost`: where redirects ended up).
    static func reply(status: Int, finalHost: String?, body: Data, hasCloudflare: Bool, hasCertificate: Bool) -> Outcome {
        if let h = finalHost?.lowercased(), h.hasSuffix("cloudflareaccess.com") {
            return .failed(hasCloudflare ? cloudflareRejected : needsCloudflare)
        }
        if (200..<300).contains(status), let hello = HubHello.parse(body) { return .found(hello) }
        let text = (String(data: body.prefix(4000), encoding: .utf8) ?? "").lowercased()
        if status == 495 || status == 496 || (status == 400 && text.contains("certificate")) {
            return .failed(hasCertificate ? certificateRejected : needsCertificate)
        }
        if (status == 401 || status == 403) && text.contains("cloudflare") {
            return .failed(hasCloudflare ? cloudflareRejected : needsCloudflare)
        }
        if status == 502 || status == 503 || status == 504 || status == 530 { return .failed(hubDown) }
        return .failed(notNewman)
    }
}
