import SwiftUI

/// A page of the hub's phone layout: tiles placed exactly at their x/y/w/h on 4 columns of square
/// cells (PhoneGrid). The page scrolls (in PageScreen); empty cells stay empty; nothing reflows.
struct PlacedGridView: View {
    let tiles: [Tile]
    @State private var width: CGFloat = 0

    var body: some View {
        let gap = Double(Theme.gap)
        let w = Double(width)
        ZStack(alignment: .topLeading) {
            Color.clear
                .frame(maxWidth: .infinity)
                .frame(height: CGFloat(PhoneGrid.height(tiles, width: w, gap: gap)))
            if width > 0 {
                ForEach(tiles) { t in
                    let r = PhoneGrid.rect(t, width: w, gap: gap)
                    PlacedTileView(tile: t, size: CGSize(width: r.width, height: r.height))
                        .frame(width: CGFloat(r.width), height: CGFloat(r.height))
                        .offset(x: CGFloat(r.x), y: CGFloat(r.y))
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        // The cells are fixed squares: text grows with Larger Text up to a cap (tiles that then
        // overflow scroll inside, device buttons shrink their labels); sheets opened from here scale fully.
        .cappedDynamicType()
        .background(
            GeometryReader { g in
                Color.clear
                    .onAppear { width = g.size.width }
                    .onChange(of: g.size.width) { _, w in width = w }
            }
        )
    }
}

/// One placed tile, sized to its cell rectangle. Its card fills the rectangle; a tile with more
/// than fits scrolls inside it. A "devices" tile shows its devices as a grid or list instead; the
/// people and greeting tiles fit themselves to the rectangle (fewer rows, "+N more", one line).
struct PlacedTileView: View {
    let tile: Tile
    let size: CGSize

    var body: some View {
        if tile.type == "devices" {
            DevicesGridTile(tile: tile, size: size)
        } else if tile.type == "people" {
            PeopleTileView(tile: tile, size: size).environment(\.tileScale, tile.textScale)
        } else if tile.type == "greeting" {
            GreetingTileView(size: size).environment(\.tileScale, tile.textScale)
        } else {
            ViewThatFits(in: .vertical) {
                TileView(tile: tile, wide: !PhoneGrid.isCompact(tile))
                ScrollView(.vertical, showsIndicators: false) {
                    TileView(tile: tile, wide: !PhoneGrid.isCompact(tile))
                }
            }
            .clipped()
        }
    }
}

/// A "devices" tile placed at its own size: two columns of device buttons when the tile is wide
/// enough, a list when narrow (PhoneGrid.fitDevices). Devices that don't fit become a "+N more"
/// button that shows them all - the tile itself never scrolls.
struct DevicesGridTile: View {
    @EnvironmentObject var model: AppModel
    let tile: Tile
    let size: CGSize
    @State private var showAll = false

    var body: some View {
        var ids = tile.strings("entities").filter { model.entities[$0] != nil }
        if tile.string("groupBy") == "area" {
            ids.sort { (model.entities[$0]?.area ?? "~").localizedCompare(model.entities[$1]?.area ?? "~") == .orderedAscending }
        }
        let list = tile.string("style") == "list"
        let named = (tile.string("title") ?? "").trimmingCharacters(in: .whitespaces)
        // Untitled buttons across the phone (two rows or more) are the page's quick controls; a small
        // tile of one or two buttons stays without a heading.
        let cell = PhoneGrid.clamp(tile)
        let quick = named.isEmpty && !list && ids.count > 1 && cell.w >= PhoneGrid.columns && cell.h >= 2
        let title = quick ? "Quick controls" : named
        let fit = PhoneGrid.fitDevices(count: ids.count, width: Double(size.width), height: Double(size.height),
                                       titled: !title.isEmpty, list: list)
        let shown = Array(ids.prefix(fit.shown))
        let gap = CGFloat(PhoneGrid.deviceGap)
        let row = CGFloat(PhoneGrid.deviceRow)
        let columns = Array(repeating: GridItem(.flexible(), spacing: gap), count: fit.columns)
        return VStack(alignment: .leading, spacing: 0) {
            if !title.isEmpty {
                CardHeader(text: title)
                    .environment(\.tileScale, tile.textScale)
                    .padding(.horizontal, 4)
                    .frame(height: CGFloat(PhoneGrid.deviceTitle), alignment: .topLeading)
            }
            if ids.isEmpty {
                Text("No devices chosen.").font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted).padding(4)
            }
            LazyVGrid(columns: columns, alignment: .leading, spacing: gap) {
                ForEach(shown, id: \.self) { id in
                    DeviceButton(entityId: id, scale: tile.textScale).frame(height: row)
                }
                if fit.more > 0 {
                    Button { showAll = true } label: {
                        Text("+\(fit.more) more")
                            .font(AppFont.manrope(14, .heavy))
                            .minimumScaleFactor(0.7)
                            .foregroundColor(Theme.text)
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                            .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Color.white.opacity(0.06)))
                    }
                    .buttonStyle(.plain)
                    .frame(height: row)
                    .accessibilityLabel("\(fit.more) more devices")
                    .accessibilityHint("Shows every device in \(title.isEmpty ? "this tile" : title)")
                }
            }
        }
        .glassCard(padding: CGFloat(PhoneGrid.devicePadding))
        .sheet(isPresented: $showAll) {
            ZStack {
                NightGlassBackground()
                ScrollView {
                    DevicesListTile(tile: tile).padding(16)
                }
            }
            .environment(\.tileScale, tile.textScale)
            .environmentObject(model)
            .fullDynamicType()
        }
    }
}

/// One device inside a placed devices tile: tap runs its main action, a long press opens
/// everything it can do (like DeviceCard, smaller).
struct DeviceButton: View {
    @EnvironmentObject var model: AppModel
    let entityId: String
    var scale: Double = 1
    @State private var sheet = false

    var body: some View {
        if let e = model.entity(entityId) {
            let kind = model.kind(of: e)
            let action = DeviceLogic.tapAction(e, kind)
            let lit = DeviceLogic.isLit(e, kind)
            let vitals = DeviceLogic.vitals(e)
            let warn = DeviceLogic.tone(e, kind, problem: vitals.problem) == .warn
            let fg = lit ? Theme.amberInk : Theme.text
            let state = vitals.problem ?? (DeviceLogic.stateText(e, kind) + (vitals.battery.map { " · \($0)%" } ?? ""))

            HStack(spacing: 10) {
                if model.busy.contains(e.id) {
                    ProgressView().tint(fg).frame(width: 22)
                } else {
                    Image(systemName: Symbols.name(DeviceLogic.kindIcon(kind))).font(.system(size: 18, weight: .medium)).frame(width: 22)
                        .accessibilityHidden(true)
                }
                VStack(alignment: .leading, spacing: 1) {
                    Text(e.name).font(AppFont.manrope(14 * scale, .heavy)).lineLimit(1).minimumScaleFactor(0.7)
                    Text(state).font(AppFont.manrope(12 * scale, .bold)).opacity(0.8).lineLimit(1).minimumScaleFactor(0.7)
                }
                Spacer(minLength: 0)
            }
            .foregroundColor(fg)
            .padding(.horizontal, 10)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(lit ? Theme.amber : Color.white.opacity(0.06)))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(warn ? Theme.coral.opacity(0.5) : Color.clear, lineWidth: 1))
            .opacity(e.isAvailable ? 1 : 0.55)
            .contentShape(Rectangle())
            // Tap = the main action; a long press opens the sheet instead (never both).
            .onTapGesture {
                guard !model.busy.contains(e.id) else { return }
                if let action, e.isAvailable { Task { await model.run(e.id, action) } } else { sheet = true }
            }
            .onLongPressGesture(minimumDuration: 0.45) { sheet = true }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(e.name)
            .accessibilityValue(model.busy.contains(e.id) ? "Working" : state)
            .accessibilityAddTraits(.isButton)
            .accessibilityHint(e.isAvailable ? (action?.label ?? "Opens its controls") : "Opens its controls")
            .accessibilityAction { // VoiceOver's double-tap, same as a tap
                guard !model.busy.contains(e.id) else { return }
                if let action, e.isAvailable { Task { await model.run(e.id, action) } } else { sheet = true }
            }
            .accessibilityAction(named: "More") { sheet = true }
            .sheet(isPresented: $sheet) { DeviceSheet(entityId: e.id).environmentObject(model) }
        }
    }
}
