import AVFoundation
import AVKit
import Combine
import SwiftUI
import UniformTypeIdentifiers

/// A camera full screen (PhoneCamera board): live video (main stream; a refreshing still until it
/// plays, or for cameras without live video), arrows/dots between the cameras, and today's alerts for this camera or all of them.
/// Tapping an alert plays its video (its snapshot while that loads); "Live" goes back to the camera.
struct CameraView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State var target: AppModel.CameraTarget
    @State private var showAll = false
    @State private var alerts: [CameraAlert] = []
    @State private var loading = false
    @State private var error: String?
    @State private var isLive = false

    init(target: AppModel.CameraTarget) {
        _target = State(initialValue: target)
    }

    private var cameras: [Entity] {
        let ids = target.cameraIds.isEmpty ? [target.entityId] : target.cameraIds
        return ids.compactMap { model.entity($0) }.filter { $0.still != nil }
    }

    private var camera: Entity? { model.entity(target.entityId) }

    var body: some View {
        ZStack {
            Theme.cameraBackground.ignoresSafeArea()
            VStack(spacing: 0) {
                topBar
                picture
                dots
                alertList
            }
        }
        .task(id: "\(target.entityId)|\(showAll)") { await loadAlerts() }
        .statusBarHidden(false)
    }

    private var topBar: some View {
        HStack(spacing: 10) {
            if target.alert != nil {
                Button { target.alert = nil } label: { Label("Live", systemImage: "chevron.left") }
                    .buttonStyle(PillButtonStyle())
            } else {
                Button { dismiss() } label: { Label("Back", systemImage: "chevron.left") }
                    .buttonStyle(PillButtonStyle())
            }
            Group {
                Text(target.alert != nil ? "ALERT" : (isLive ? "LIVE" : "STILL"))
                    .font(AppFont.manrope(11, .heavy)).kerning(1)
                    .padding(.horizontal, 7).frame(minHeight: 22)
                    .background(RoundedRectangle(cornerRadius: 6).fill(target.alert == nil ? Theme.red : Theme.amber))
                    .foregroundColor(target.alert == nil ? Theme.text : Theme.amberInk)
                    .accessibilityLabel(target.alert != nil ? "Alert" : (isLive ? "Live" : "Still picture"))
                Text(target.alert?.title ?? camera?.name ?? "Camera")
                    .font(AppFont.manrope(16, .heavy)).foregroundColor(Theme.text).lineLimit(1)
            }
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isHeader)
            Spacer()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    private var picture: some View {
        ZStack {
            LinearGradient(colors: [Color(hex: 0x1F2A47), Color(hex: 0x0C1224), Color(hex: 0x070A16)], startPoint: .topLeading, endPoint: .bottomTrailing)
            if let a = target.alert {
                AlertMedia(alert: a)
                    .id("alert:" + a.id)
            } else {
                Group {
                    if let cam = camera {
                        LiveCameraView(entity: cam, quality: .main, priority: true, stillHeight: 720, contentMode: .fit,
                                       onPlaying: { isLive = $0 }, placeholder: {
                            Text("Loading \(cam.name)…").font(AppFont.manrope(13, .bold)).foregroundColor(Theme.faint)
                        })
                        .id("cam:" + cam.id)
                    }
                }
                // One image element for VoiceOver ("Driveway, live video, camera 2 of 3"); a
                // three-finger swipe changes camera like the drag does.
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(pictureLabel)
                .accessibilityAddTraits(.isImage)
                .accessibilityScrollAction { edge in
                    if edge == .trailing { step(1) } else if edge == .leading { step(-1) }
                }
            }
            if target.alert == nil && cameras.count > 1 {
                HStack {
                    arrow("chevron.left") { step(-1) }
                        .accessibilityLabel("Previous camera")
                    Spacer()
                    arrow("chevron.right") { step(1) }
                        .accessibilityLabel("Next camera")
                }
                .padding(.horizontal, 8)
            }
        }
        .frame(maxWidth: .infinity)
        .aspectRatio(16 / 9, contentMode: .fit)
        // Off while an alert shows, so the video's own scrubbing isn't taken for a camera swipe.
        .gesture(DragGesture(minimumDistance: 30).onEnded { g in
            if target.alert == nil && abs(g.translation.width) > abs(g.translation.height) { step(g.translation.width < 0 ? 1 : -1) }
        }, including: target.alert == nil ? .all : .subviews)
    }

    /// "Driveway, live video, camera 2 of 3" (an alert labels itself: AlertMedia).
    private var pictureLabel: String {
        let name = camera?.name ?? "Camera"
        let i = cameras.firstIndex { $0.id == target.entityId }
        return [name, isLive ? "live video" : "still picture",
                cameras.count > 1 ? i.map { "camera \($0 + 1) of \(cameras.count)" } : nil]
            .compactMap { $0 }.joined(separator: ", ")
    }

    private var dots: some View {
        HStack(spacing: 6) {
            if cameras.count > 1 {
                ForEach(cameras) { c in
                    Capsule().fill(c.id == target.entityId ? Theme.text : Color.white.opacity(0.25))
                        .frame(width: c.id == target.entityId ? 18 : 7, height: 7)
                }
            }
        }
        .frame(height: 27)
        .accessibilityHidden(true) // the picture says "camera 2 of 3"
    }

    private var alertList: some View {
        let todays = alerts.filter { a in
            guard let s = a.start else { return false }
            return Calendar.current.isDateInToday(Date(timeIntervalSince1970: s))
        }
        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                (Text("Today").font(AppFont.manrope(17, .heavy)).foregroundColor(Theme.text)
                    + Text(" · \(todays.count)").font(AppFont.manrope(13, .heavy)).foregroundColor(Theme.muted))
                    .accessibilityLabel("Today, \(todays.count) \(todays.count == 1 ? "alert" : "alerts")")
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                HStack(spacing: 2) {
                    segment("This camera", !showAll) { showAll = false }
                    segment("All", showAll) { showAll = true }
                }
                .padding(3)
                .background(Capsule().fill(Color.white.opacity(0.07)))
            }
            .padding(.horizontal, 16)
            ScrollView {
                LazyVStack(spacing: 8) {
                    if loading && alerts.isEmpty { ProgressView().tint(Theme.text).padding(20) }
                    if let error { Text(error).font(AppFont.manrope(13, .bold)).foregroundColor(Theme.coralText) }
                    if !loading && todays.isEmpty && error == nil {
                        Text(showAll ? "Nothing on any camera today." : "Nothing on this camera today.")
                            .font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted).padding(.vertical, 20)
                    }
                    ForEach(todays) { a in
                        AlertRow(alert: a, highlighted: a.id == target.alert?.id) {
                            target.alert = a
                            if let eid = a.entityId, eid != target.entityId, model.entity(eid) != nil {
                                target.entityId = eid
                                if !target.cameraIds.contains(eid) { target.cameraIds.append(eid) }
                            }
                        }
                        .background(RoundedRectangle(cornerRadius: 16).fill(Color.white.opacity(0.05)))
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
            }
        }
    }

    private func segment(_ title: String, _ on: Bool, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(AppFont.manrope(12, .heavy))
                .padding(.horizontal, 12).frame(minHeight: 34)
                .foregroundColor(on ? Theme.background : Theme.text)
                .background(Capsule().fill(on ? Theme.text : Color.clear))
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    private func arrow(_ symbol: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 20, weight: .bold))
                .frame(width: 48, height: 48)
                .background(Circle().fill(Color(hex: 0x05070F, alpha: 0.55)))
                .foregroundColor(Theme.text)
        }
    }

    private func step(_ d: Int) {
        let cams = cameras
        guard cams.count > 1, let i = cams.firstIndex(where: { $0.id == target.entityId }) else { return }
        target.entityId = cams[(i + d + cams.count) % cams.count].id
    }

    private func loadAlerts() async {
        loading = true
        defer { loading = false }
        do {
            if showAll {
                alerts = try await APIClient.shared.alerts(limit: 60)
            } else if let path = camera?.alerts {
                alerts = try await APIClient.shared.alerts(path: path, limit: 60)
            } else {
                alerts = []
            }
            error = nil
        } catch {
            self.error = (error as? APIError)?.message ?? error.localizedDescription
        }
    }
}

/// An alert in the camera screen: its video, else its snapshot. The video (the hub's api/clips/…,
/// AlertClip) is streamed: AVPlayer asks ClipStreamLoader for byte ranges, which fetches them through
/// the app's mTLS session with the hub's headers (AVPlayer can't present the client certificate
/// itself), so it starts within moments and seeks. A clip still being recorded has no byte ranges
/// on the hub: that one is downloaded whole and played from the file. The snapshot stays up until
/// the video is ready to play.
struct AlertMedia: View {
    @EnvironmentObject var model: AppModel
    let alert: CameraAlert

    @State private var player: AVPlayer?
    @State private var loader: ClipStreamLoader?
    @State private var file: URL?
    @State private var ready = false
    @State private var problem: String?
    @State private var attempt = 0

    private var place: String { alert.cameraName.map { " on \($0)" } ?? "" }

    var body: some View {
        ZStack {
            if let player {
                VideoPlayer(player: player) // AVKit's own controls, VoiceOver included
                    .opacity(ready ? 1 : 0)
                    .accessibilityHidden(!ready)
            }
            if !ready {
                RemoteImage(url: model.url(alert.snapshotUrl ?? alert.thumbUrl), contentMode: .fit) {
                    if AlertClip.path(alert.clip) == nil { ProgressView().tint(Theme.text) } // else the video's spinner
                }
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("Snapshot of \(alert.title ?? "the alert")\(place)")
                    .accessibilityAddTraits(.isImage)
                if AlertClip.path(alert.clip) != nil {
                    if let problem {
                        VStack {
                            Spacer()
                            HStack(spacing: 10) {
                                Text(problem).font(AppFont.manrope(13, .bold)).foregroundColor(Theme.text)
                                Button("Retry") {
                                    stop()
                                    self.problem = nil
                                    attempt += 1
                                }
                                .buttonStyle(PillButtonStyle())
                            }
                            .padding(.horizontal, 12).padding(.vertical, 6)
                            .background(Capsule().fill(Color(hex: 0x05070F, alpha: 0.7)))
                            .padding(.bottom, 10)
                        }
                    } else {
                        ProgressView().tint(Theme.text)
                            .accessibilityLabel("Loading the video")
                    }
                }
            }
        }
        .overlay(alignment: .top) {
            if alert.end == nil && AlertClip.path(alert.clip) != nil {
                Text("Still recording — the video will be complete when it ends.")
                    .font(AppFont.manrope(12, .bold)).foregroundColor(Theme.text)
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(Capsule().fill(Color(hex: 0x05070F, alpha: 0.7)))
                    .padding(.top, 8)
            }
        }
        .task(id: attempt) { await load() }
        .onDisappear { stop() }
    }

    private func stop() {
        player?.pause()
        player = nil
        loader?.cancelAll()
        loader = nil
        ready = false
        if let file { try? FileManager.default.removeItem(at: file) }
        file = nil
    }

    private func load() async {
        guard player == nil, let path = AlertClip.path(alert.clip), let url = model.url(path) else { return }
        // One request decides: a finished clip answers two bytes and its length (206) and is
        // streamed; a still-recording one ignores the range and sends the whole video (200).
        var req = NewmanURLSession.request(url, accept: "video/mp4")
        req.setValue(ClipRange.probe, forHTTPHeaderField: "Range")
        req.timeoutInterval = 60 // the hub fetches the clip from Frigate first
        let item: AVPlayerItem
        do {
            let (tmp, response) = try await NewmanURLSession.shared.download(for: req)
            let http = response as? HTTPURLResponse
            let status = http?.statusCode ?? 0
            switch status {
            case 206:
                try? FileManager.default.removeItem(at: tmp)
                guard let total = ClipRange(contentRange: http?.value(forHTTPHeaderField: "Content-Range"))?.total,
                      let streamer = ClipStreamLoader(source: url, total: total) else {
                    problem = "The video couldn't be loaded."
                    return
                }
                if Task.isCancelled { return }
                loader = streamer
                item = AVPlayerItem(asset: streamer.asset)
            case 200..<300:
                let dest = FileManager.default.temporaryDirectory.appendingPathComponent("alert-\(UUID().uuidString).mp4")
                try FileManager.default.moveItem(at: tmp, to: dest)
                if Task.isCancelled {
                    try? FileManager.default.removeItem(at: dest)
                    return
                }
                file = dest
                item = AVPlayerItem(url: dest)
            default:
                try? FileManager.default.removeItem(at: tmp)
                problem = "The video isn't ready yet."
                return
            }
        } catch {
            if !Task.isCancelled { problem = "The video couldn't be loaded." }
            return
        }
        let p = AVPlayer(playerItem: item)
        player = p
        // The snapshot stays until the video can play; a later failure brings it back with Retry.
        for await status in item.publisher(for: \.status).values {
            if status == .readyToPlay && !ready {
                ready = true
                p.play()
            } else if status == .failed {
                p.pause()
                ready = false
                problem = "The video couldn't be loaded."
                return
            }
        }
    }
}

/// Feeds AVPlayer an alert's video in byte ranges fetched through the app's own URLSession
/// (NewmanURLSession: client certificate, hub headers). The asset gets the clip's address under a
/// made-up scheme, so AVFoundation hands every read to this delegate; each read becomes one Range
/// request whose bytes are passed on as they arrive. Reads and their tasks live on `queue` only.
final class ClipStreamLoader: NSObject, AVAssetResourceLoaderDelegate, URLSessionDataDelegate, @unchecked Sendable {
    static let scheme = "newman-clip"

    let asset: AVURLAsset
    private let source: URL
    private let total: Int64
    private let queue = DispatchQueue(label: "ca.nimaste.newman.clip-stream")
    /// Reads in flight, by URLSession task id.
    private var reads: [Int: Read] = [:]

    private final class Read {
        let request: AVAssetResourceLoadingRequest
        let task: URLSessionDataTask
        let offset: Int64

        init(request: AVAssetResourceLoadingRequest, task: URLSessionDataTask, offset: Int64) {
            self.request = request
            self.task = task
            self.offset = offset
        }
    }

    /// `total`: the clip's length in bytes (from the probe's Content-Range).
    init?(source: URL, total: Int64) {
        guard total > 0, var c = URLComponents(url: source, resolvingAgainstBaseURL: true) else { return nil }
        c.scheme = ClipStreamLoader.scheme
        guard let custom = c.url else { return nil }
        self.source = source
        self.total = total
        asset = AVURLAsset(url: custom)
        super.init()
        asset.resourceLoader.setDelegate(self, queue: queue) // held weakly: AlertMedia keeps this loader
    }

    /// Leaving the video: stop every read.
    func cancelAll() {
        queue.async { [self] in
            for read in reads.values { read.task.cancel() }
            reads.removeAll()
        }
    }

    // MARK: AVAssetResourceLoaderDelegate (on `queue`)

    func resourceLoader(_ resourceLoader: AVAssetResourceLoader,
                        shouldWaitForLoadingOfRequestedResource loadingRequest: AVAssetResourceLoadingRequest) -> Bool {
        if let info = loadingRequest.contentInformationRequest {
            info.contentType = UTType.mpeg4Movie.identifier
            info.contentLength = total
            info.isByteRangeAccessSupported = true
        }
        guard let data = loadingRequest.dataRequest else {
            loadingRequest.finishLoading()
            return true
        }
        let offset = data.currentOffset
        let length = Int64(data.requestedLength) - (offset - data.requestedOffset)
        guard let range = ClipRange.header(offset: offset, length: length,
                                           toEnd: data.requestsAllDataToEndOfResource, total: total) else {
            loadingRequest.finishLoading() // nothing left to send
            return true
        }
        var req = NewmanURLSession.request(source, accept: "video/mp4")
        req.setValue(range, forHTTPHeaderField: "Range")
        let task = NewmanURLSession.shared.dataTask(with: req)
        task.delegate = self // this task's response and bytes come here, ahead of the session's delegate
        reads[task.taskIdentifier] = Read(request: loadingRequest, task: task, offset: offset)
        task.resume()
        return true
    }

    func resourceLoader(_ resourceLoader: AVAssetResourceLoader, didCancel loadingRequest: AVAssetResourceLoadingRequest) {
        for (id, read) in reads where read.request === loadingRequest {
            read.task.cancel()
            reads[id] = nil
        }
    }

    // MARK: URLSessionDataDelegate (the session's queue: hop to `queue`)

    /// The client certificate, exactly as for every other hub request.
    func urlSession(_ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        ClientCertificateDelegate.shared.urlSession(session, task: task, didReceive: challenge, completionHandler: completionHandler)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        let id = dataTask.taskIdentifier
        let http = response as? HTTPURLResponse
        let status = http?.statusCode ?? 0
        let got = ClipRange(contentRange: http?.value(forHTTPHeaderField: "Content-Range"))
        queue.async { [self] in
            // Runs before any of this task's bytes (same serial queue): they must start where the player asked.
            guard let read = reads[id], !(status == 206 && got?.start == read.offset) else { return }
            reads[id] = nil
            read.task.cancel()
            read.request.finishLoading(with: URLError(.badServerResponse))
        }
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        let id = dataTask.taskIdentifier
        queue.async { [self] in
            reads[id]?.request.dataRequest?.respond(with: data)
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let id = task.taskIdentifier
        queue.async { [self] in
            guard let read = reads.removeValue(forKey: id) else { return } // cancelled or refused already
            if let error {
                read.request.finishLoading(with: error)
            } else {
                read.request.finishLoading()
            }
        }
    }
}
