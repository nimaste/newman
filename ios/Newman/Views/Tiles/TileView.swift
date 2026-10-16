import SwiftUI

/// Draws a dashboard tile by its type (GET /api/dashboards/tiles). Devices come only from the
/// tile's config; what they can do comes from /api/meta. Unknown types get a placeholder.
/// (Placed on a phone layout, people and greeting tiles get their cell's size from PlacedTileView.)
struct TileView: View {
    @EnvironmentObject var model: AppModel
    let tile: Tile
    let wide: Bool

    var body: some View {
        Group {
            switch tile.type {
            case "camera": CameraTile(tile: tile)
            case "alerts": AlertsTile(tile: tile)
            case "dial": DialTile(tile: tile, wide: wide)
            case "devices": DevicesListTile(tile: tile)
            case "mode": ModeTile(tile: tile)
            case "values": ValuesTile(tile: tile)
            case "attention": AttentionTile(tile: tile)
            case "weather": WeatherTile(tile: tile, wide: wide)
            case "calendar": CalendarTile(tile: tile)
            case "today", "todo": TodayCardView(showWhenEmpty: true)
            case "people": PeopleTileView(tile: tile)
            case "greeting": GreetingTileView()
            default: SoonTile(tile: tile)
            }
        }
        .environment(\.tileScale, tile.textScale)
    }
}

private struct TileScaleKey: EnvironmentKey {
    static let defaultValue: Double = 1
}

extension EnvironmentValues {
    /// A tile's text size (config.textSize) as a factor for its fonts.
    var tileScale: Double {
        get { self[TileScaleKey.self] }
        set { self[TileScaleKey.self] = newValue }
    }
}

/// Every card's heading: a small capitals label ("CAMERAS", "SECURITY · 15") and, on the right, the
/// card's one action ("All 8", "Show all") or a note. One look for every tile and list on the phone.
struct CardHeader<Trailing: View>: View {
    @Environment(\.tileScale) var scale
    let text: String
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        HStack(spacing: 8) {
            Text(text.uppercased())
                .font(AppFont.manrope(12 * scale, .heavy)).kerning(1.1).foregroundColor(Theme.dim)
                .lineLimit(1).minimumScaleFactor(0.8)
                .accessibilityLabel(text) // not spelled out letter by letter
                .accessibilityAddTraits(.isHeader)
            Spacer(minLength: 8)
            trailing()
        }
        .frame(minHeight: 22)
    }
}

extension CardHeader where Trailing == EmptyView {
    init(text: String) {
        self.text = text
        self.trailing = { EmptyView() }
    }
}

struct TileTitle: View {
    @Environment(\.tileScale) var scale
    let text: String
    var note: String? = nil
    var noteColor: Color = Theme.muted

    var body: some View {
        CardHeader(text: text) {
            if let note { Text(note).font(AppFont.manrope(13 * scale, .heavy)).foregroundColor(noteColor).lineLimit(1) }
        }
    }
}

/// A "devices" tile in the list look: its title and one row per device.
struct DevicesListTile: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.tileScale) var scale
    let tile: Tile

    var body: some View {
        let byArea = tile.string("groupBy") == "area"
        var ids = tile.strings("entities").filter { model.entities[$0] != nil }
        if byArea { ids.sort { (model.entities[$0]?.area ?? "~").localizedCompare(model.entities[$1]?.area ?? "~") == .orderedAscending } }
        return VStack(alignment: .leading, spacing: 0) {
            if let t = tile.string("title"), !t.isEmpty { TileTitle(text: t).padding(.vertical, 8) }
            if ids.isEmpty { Text("No devices chosen.").font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted).padding(.vertical, 8) }
            ForEach(Array(ids.enumerated()), id: \.element) { i, id in
                DeviceRow(entityId: id, showArea: byArea, scale: scale)
                if i < ids.count - 1 { Divider().overlay(Theme.divider) }
            }
        }
        .glassCard(padding: 14)
    }
}

/// The house mode (an alarm): its state large, and the commands that change it.
struct ModeTile: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.tileScale) var scale
    let tile: Tile

    var body: some View {
        Group {
            if let e = model.entity(tile.string("entity") ?? "") {
                let kind = model.kind(of: e)
                let cmds = DeviceLogic.plainCommands(kind).filter { $0.confirmsTo != e.state }
                VStack(alignment: .leading, spacing: 10) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(e.name).font(AppFont.manrope(12 * scale, .heavy)).foregroundColor(Theme.muted)
                        Text(e.isAvailable ? DeviceLogic.stateLabel(e, kind) : "Offline")
                            .font(AppFont.sora(22 * scale)).foregroundColor(Theme.text)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityAddTraits(.isHeader)
                    LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
                        ForEach(cmds) { c in
                            Button {
                                Task { await model.run(e.id, c) }
                            } label: {
                                HStack(spacing: 6) {
                                    if DeviceLogic.needsAuthentication(c, biometricsEnabled: model.biometricsEnabled) {
                                        Image(systemName: "faceid").accessibilityHidden(true)
                                    }
                                    Text(c.label).lineLimit(1).minimumScaleFactor(0.7)
                                }
                                .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(PillButtonStyle(height: 48))
                            .disabled(!e.isAvailable || model.busy.contains(e.id))
                            .accessibilityLabel(c.label)
                            .accessibilityHint(DeviceLogic.needsAuthentication(c, biometricsEnabled: model.biometricsEnabled)
                                               ? "Asks for \(DeviceAuth.methodName) first" : "")
                        }
                    }
                }
            } else {
                Text("This device is missing.").font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted)
            }
        }
        .glassCard(padding: 14)
    }
}

/// Readings: one device value per row (temperature, humidity…), from the device's properties.
struct ValuesTile: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.tileScale) var scale
    let tile: Tile

    private struct Item: Identifiable {
        var id: String
        var name: String
        var prop: DeviceProperty?
    }

    var body: some View {
        let items: [Item] = (tile.config?["items"]?.arrayValue ?? []).compactMap { it in
            guard let eid = it["entity"]?.stringValue, let e = model.entity(eid) else { return nil }
            let pid = it["property"]?.stringValue
            return Item(id: eid + ":" + (pid ?? ""), name: e.name, prop: (e.properties ?? []).first { $0.id == pid })
        }
        return VStack(alignment: .leading, spacing: 0) {
            if let t = tile.string("title"), !t.isEmpty { TileTitle(text: t).padding(.bottom, 6) }
            if items.isEmpty { Text("No readings chosen.").font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted) }
            ForEach(Array(items.enumerated()), id: \.element.id) { i, it in
                HStack {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(it.name).font(AppFont.manrope(15 * scale, .heavy)).foregroundColor(Theme.text)
                        Text(it.prop?.label ?? "Not reported").font(AppFont.manrope(12 * scale, .bold)).foregroundColor(Theme.muted)
                    }
                    Spacer()
                    Text(it.prop.map { DeviceLogic.formatProp($0, $0.value) } ?? "—")
                        .font(AppFont.sora(20 * scale)).foregroundColor(Theme.text)
                }
                .frame(minHeight: 50)
                .accessibilityElement(children: .combine)
                if i < items.count - 1 { Divider().overlay(Theme.divider) }
            }
        }
        .glassCard(padding: 14)
    }
}

/// Needs attention: leaks/smoke first, then offline, low battery, tamper.
struct AttentionTile: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.tileScale) var scale
    let tile: Tile

    var body: some View {
        let problems = HouseStatus.attention(model.allEntities, kinds: model.kinds)
        let bad = !problems.isEmpty
        return VStack(alignment: .leading, spacing: 8) {
            Label(bad ? "Needs attention" : "All devices OK", systemImage: bad ? "exclamationmark.triangle" : "checkmark.circle")
                .font(AppFont.manrope(15 * scale, .heavy))
                .foregroundColor(bad ? Theme.text : Theme.teal)
                .accessibilityAddTraits(.isHeader)
            ForEach(problems) { p in
                VStack(alignment: .leading, spacing: 1) {
                    Text(p.name).font(AppFont.manrope(14 * scale, .heavy)).foregroundColor(Theme.text)
                    Text(p.issues.joined(separator: " · ")).font(AppFont.manrope(12 * scale, .bold))
                        .foregroundColor(p.isAlarm ? Theme.red : Theme.coralText)
                }
                .accessibilityElement(children: .combine)
            }
        }
        .glassCard(padding: 14, fill: bad ? Theme.coral.opacity(0.10) : Theme.card,
                   border: bad ? Theme.coral.opacity(0.35) : Theme.cardBorder)
    }
}

struct SoonTile: View {
    let tile: Tile

    var body: some View {
        VStack(spacing: 6) {
            Image(systemName: "square.grid.2x2").font(.system(size: 24)).foregroundColor(Theme.muted)
                .accessibilityHidden(true)
            Text(DeviceLogic.humanize(tile.type)).font(AppFont.manrope(14, .heavy)).foregroundColor(Theme.text)
            Text("Coming soon to the phone").font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
        }
        .frame(maxWidth: .infinity, minHeight: 90)
        .glassCard()
    }
}
