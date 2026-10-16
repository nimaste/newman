import SwiftUI

/// One page under a greeting and status chips on the first page or the page's name on the others.
/// From the hub's phone layout its tiles sit at their own place on the 4-column grid (PhoneGrid,
/// PlacedGridView); on older hubs a dashboard page's tiles flow into it (PhoneLayout).
struct PageScreen: View {
    @EnvironmentObject var model: AppModel
    let page: DashboardPage
    let isFirst: Bool
    /// Set when the page is shown over the tabs (Climate from its thermostat tile).
    var onClose: (() -> Void)? = nil
    @State private var showMe = false
    @State private var openPage: DashboardPage?
    @Environment(\.dynamicTypeSize) private var typeSize

    private var rows: [PhoneLayout.Row] {
        PhoneLayout.rows(PhoneLayout.cells(for: page, areaOf: { model.entities[$0]?.area }, exists: { model.entities[$0] != nil }))
    }

    var body: some View {
        let plan = model.tabPlan
        ZStack {
            NightGlassBackground(teal: isFirst)
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    header
                    if isFirst {
                        StatusChips(showWeather: !(page.tiles ?? []).contains { $0.type == "weather" }).padding(.bottom, 4)
                    }
                    if model.liveStatus == .down {
                        Label("Reconnecting to Newman…", systemImage: "arrow.triangle.2.circlepath")
                            .font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted)
                    }
                    if isFirst && PhoneTabs.showsTodayCard(plan, page: page) {
                        TodayCardView(showWhenEmpty: PhoneTabs.todayWhenEmpty(plan))
                    }
                    if plan.explicit {
                        PlacedGridView(tiles: PhoneGrid.placed(page.tiles ?? []))
                    } else {
                        LazyVStack(spacing: Theme.gap) {
                            ForEach(rows) { row in
                                GridRowView(row: row, pageName: page.name)
                            }
                        }
                    }
                    if plan.explicit ? (page.tiles ?? []).isEmpty : rows.isEmpty {
                        Text("Nothing on this page yet. Add tiles from the Newman dashboard.")
                            .font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted).padding(.top, 30)
                    }
                    if isFirst {
                        ForEach(plan.more) { p in
                            Button { openPage = p } label: {
                                HStack(spacing: 12) {
                                    Image(systemName: Symbols.name(p.icon)).font(.system(size: 18, weight: .semibold))
                                        .accessibilityHidden(true)
                                    Text(p.name).font(AppFont.manrope(15, .heavy))
                                    Spacer()
                                    Image(systemName: "chevron.right").font(.system(size: 14, weight: .bold)).foregroundColor(Theme.dim)
                                        .accessibilityHidden(true)
                                }
                                .foregroundColor(Theme.text)
                                .frame(minHeight: 30)
                                .glassCard(radius: 18, padding: 12)
                            }
                            .buttonStyle(.plain)
                            .accessibilityHint("Opens the page")
                        }
                    }
                }
                .padding(.horizontal, 16)
                // Clear of the tab bar: the last card scrolls up above it.
                .padding(.bottom, 40)
            }
            .refreshable { await model.reloadAll() }
        }
        .sheet(isPresented: $showMe) { MeScreen().environmentObject(model).environmentObject(PushManager.shared) }
        .fullScreenCover(item: $openPage) { p in
            PageScreen(page: p, isFirst: false, onClose: { openPage = nil }).environmentObject(model)
        }
    }

    /// The header's lines wrap instead of shrinking at the accessibility text sizes.
    private var headerLines: Int { typeSize.isAccessibilitySize ? 3 : 1 }

    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            if let onClose {
                Button { onClose() } label: { Image(systemName: "chevron.left") }
                    .buttonStyle(PillButtonStyle())
                    .accessibilityLabel("Back")
            }
            if isFirst {
                VStack(alignment: .leading, spacing: 2) {
                    Text(Date(), format: .dateTime.weekday(.wide).month(.wide).day())
                        .font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted)
                    Text(greeting).font(AppFont.sora(26)).foregroundColor(Theme.text).lineLimit(headerLines).minimumScaleFactor(0.7)
                    if let s = todaySummary {
                        Text(s).font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted).lineLimit(headerLines)
                    }
                }
                .accessibilityElement(children: .combine)
                .accessibilityAddTraits(.isHeader)
            } else {
                Text(page.name).font(AppFont.sora(28)).foregroundColor(Theme.text)
                    .accessibilityAddTraits(.isHeader)
            }
            Spacer()
            Button { showMe = true } label: {
                Text(initial)
                    .font(AppFont.manrope(17, .heavy))
                    .foregroundColor(Theme.text)
                    .frame(width: 44, height: 44)
                    .background(Circle().fill(Color.white.opacity(0.10)))
                    .overlay(Circle().stroke(Color.white.opacity(0.16)))
                    .cappedDynamicType(.xxLarge) // one letter in a fixed circle
            }
            .accessibilityLabel("Your account")
            .accessibilityHint("Settings, sign-in and location sharing")
        }
        .padding(.top, 12)
        .padding(.bottom, 4)
    }

    /// "2 things to do · 1 event" under the greeting.
    private var todaySummary: String? {
        guard let b = model.today else { return nil }
        let s = TodayView.card(b, done: model.todayDone, now: Date().timeIntervalSince1970).summary
        return s.isEmpty ? nil : s
    }

    private var initial: String {
        String((model.user?.name ?? model.user?.username ?? "N").prefix(1)).uppercased()
    }

    private var greeting: String {
        let h = Calendar.current.component(.hour, from: Date())
        let part = h < 12 ? "Good morning" : (h < 18 ? "Good afternoon" : "Good evening")
        if let n = model.user?.name, !n.isEmpty { return "\(part), \(n.split(separator: " ").first.map(String.init) ?? n)" }
        return part
    }
}

/// "All secure", the house mode, the weather (unless the page shows its own weather tile), where you
/// are, what needs attention. The chips wrap onto more lines rather than running off the screen.
struct StatusChips: View {
    @EnvironmentObject var model: AppModel
    var showWeather = true

    var body: some View {
        let ents = model.allEntities
        WrapLayout(spacing: 8) {
                if let s = HouseStatus.securitySummary(ents, kinds: model.kinds) {
                    if s == "All secure" {
                        StatusChip(text: s, symbol: "checkmark.shield", tint: Theme.teal)
                    } else {
                        StatusChip(text: s, symbol: "exclamationmark.triangle", tint: Theme.coral)
                    }
                }
                if let mode = HouseStatus.modeDevice(ents, kinds: model.kinds) {
                    StatusChip(text: "\(mode.name): \(DeviceLogic.stateLabel(mode, model.kinds[mode.kind]).lowercased())")
                }
                if showWeather, let w = WeatherModel.pick(tile: nil, entities: ents) {
                    let wm = WeatherModel(entity: w, kind: model.kinds[w.kind])
                    if wm.temperature != nil {
                        StatusChip(text: "\(WeatherModel.deg(wm.temperature)) \(wm.label)", symbol: Symbols.weather(wm.condition, isDay: wm.isDay))
                    }
                }
                if let a = model.authState {
                    StatusChip(text: a.isHome ? "At home" : (a.hasCertificate ? "Away · secure link" : "Away"),
                               dot: a.isHome ? Theme.teal : Theme.blue)
                }
                let bad = ents.filter { $0.health?.ok == false }
                if !bad.isEmpty {
                    StatusChip(text: bad.count == 1 ? "\(bad[0].name): \(bad[0].issues.first ?? "needs attention")" : "\(bad.count) devices need attention",
                               symbol: "exclamationmark.triangle", tint: Theme.coral)
                }
        }
    }
}

/// Lays its children out left to right, starting a new line when the next one doesn't fit
/// (the status chips). A child wider than the line gets the whole line.
struct WrapLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        let rows = arrange(subviews, width: width)
        let height = rows.reduce(0) { $0 + $1.height } + spacing * CGFloat(max(0, rows.count - 1))
        let used = rows.map { $0.width }.max() ?? 0
        return CGSize(width: proposal.width ?? used, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in arrange(subviews, width: bounds.width) {
            var x = bounds.minX
            for i in row.items {
                let size = subviews[i].sizeThatFits(ProposedViewSize(width: min(bounds.width, row.sizes[i] ?? 0), height: nil))
                subviews[i].place(at: CGPoint(x: x, y: y + (row.height - size.height) / 2), anchor: .topLeading,
                                  proposal: ProposedViewSize(width: size.width, height: size.height))
                x += size.width + spacing
            }
            y += row.height + spacing
        }
    }

    private struct Row {
        var items: [Int] = []
        var sizes: [Int: CGFloat] = [:]
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    private func arrange(_ subviews: Subviews, width: CGFloat) -> [Row] {
        var rows: [Row] = []
        var row = Row()
        for i in subviews.indices {
            let size = subviews[i].sizeThatFits(ProposedViewSize(width: width, height: nil))
            let w = min(size.width, width)
            if !row.items.isEmpty && row.width + spacing + w > width {
                rows.append(row)
                row = Row()
            }
            row.width += (row.items.isEmpty ? 0 : spacing) + w
            row.height = max(row.height, size.height)
            row.items.append(i)
            row.sizes[i] = w
        }
        if !row.items.isEmpty { rows.append(row) }
        return rows
    }
}

/// One row of the phone grid: cells side by side, 2 of 4 columns each or the full width.
struct GridRowView: View {
    let row: PhoneLayout.Row
    let pageName: String

    var body: some View {
        HStack(alignment: .top, spacing: Theme.gap) {
            ForEach(row.cells) { cell in
                CellView(cell: cell)
                    .frame(maxWidth: .infinity)
            }
            if row.used < PhoneLayout.columns && row.cells.count == 1 && row.cells[0].span < PhoneLayout.columns {
                Color.clear.frame(maxWidth: .infinity, maxHeight: 1)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
    }
}

struct CellView: View {
    let cell: PhoneLayout.Cell

    var body: some View {
        switch cell.content {
        case .heading(let text, let scale):
            Text(text)
                .font(AppFont.manrope(15 * scale, .heavy))
                .foregroundColor(Theme.text)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 6)
                .accessibilityAddTraits(.isHeader)
        case .device(let tile, let entityId):
            DeviceCard(entityId: entityId, wide: cell.span == PhoneLayout.columns, scale: tile.textScale)
        case .tile(let tile):
            TileView(tile: tile, wide: cell.span == PhoneLayout.columns)
        }
    }
}
