import Foundation
import os
import UserNotifications

/// Runs for Newman pushes with mutable-content (camera snapshots): attaches the snapshot. The hub
/// sends `snapshot_url` (a signed /api/public/snapshots link), fetched through the same mTLS
/// session as the app, with the client certificate and Cloudflare headers read from the Keychain
/// shared with the app (group.ca.nimaste.newman).
///
/// It never touches notification categories (UNUserNotificationCenter): on iOS 26, an extension
/// that registered a push's buttons as its category stopped every later Newman notification from
/// showing until the phone restarted (2026-10-10). The push's aps.category is left as sent; its
/// buttons show when the app has registered that set (PushCategories, from the app only).
///
/// The notification is always handed back: by PushPayload.extensionDeadlineSeconds at the latest,
/// whatever is still running, and at once when iOS says time is up. Each step is logged
/// (subsystem ca.nimaste.newman.NotificationServiceExtension, in Console.app).
final class NotificationService: UNNotificationServiceExtension {
    private let lock = NSLock()
    private var current: Delivery?

    override func didReceive(_ request: UNNotificationRequest, withContentHandler contentHandler: @escaping (UNNotificationContent) -> Void) {
        guard let content = request.content.mutableCopy() as? UNMutableNotificationContent else {
            contentHandler(request.content)
            return
        }
        if let thread = content.userInfo["thread"] as? String, !thread.isEmpty, content.threadIdentifier.isEmpty {
            content.threadIdentifier = thread
        }
        let delivery = Delivery(content: content, handler: contentHandler)
        lock.lock()
        current = delivery
        lock.unlock()
        guard let url = PushPayload.snapshotURL(userInfo: content.userInfo) else {
            delivery.deliver("nothing to attach")
            return
        }
        pushLog.notice("push \(request.identifier, privacy: .public): fetching snapshot")
        // The hard limit: whatever is still running then, the notification goes out.
        DispatchQueue.global(qos: .userInitiated).asyncAfter(deadline: .now() + PushPayload.extensionDeadlineSeconds) {
            delivery.deliver("deadline")
        }
        // The link carries its own token; no Bearer token needed (or sent).
        var req = NewmanURLSession.request(url, includeToken: false, accept: "image/*")
        req.timeoutInterval = PushPayload.snapshotTimeout(elapsed: delivery.elapsed)
        let dataTask = NewmanURLSession.shared.dataTask(with: req) { data, response, error in
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            var attachment: UNNotificationAttachment?
            if let data, (200..<300).contains(status), !data.isEmpty {
                attachment = Self.attachment(data, response: response)
            }
            pushLog.notice("snapshot: status \(status, privacy: .public), attached \(attachment != nil, privacy: .public), error \(error?.localizedDescription ?? "-", privacy: .public)")
            delivery.deliver("snapshot done") { content in
                if let attachment { content.attachments = [attachment] }
            }
        }
        if delivery.track(dataTask) { dataTask.resume() }
    }

    override func serviceExtensionTimeWillExpire() {
        lock.lock()
        let delivery = current
        lock.unlock()
        delivery?.deliver("time expired")
    }

    private static func attachment(_ data: Data, response: URLResponse?) -> UNNotificationAttachment? {
        let mime = (response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Type") ?? "image/jpeg"
        let ext = mime.contains("png") ? "png" : (mime.contains("webp") ? "webp" : "jpg")
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let file = dir.appendingPathComponent("snapshot.\(ext)")
            try data.write(to: file)
            return try UNNotificationAttachment(identifier: "snapshot", url: file, options: nil)
        } catch {
            return nil
        }
    }
}

private let pushLog = Logger(subsystem: "ca.nimaste.newman.NotificationServiceExtension", category: "push")

/// One notification's hand-back to iOS: exactly once, whichever comes first (the snapshot, the
/// deadline, iOS's own time limit). The lock only guards the swap - it is never held while
/// calling out, so no caller can block another.
private final class Delivery: @unchecked Sendable {
    let content: UNMutableNotificationContent
    private let started = Date()
    private let lock = NSLock()
    private var handler: ((UNNotificationContent) -> Void)?
    private var task: URLSessionDataTask?

    init(content: UNMutableNotificationContent, handler: @escaping (UNNotificationContent) -> Void) {
        self.content = content
        self.handler = handler
    }

    var elapsed: Double { Date().timeIntervalSince(started) }

    /// Keeps the snapshot download to cancel when time runs out; false when already delivered.
    func track(_ t: URLSessionDataTask) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard handler != nil else { return false }
        task = t
        return true
    }

    /// Hands the content back unless that already happened; `update` (the snapshot) applies only
    /// if this call is the one that delivers.
    func deliver(_ why: String, update: ((UNMutableNotificationContent) -> Void)? = nil) {
        lock.lock()
        guard let h = handler else {
            lock.unlock()
            return
        }
        handler = nil
        let pending = task
        task = nil
        update?(content)
        lock.unlock()
        pending?.cancel()
        pushLog.notice("delivered (\(why, privacy: .public)) after \(String(format: "%.2f", self.elapsed), privacy: .public) s")
        h(content)
    }
}
