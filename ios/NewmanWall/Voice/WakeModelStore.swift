import Foundation
import WebKit

/// The wake-word model, from the hub (GET /api/voice/wakeword, then its three .onnx files), kept
/// in Application Support/voice/<version> (Android's WakeModelStore.kt): a retrained model, another
/// wake word or a new sensitivity on the hub is picked up without a new app; only the current
/// version is kept. Asks with the wall page's session (its cookies from the web view's store, as
/// BatteryReporter does) through NewmanURLSession (client certificate, Cloudflare headers).
@MainActor
final class WakeModelStore {
    enum Failure: Error { case noHub, http(Int, String), notKept }

    private var root: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent("voice", isDirectory: true)
    }

    /// The current model's folder (downloading it first if needed) and its config; nil when the hub has none.
    func fetch() async throws -> (URL, WakeConfig)? {
        guard let base = ConnectionStore.shared.serverURL, let url = ServerAddress.url(base, WakeConfig.path) else { throw Failure.noHub }
        let (data, response) = try await NewmanURLSession.shared.data(for: await request(url))
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        if code == 404 { return nil }
        guard (200..<300).contains(code) else { throw Failure.http(code, WakeConfig.path) }
        guard let cfg = WakeConfig.parse(data), cfg.complete else { return nil }

        let fm = FileManager.default
        let dir = root.appendingPathComponent(WakeConfig.directoryName(cfg.version), isDirectory: true)
        let have = WakeConfig.files.allSatisfy { name in
            let size = (try? fm.attributesOfItem(atPath: dir.appendingPathComponent("\(name).onnx").path)[.size] as? NSNumber)?.intValue ?? 0
            return size > 0
        }
        if !have {
            let tmp = root.appendingPathComponent("download", isDirectory: true)
            try? fm.removeItem(at: tmp)
            try fm.createDirectory(at: tmp, withIntermediateDirectories: true)
            for name in WakeConfig.files {
                guard let path = cfg.files[name], let fileURL = ServerAddress.url(base, path) else { throw Failure.noHub }
                let req = await request(fileURL, accept: "application/octet-stream")
                let (location, r) = try await NewmanURLSession.shared.download(for: req)
                let status = (r as? HTTPURLResponse)?.statusCode ?? 0
                guard (200..<300).contains(status) else {
                    try? fm.removeItem(at: location)
                    throw Failure.http(status, name)
                }
                try fm.moveItem(at: location, to: tmp.appendingPathComponent("\(name).onnx"))
            }
            try? fm.removeItem(at: dir)
            do {
                try fm.moveItem(at: tmp, to: dir)
            } catch {
                throw Failure.notKept
            }
            var values = URLResourceValues()
            values.isExcludedFromBackup = true // the hub has it
            var kept = dir
            try? kept.setResourceValues(values)
        }
        // Only the current version is kept.
        for other in (try? fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)) ?? []
            where other.lastPathComponent != dir.lastPathComponent {
            try? fm.removeItem(at: other)
        }
        return (dir, cfg)
    }

    /// A request with the page's cookies for the hub (not a native token).
    private func request(_ url: URL, accept: String = "application/json") async -> URLRequest {
        var req = NewmanURLSession.request(url, includeToken: false, accept: accept)
        req.httpShouldHandleCookies = false
        guard let host = url.host else { return req }
        let https = url.scheme?.lowercased() == "https"
        let all = await WKWebsiteDataStore.default().httpCookieStore.allCookies()
        let cookies = all.filter { c in
            TabletBattery.cookieDomainMatches(c.domain, host: host)
                && (!c.isSecure || https)
                && (c.expiresDate.map { $0 > Date() } ?? true)
        }
        for (k, v) in HTTPCookie.requestHeaderFields(with: cookies) { req.setValue(v, forHTTPHeaderField: k) }
        return req
    }
}
