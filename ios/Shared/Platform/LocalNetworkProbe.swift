import Foundation
import Network

/// iOS 18 only lists an app under Local Network (and asks for permission) after it actually
/// browses with Bonjour. The hub may be on a private address (http://10.1.1.2:8093, or a
/// split-DNS name), so browse briefly at launch, as Optiq does. Needs NSBonjourServices
/// (_http._tcp) and NSLocalNetworkUsageDescription in Info.plist.
enum LocalNetworkProbe {
    private static var browser: NWBrowser?

    static func run() {
        guard browser == nil else { return }
        let b = NWBrowser(for: .bonjour(type: "_http._tcp", domain: nil), using: .tcp)
        b.stateUpdateHandler = { _ in }
        b.browseResultsChangedHandler = { _, _ in }
        b.start(queue: .main)
        browser = b
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
            browser?.cancel()
            browser = nil
        }
    }
}
