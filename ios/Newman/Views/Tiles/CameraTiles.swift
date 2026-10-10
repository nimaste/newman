import SwiftUI

/// The camera tile: live video (or a still refreshing every 2 s) while on screen, the tile's
/// cameras as pills; tap the picture for full screen.
struct CameraTile: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.tileScale) var scale
    let tile: Tile
    @State private var pick: String?

    var body: some View {
        let cams = tile.strings("entities").compactMap { model.entity($0) }
        let cam = cams.first { $0.id == pick } ?? cams.first
        return VStack(alignment: .leading, spacing: 8) {
            if let cam {
                Button {
                    model.openCamera = AppModel.CameraTarget(entityId: cam.id, cameraIds: cams.map { $0.id })
                } label: {
                    ZStack(alignment: .topLeading) {
                        // Live video (sub stream) over the still; just the refreshing still without live.
                        LiveCameraView(entity: cam, quality: .sub, priority: false, stillHeight: 480, contentMode: .fill, placeholder: {
                            LinearGradient(colors: [Color(hex: 0x26314F), Color(hex: 0x121A33)], startPoint: .topLeading, endPoint: .bottomTrailing)
                        })
                        .id(cam.id)
                        .frame(height: 190)
                        .frame(maxWidth: .infinity)
                        .clipped()
                        CameraNameBadge(name: cam.name)
                            .padding(10)
                    }
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(cam.name) camera")
                .accessibilityAddTraits(.isImage)
                .accessibilityHint("Opens it full screen")
                if cams.count > 1 {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(cams) { c in
                                Button(c.name) { pick = c.id }
                                    .buttonStyle(PillButtonStyle(selected: c.id == cam.id, height: 36))
                                    .accessibilityHint("Shows this camera in the tile")
                            }
                        }
                    }
                }
            } else {
                Text("No cameras chosen for this tile.").font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted).padding(6)
            }
        }
        .glassCard(padding: 8)
    }
}

struct CameraNameBadge: View {
    let name: String

    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(Theme.red).frame(width: 7, height: 7)
            Text(name).font(AppFont.manrope(12, .heavy)).foregroundColor(Theme.text)
        }
        .padding(.horizontal, 9)
        .frame(height: 26)
        .background(Capsule().fill(Color(hex: 0x0B1026, alpha: 0.72)))
    }
}

/// Recent camera alerts (all, or the tile's cameras); tap one to see it on its camera.
struct AlertsTile: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.tileScale) var scale
    let tile: Tile

    var body: some View {
        let ids = tile.strings("entities")
        let limit = Int(max(1, min(12, tile.number("limit") ?? 4)))
        let items = model.cameraAlerts.filter { ids.isEmpty || ids.contains($0.entityId ?? "") }.prefix(limit)
        return VStack(alignment: .leading, spacing: 8) {
            TileTitle(text: "Recent alerts")
            if items.isEmpty {
                Text("Nothing lately.").font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted)
            }
            ForEach(Array(items)) { a in
                AlertRow(alert: a) {
                    if let eid = a.entityId {
                        model.openCamera = AppModel.CameraTarget(entityId: eid, cameraIds: ids.isEmpty ? [eid] : ids, alert: a)
                    }
                }
            }
        }
        .glassCard(padding: 14)
    }
}

struct AlertRow: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.tileScale) var scale
    let alert: CameraAlert
    var highlighted = false
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 12) {
                ZStack {
                    RemoteImage(url: model.url(alert.thumbUrl ?? alert.snapshotUrl)) {
                        LinearGradient(colors: [Color(hex: 0x2B3758), Color(hex: 0x161E38)], startPoint: .topLeading, endPoint: .bottomTrailing)
                    }
                    Image(systemName: "play.fill").font(.system(size: 12)).foregroundColor(Color(hex: 0xC9D4F5))
                }
                .frame(width: 84, height: 50)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                VStack(alignment: .leading, spacing: 2) {
                    Text(alert.title ?? "Alert").font(AppFont.manrope(14 * scale, .heavy)).foregroundColor(Theme.text).lineLimit(1)
                    Text((alert.zones ?? []).joined(separator: ", ").isEmpty ? (alert.cameraName ?? "") : (alert.zones ?? []).joined(separator: ", "))
                        .font(AppFont.manrope(12 * scale, .bold)).foregroundColor(Theme.muted).lineLimit(1)
                }
                Spacer()
                if let s = alert.start {
                    Text(HubDates.time(Date(timeIntervalSince1970: s))).font(AppFont.manrope(12 * scale, .heavy)).foregroundColor(Theme.muted)
                }
            }
            .padding(6)
            .background(RoundedRectangle(cornerRadius: 14).fill(highlighted ? Color.white.opacity(0.10) : Color.clear))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(highlighted ? Theme.text.opacity(0.6) : Color.clear, lineWidth: 2))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(accessibilityText)
        .accessibilityAddTraits(highlighted ? .isSelected : [])
        .accessibilityHint("Shows what the camera saw")
    }

    /// "Person, Driveway, 3:42 PM" (title, zones or camera, time).
    private var accessibilityText: String {
        let zones = (alert.zones ?? []).joined(separator: ", ")
        return [alert.title ?? "Alert", zones.isEmpty ? alert.cameraName : zones,
                alert.start.map { HubDates.time(Date(timeIntervalSince1970: $0)) }]
            .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: ", ")
    }
}
