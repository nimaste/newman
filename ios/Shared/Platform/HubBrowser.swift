import Combine
import Foundation
import Network

/// Newman hubs on this network: Bonjour `_newman._tcp` (TXT: id, name, v, ext = outside address,
/// path), each resolved to its http://host:port address. Needs `_newman._tcp` in NSBonjourServices.
@MainActor
final class HubBrowser: ObservableObject {
    struct Found: Identifiable, Equatable {
        /// The Bonjour service name.
        var id: String
        var name: String
        var hubId: String?
        var outside: URL?
        var address: URL?

        var candidate: HubCandidate? {
            guard let address else { return nil }
            return HubCandidate(local: address, outside: outside, id: hubId, name: name)
        }
    }

    @Published private(set) var hubs: [Found] = []

    private var browser: NWBrowser?
    private var resolvers: [String: NWConnection] = [:]

    /// Made by a view's @StateObject.
    nonisolated init() {}

    func start() {
        guard browser == nil else { return }
        let b = NWBrowser(for: .bonjourWithTXTRecord(type: "_newman._tcp", domain: nil), using: .tcp)
        b.browseResultsChangedHandler = { [weak self] results, _ in
            MainActor.assumeIsolated { self?.update(results) }
        }
        b.start(queue: .main)
        browser = b
    }

    func stop() {
        browser?.cancel()
        browser = nil
        resolvers.values.forEach { $0.cancel() }
        resolvers = [:]
    }

    private func update(_ results: Set<NWBrowser.Result>) {
        var next: [Found] = []
        for r in results {
            guard case .service(let service, _, _, _) = r.endpoint else { continue }
            var txt: [String: String] = [:]
            if case .bonjour(let record) = r.metadata { txt = record.dictionary }
            var f = hubs.first { $0.id == service } ?? Found(id: service, name: service)
            if let n = txt["name"]?.trimmingCharacters(in: .whitespaces), !n.isEmpty { f.name = n }
            f.hubId = txt["id"].flatMap { $0.isEmpty ? nil : $0 }
            f.outside = txt["ext"].flatMap { ServerAddress.normalize($0) }
            if !next.contains(where: { $0.id == f.id }) { next.append(f) }
            if f.address == nil, resolvers[service] == nil { resolve(service, r.endpoint) }
        }
        hubs = next.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    /// Connects once to learn the service's IPv4 address and port, then hangs up.
    private func resolve(_ service: String, _ endpoint: NWEndpoint) {
        let params = NWParameters.tcp
        if let ip = params.defaultProtocolStack.internetProtocol as? NWProtocolIP.Options { ip.version = .v4 }
        let c = NWConnection(to: endpoint, using: params)
        resolvers[service] = c
        c.stateUpdateHandler = { [weak self, weak c] state in
            MainActor.assumeIsolated {
                guard let self, let c else { return }
                switch state {
                case .ready:
                    if case .hostPort(let host, let port)? = c.currentPath?.remoteEndpoint,
                       let url = ServerAddress.lanURL(host: HubBrowser.text(host), port: Int(port.rawValue)),
                       let i = self.hubs.firstIndex(where: { $0.id == service }) {
                        self.hubs[i].address = url
                    }
                    c.cancel()
                case .waiting, .failed:
                    c.cancel()
                case .cancelled:
                    if self.resolvers[service] === c { self.resolvers[service] = nil }
                default:
                    break
                }
            }
        }
        c.start(queue: .main)
    }

    nonisolated private static func text(_ host: NWEndpoint.Host) -> String {
        switch host {
        case .ipv4(let a): return "\(a)"
        case .ipv6(let a): return "\(a)"
        case .name(let n, _): return n
        @unknown default: return "\(host)"
        }
    }
}
