import Foundation

/// The hub's push channel (GET /api/ws) over the mTLS session, with the Cloudflare and Bearer
/// headers. Reconnects with backoff (1 s -> 30 s) and reports `.reconnected` after a drop so
/// the app reloads everything it might have missed. Pings every 25 s like the web dashboard.
@MainActor
final class LiveConnection {
    enum Status: Equatable { case connecting, open, down }

    var onMessage: ((LiveMessage) -> Void)?
    var onReconnected: (() -> Void)?
    var onStatus: ((Status) -> Void)?

    private var task: URLSessionWebSocketTask?
    private var running = false
    private var everOpen = false
    private var backoff = Backoff()
    private var retry: Task<Void, Never>?
    private var pinger: Task<Void, Never>?
    private var generation = 0
    private(set) var status: Status = .connecting {
        didSet { if status != oldValue { onStatus?(status) } }
    }

    func start() {
        guard !running else { return }
        running = true
        connect()
    }

    func stop() {
        running = false
        everOpen = false
        generation += 1
        retry?.cancel()
        pinger?.cancel()
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        backoff.reset()
        status = .connecting
    }

    /// Reconnect now (the app came back to the foreground with a dead socket).
    func kick() {
        guard running, status != .open else { return }
        retry?.cancel()
        backoff.reset()
        connect()
    }

    private func connect() {
        guard running, let base = ConnectionStore.shared.serverURL, let url = ServerAddress.webSocketURL(base) else { return }
        generation += 1
        let gen = generation
        task?.cancel(with: .goingAway, reason: nil)
        var req = NewmanURLSession.request(url)
        req.timeoutInterval = 20
        let t = NewmanURLSession.shared.webSocketTask(with: req)
        task = t
        status = .connecting
        t.resume()
        receive(t, gen)
    }

    private func receive(_ t: URLSessionWebSocketTask, _ gen: Int) {
        t.receive { [weak self] result in
            Task { @MainActor in
                guard let self, gen == self.generation else { return }
                switch result {
                case .success(let msg):
                    self.opened(t, gen)
                    if case .string(let text) = msg, let m = LiveMessage.parse(text) { self.onMessage?(m) }
                    self.receive(t, gen)
                case .failure:
                    self.dropped(gen)
                }
            }
        }
    }

    /// The first frame (the hub's "hello") proves the socket is open.
    private func opened(_ t: URLSessionWebSocketTask, _ gen: Int) {
        guard status != .open else { return }
        status = .open
        backoff.reset()
        if everOpen { onReconnected?() }
        everOpen = true
        pinger?.cancel()
        pinger = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 25_000_000_000)
                guard let self, !Task.isCancelled, gen == self.generation else { return }
                t.send(.string("ping")) { _ in }
            }
        }
    }

    private func dropped(_ gen: Int) {
        pinger?.cancel()
        status = .down
        guard running else { return }
        let delay = backoff.take()
        retry?.cancel()
        retry = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard let self, !Task.isCancelled, gen == self.generation, self.running else { return }
            self.connect()
        }
    }
}
