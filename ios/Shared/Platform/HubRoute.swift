import Foundation
import Network

extension Notification.Name {
    /// The app switched between the hub's home and outside addresses (the network changed).
    static let newmanServerChanged = Notification.Name("ca.nimaste.newman.serverChanged")
}

/// Which of the hub's addresses the PHONE uses (the wall tablet never calls this - it stays on
/// the address chosen on its Server screen and keeps retrying it). The rules are
/// `HubRoutePolicy`: a hub restart doesn't move the phone outside; home -> outside only after
/// home failed for 2 minutes (or the network changed) and the outside address answers as the same
/// hub; while outside, home is probed every 30 s and used again as soon as it answers.
/// Checked at launch, when the app comes back to the foreground, when the network changes
/// (NWPathMonitor) and, while home is failing or the phone is outside, on a timer while the app
/// is active. The choice is written to `ConnectionStore.serverURL`, which everything (and the
/// extensions) reads.
///
/// Installs from before the Server screen saved only an address: that address is kept as it is,
/// and the hub's id and other address are learnt from it in the background.
@MainActor
final class HubRoute {
    static let shared = HubRoute()

    private static let homeNetworkKey = "hubRoute.homeNetwork"

    private var monitor: NWPathMonitor?
    private var lastPath: String?
    private var pending: Task<Void, Never>?
    private var checking: Task<Bool, Never>?
    private var timer: Task<Void, Never>?
    private var active = false
    private var learning = false
    private var policy: HubRoutePolicy?
    private var policyHub: SavedHub?

    func startMonitoring() {
        guard monitor == nil else { return }
        let m = NWPathMonitor()
        m.pathUpdateHandler = { path in
            let up = path.status == .satisfied
            // The preferred interface and the gateways: a different Wi-Fi network is a change too.
            let summary = up ? (path.availableInterfaces.prefix(1).map { "\($0.type)-\($0.name)" } + path.gateways.map { "\($0)" })
                .joined(separator: ",") : "down"
            MainActor.assumeIsolated { HubRoute.shared.pathChanged(up: up, summary: summary) }
        }
        m.start(queue: .main)
        monitor = m
    }

    /// The app is in the foreground (re-probes run only then) or not.
    func setActive(_ on: Bool) {
        active = on
        if on { schedule() } else { timer?.cancel(); timer = nil }
    }

    private func pathChanged(up: Bool, summary: String) {
        let first = lastPath == nil
        defer { lastPath = summary }
        // The first report is the network at launch (which also covers launches in the background
        // for location events); after that, only real changes.
        guard up, first || lastPath != summary else { return }
        if !first { policy?.pathChanged() }
        pending?.cancel()
        pending = Task {
            if !first { try? await Task.sleep(nanoseconds: 1_500_000_000) }
            guard !Task.isCancelled else { return }
            if await self.check() { NotificationCenter.default.post(name: .newmanServerChanged, object: nil) }
        }
    }

    /// Picks the address to use now; true when it changed.
    @discardableResult
    func check() async -> Bool {
        if let running = checking { return await running.value }
        let t = Task { await self.pick() }
        checking = t
        let changed = await t.value
        checking = nil
        schedule()
        return changed
    }

    private func pick() async -> Bool {
        let store = ConnectionStore.shared
        guard let hub = store.hub else {
            learn()
            return false
        }
        guard let local = hub.localURL, let outside = hub.outsideURL, local != outside else {
            // One address: nothing to choose.
            policy = nil
            guard let only = hub.localURL ?? hub.outsideURL, store.serverURL != only else { return false }
            store.serverURL = only
            return true
        }
        var changed = false
        if policy == nil || policyHub != hub {
            // Start from the address in use (the one chosen on the Server screen, or last time).
            if store.serverURL != local && store.serverURL != outside {
                store.serverURL = local
                changed = true
            }
            policy = HubRoutePolicy(using: store.serverURL == outside ? .outside : .home,
                                    homeNetwork: UserDefaults.standard.string(forKey: Self.homeNetworkKey))
            policyHub = hub
        }
        // The Server screen may have picked the other address since.
        let inUse: HubRoutePolicy.Using = store.serverURL == outside ? .outside : .home
        if policy?.using != inUse { policy?.using = inUse }
        let answered = await HubProbe.answers(local, id: hub.id)
        let network = lastPath
        switch policy?.home(answered: answered, now: Date().timeIntervalSince1970, network: network) ?? .stay {
        case .useHome:
            store.serverURL = local
            changed = true
        case .probeOutside:
            if await HubProbe.answers(outside, id: hub.id, timeout: 8), policy?.outside(sameHub: true) == true {
                store.serverURL = outside
                changed = true
            }
        case .stay:
            break
        }
        if answered, let n = policy?.homeNetwork { UserDefaults.standard.set(n, forKey: Self.homeNetworkKey) }
        return changed
    }

    /// While the app is active: the next probe the policy asks for (home failing, or outside).
    private func schedule() {
        timer?.cancel()
        timer = nil
        guard active, let delay = policy?.nextProbe else { return }
        timer = Task {
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard !Task.isCancelled else { return }
            if await self.check() { NotificationCenter.default.post(name: .newmanServerChanged, object: nil) }
        }
    }

    /// An address saved by an older version: ask it once for the hub's id, name and other address.
    private func learn() {
        guard !learning, let saved = ConnectionStore.shared.serverURL else { return }
        learning = true
        Task {
            defer { self.learning = false }
            guard case .found(let hello) = await HubProbe.hello(saved),
                  ConnectionStore.shared.hub == nil, ConnectionStore.shared.serverURL == saved else { return }
            ConnectionStore.shared.hub = HubPlan.saved(tested: saved, hello: hello, candidate: .typed(saved))
        }
    }
}
