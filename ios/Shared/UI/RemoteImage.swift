import SwiftUI
import UIKit

/// Loads hub pictures through the mTLS session with the hub's headers. (AsyncImage would use
/// URLSession.shared, which can't present the client certificate.)
final class ImageLoader {
    static let shared = ImageLoader()
    private let cache = NSCache<NSURL, UIImage>()

    init() { cache.countLimit = 150 }

    func cached(_ url: URL) -> UIImage? { cache.object(forKey: url as NSURL) }

    func load(_ url: URL, useCache: Bool = true) async throws -> UIImage {
        if useCache, let img = cache.object(forKey: url as NSURL) { return img }
        let req = NewmanURLSession.request(url, accept: "image/*")
        let pair: (Data, URLResponse)
        do {
            pair = try await NewmanURLSession.shared.data(for: req)
        } catch {
            throw APIError.network(error)
        }
        let (data, resp) = pair
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            throw APIError(status: status, message: JSONBody.errorText(data) ?? "No picture (\(status))")
        }
        guard let img = UIImage(data: data) else { throw APIError(status: status, message: "Not a picture") }
        if useCache { cache.setObject(img, forKey: url as NSURL) }
        return img
    }
}

/// A hub picture. With `refresh` it reloads every few seconds while on screen (camera stills),
/// keeping the last good frame up while the next one loads.
struct RemoteImage<Placeholder: View>: View {
    var url: URL?
    var refresh: TimeInterval? = nil
    var contentMode: ContentMode = .fill
    @ViewBuilder var placeholder: () -> Placeholder

    @State private var image: UIImage?
    @State private var failed = false

    var body: some View {
        ZStack {
            if let image {
                Image(uiImage: image).resizable().aspectRatio(contentMode: contentMode)
            } else {
                placeholder()
            }
        }
        // Restart when the picture or the refresh changes (live video playing stops the refresh).
        .task(id: TaskKey(url: url, refresh: refresh)) { await run() }
    }

    private func run() async {
        guard let url else { return }
        if image == nil, refresh == nil, let c = ImageLoader.shared.cached(url) { image = c }
        repeat {
            let target = refresh == nil ? url : ServerAddress.with(url, "_", String(Int(Date().timeIntervalSince1970 * 1000)))
            if let img = try? await ImageLoader.shared.load(target, useCache: refresh == nil) {
                image = img
                failed = false
            } else {
                failed = true
            }
            guard let r = refresh else { return }
            try? await Task.sleep(nanoseconds: UInt64(r * 1_000_000_000))
        } while !Task.isCancelled
    }
}

private struct TaskKey: Equatable {
    var url: URL?
    var refresh: TimeInterval?
}

extension RemoteImage where Placeholder == Color {
    init(url: URL?, refresh: TimeInterval? = nil, contentMode: ContentMode = .fill) {
        self.init(url: url, refresh: refresh, contentMode: contentMode, placeholder: { Color.white.opacity(0.05) })
    }
}
