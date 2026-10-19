import SwiftUI

/// The screens under the Cameras tab.
enum CamerasRoute: Hashable {
    case camera(String)
}

enum CameraList {
    /// Every camera of the house; a zone (part of another camera's picture, attr "camera") isn't one.
    static func all(_ entities: [Entity], kinds: [String: Kind]) -> [Entity] {
        entities.filter { kinds[$0.kind]?.hasCamera == true && $0.attr("camera") == nil }
    }
}

/// The Cameras tab: the big picture - "Latest activity" or "Featured camera", exactly as chosen in
/// Settings (it never switches between them by itself; admins change it here) - then every camera.
struct CamerasTab: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        NavigationStack(path: $model.camerasPath) {
            CamerasScreen()
                .toolbar(.hidden, for: .navigationBar)
                .navigationDestination(for: CamerasRoute.self) { route in
                    Group {
                        switch route {
                        case .camera(let id): CameraDetailScreen(cameraId: id)
                        }
                    }
                    .navigationTitle("")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbarBackground(.hidden, for: .navigationBar)
                    .toolbarColorScheme(.dark, for: .navigationBar)
                }
        }
    }
}

struct CamerasScreen: View {
    @EnvironmentObject var model: AppModel
    @State private var choosing = false

    var body: some View {
        let cams = CameraList.all(model.allEntities, kinds: model.kinds)
        let setting = model.home?.cameras ?? HomeData.Cameras()
        let big = setting.bigCamera.flatMap { model.entity($0) }
        ZStack {
            NightGlassBackground(teal: false)
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(alignment: .firstTextBaseline) {
                        Text("Cameras").font(AppFont.sora(30)).foregroundColor(Theme.text)
                            .accessibilityAddTraits(.isHeader)
                        Spacer()
                        Text(cams.count == 1 ? "1 camera" : "\(cams.count) cameras")
                            .font(AppFont.manrope(13, .heavy)).foregroundColor(Theme.muted)
                    }
                    .padding(.top, 12)
                    CardHeader(text: setting.label) {
                        if model.isAdmin {
                            Button("Change") { choosing = true }
                                .font(AppFont.manrope(13, .heavy)).foregroundColor(Theme.blue)
                                .accessibilityHint("Latest activity or a featured camera")
                        }
                    }
                    if let big {
                        Button { model.camerasPath.append(.camera(big.id)) } label: {
                            ZStack(alignment: .bottomLeading) {
                                LiveCameraView(entity: big, quality: .sub, stillHeight: 480, contentMode: .fill) {
                                    Theme.cameraBackground
                                }
                                .frame(height: 200)
                                .frame(maxWidth: .infinity)
                                .clipped()
                                LinearGradient(colors: [Color.black.opacity(0), Color.black.opacity(0.65)], startPoint: .center, endPoint: .bottom)
                                    .allowsHitTesting(false)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(big.name).font(AppFont.manrope(16, .heavy)).foregroundColor(.white)
                                    if !setting.isFeatured, let line = CamerasScreen.topLine(setting.top) {
                                        Text(line).font(AppFont.manrope(12, .bold)).foregroundColor(Color.white.opacity(0.85))
                                    }
                                }
                                .padding(12)
                            }
                            .frame(height: 200)
                            .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
                            .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(Theme.cardBorder))
                            .overlay(alignment: .topLeading) { liveBadge.padding(10) }
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("\(setting.label): \(big.name)")
                    }
                    LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 12) {
                        ForEach(cams) { c in tile(c) }
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 32)
            }
            .refreshable {
                await model.reloadAll()
            }
        }
        .sheet(isPresented: $choosing) {
            CameraModeSheet(cameras: cams, mode: setting.isFeatured ? "featured" : "latest", featured: setting.featured)
                .environmentObject(model)
        }
    }

    private var liveBadge: some View {
        HStack(spacing: 5) {
            Circle().fill(Theme.red).frame(width: 7, height: 7)
            Text("LIVE").font(AppFont.manrope(11, .heavy)).kerning(0.6)
        }
        .foregroundColor(.white)
        .padding(.horizontal, 9)
        .frame(height: 24)
        .background(Capsule().fill(Color.black.opacity(0.55)))
        .accessibilityHidden(true)
    }

    private func tile(_ c: Entity) -> some View {
        Button { model.camerasPath.append(.camera(c.id)) } label: {
            VStack(alignment: .leading, spacing: 6) {
                RemoteImage(url: model.stillURL(c, height: 300), refresh: 10) { Theme.cameraBackground }
                    .frame(height: 96)
                    .frame(maxWidth: .infinity)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Theme.cardBorder))
                Text(c.name).font(AppFont.manrope(13, .heavy)).foregroundColor(Theme.text).lineLimit(1)
                    .padding(.horizontal, 2)
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(c.name)
        .accessibilityHint("Live, and what it saw")
    }

    /// "Person at Garage Walkway · 8:36 AM".
    static func topLine(_ top: HomeData.TopCamera?) -> String? {
        guard let top else { return nil }
        var what = top.label ?? ""
        if let z = top.zone, !z.isEmpty { what += what.isEmpty ? z : " at \(z)" }
        let when = top.at.map { HubDates.time(Date(timeIntervalSince1970: $0)) }
        let parts = [what, when ?? ""].filter { !$0.isEmpty }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

/// Admins: what the Cameras tab's big picture shows (PATCH /api/settings/house {"cameras"}).
struct CameraModeSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let cameras: [Entity]
    @State var mode: String
    @State var featured: String?
    @State private var saving = false

    var body: some View {
        NavigationStack {
            List {
                Section {
                    choice("Latest activity", "The camera that saw something last", on: mode == "latest") { mode = "latest" }
                    choice("Featured camera", "Always the same camera", on: mode == "featured") { mode = "featured" }
                }
                .listRowBackground(Theme.card)
                if mode == "featured" {
                    Section {
                        ForEach(cameras) { c in
                            choice(c.name, nil, on: featured == c.id) { featured = c.id }
                        }
                    } header: {
                        Text("WHICH CAMERA").font(AppFont.manrope(12, .heavy)).kerning(1.2).foregroundColor(Theme.muted)
                    }
                    .listRowBackground(Theme.card)
                }
            }
            .scrollContentBackground(.hidden)
            .background(Theme.background)
            .navigationTitle("Big picture")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        saving = true
                        Task {
                            if await model.setCameraMode(mode, featured: mode == "featured" ? featured : nil) { dismiss() }
                            saving = false
                        }
                    }
                    .disabled(saving || (mode == "featured" && featured == nil))
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private func choice(_ title: String, _ detail: String?, on: Bool, _ pick: @escaping () -> Void) -> some View {
        Button(action: pick) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                    if let detail { Text(detail).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted) }
                }
                Spacer()
                if on { Image(systemName: "checkmark").font(.system(size: 15, weight: .bold)).foregroundColor(Theme.blue) }
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

/// One camera: live, then what it saw (GET /api/alerts?entity=) from today, a chip per label;
/// "Earlier days" reaches back a day at a time; tapping one plays its clip.
struct CameraDetailScreen: View {
    @EnvironmentObject var model: AppModel
    let cameraId: String
    @State private var detections: [Detection]?
    @State private var filter: String?
    @State private var daysBack = 0
    @State private var loadingMore = false

    var body: some View {
        let list = detections ?? []
        let chips = DetectionLogic.chips(list)
        let shown = filter.map { f in list.filter { $0.label == f } } ?? list
        let grouped = Dictionary(grouping: shown) { HubDates.dayKey(Date(timeIntervalSince1970: $0.at ?? 0)) }
        let days = grouped.keys.sorted(by: >)
        ZStack {
            NightGlassBackground(teal: false)
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if let cam = model.entity(cameraId) {
                        Text(cam.name).font(AppFont.sora(28)).foregroundColor(Theme.text)
                            .accessibilityAddTraits(.isHeader)
                        Button { model.openCamera = AppModel.CameraTarget(entityId: cam.id, cameraIds: [cam.id]) } label: {
                            LiveCameraView(entity: cam, quality: .sub, stillHeight: 480, contentMode: .fill) { Theme.cameraBackground }
                                .frame(height: 210)
                                .frame(maxWidth: .infinity)
                                .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
                                .overlay(alignment: .bottomTrailing) {
                                    Image(systemName: "arrow.up.left.and.arrow.down.right")
                                        .font(.system(size: 14, weight: .bold)).foregroundColor(.white)
                                        .frame(width: 36, height: 36)
                                        .background(Circle().fill(Color.black.opacity(0.55)))
                                        .padding(10)
                                }
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("\(cam.name), live")
                        .accessibilityHint("Full screen")
                    }
                    if !chips.isEmpty {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 6) {
                                Button("All \(list.count)") { filter = nil }
                                    .buttonStyle(PillButtonStyle(selected: filter == nil, height: 34))
                                ForEach(chips, id: \.label) { c in
                                    Button("\(c.label) \(c.count)") { filter = c.label }
                                        .buttonStyle(PillButtonStyle(selected: filter == c.label, height: 34))
                                }
                            }
                        }
                    }
                    VStack(alignment: .leading, spacing: 0) {
                        if detections == nil {
                            ProgressView().tint(Theme.text).frame(maxWidth: .infinity).padding(.vertical, 12)
                        } else if shown.isEmpty {
                            CardHeader(text: "Today").padding(.bottom, 4)
                            Text("Nothing yet.").font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted).padding(.vertical, 8)
                        }
                        ForEach(days, id: \.self) { day in
                            let rows = (grouped[day] ?? []).sorted { ($0.at ?? 0) > ($1.at ?? 0) }
                            CardHeader(text: HubDates.dayLabel(Date(timeIntervalSince1970: rows.first?.at ?? 0)))
                                .padding(.top, day == days.first ? 0 : 10)
                                .padding(.bottom, 4)
                            ForEach(Array(rows.enumerated()), id: \.element.id) { i, d in
                                detection(d)
                                if i < rows.count - 1 { Divider().overlay(Theme.divider) }
                            }
                        }
                        if detections != nil {
                            Button {
                                loadingMore = true
                                Task {
                                    daysBack += 1
                                    await load()
                                    loadingMore = false
                                }
                            } label: {
                                HStack(spacing: 8) {
                                    if loadingMore { ProgressView().tint(Theme.text) }
                                    Text("Earlier days")
                                }
                                .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(PillButtonStyle(height: 42))
                            .disabled(loadingMore)
                            .padding(.top, 10)
                        }
                    }
                    .glassCard(padding: 14)
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 32)
            }
            .refreshable { await load() }
        }
        .task { await load() }
    }

    private func detection(_ d: Detection) -> some View {
        Button { model.openCamera = AppModel.CameraTarget(entityId: cameraId, cameraIds: [cameraId], alert: d.cameraAlert(camera: cameraId)) } label: {
            HStack(spacing: 12) {
                RemoteImage(url: model.url(d.thumbUrl ?? d.snapshot)) { Theme.cameraBackground }
                    .frame(width: 92, height: 56)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .overlay {
                        if d.clip != nil {
                            Image(systemName: "play.fill").font(.system(size: 16, weight: .bold)).foregroundColor(.white.opacity(0.9))
                        }
                    }
                VStack(alignment: .leading, spacing: 2) {
                    Text(d.label).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                    Text(DetectionLogic.line(d) { HubDates.time($0) })
                        .font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                }
                Spacer(minLength: 0)
            }
            .frame(minHeight: 72)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityHint(d.clip != nil ? "Plays its clip" : "Shows its picture")
    }

    /// From the start of today (and a day earlier per "Earlier days"); older hubs: the camera's own alerts.
    private func load() async {
        let since = DetectionLogic.since(daysBack: daysBack, now: Date())
        do {
            detections = try await APIClient.shared.detections(entity: cameraId, since: since)
        } catch let e as APIError where e.status == 404 {
            await loadOld(since: since)
        } catch {
            if detections == nil { detections = [] }
        }
    }

    private func loadOld(since: Double) async {
        guard let path = model.entity(cameraId)?.alerts,
              let list = try? await APIClient.shared.alerts(path: path, limit: 50) else {
            if detections == nil { detections = [] }
            return
        }
        detections = list.filter { ($0.start ?? 0) >= since }.map { a in
            var d = Detection(id: a.id, at: a.start,
                              label: a.objects?.first.map { DeviceLogic.humanize($0) } ?? (a.title ?? "Activity"),
                              zone: a.zones?.first, snapshot: a.snapshotUrl, clip: a.clip)
            d.thumbUrl = a.thumbUrl
            d.objects = a.objects
            d.zones = a.zones
            d.entityId = a.entityId
            d.cameraName = a.cameraName
            return d
        }
    }
}
