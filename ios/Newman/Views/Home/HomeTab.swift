import SwiftUI

/// The screens under the Home tab.
enum HomeRoute: Hashable {
    case climate
    case climateRooms
    case lights
    case security
    case today
    case people
    case editHome
    /// A category without a purpose-built screen: its devices (CategoryScreen).
    case category(String)
}

/// The first tab: Home, and the category screens its cards open, in a navigation stack
/// (AppModel.homePath).
struct HomeTab: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        NavigationStack(path: $model.homePath) {
            HomeScreen()
                .toolbar(.hidden, for: .navigationBar)
                .navigationDestination(for: HomeRoute.self) { route in
                    Group {
                        switch route {
                        case .climate: ClimateScreen()
                        case .climateRooms: ClimateRoomsScreen()
                        case .lights: LightsScreen()
                        case .security: SecurityScreen()
                        case .today: TodayScreen()
                        case .people: PeopleScreen()
                        case .editHome: EditHomeScreen()
                        case .category(let id): CategoryScreen(category: id)
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

/// Home: what needs you (a red line for real dangers with their button, amber lines for device problems
/// with Mute, else "All good at home"), the category cards (worded by the hub), and the favourites.
struct HomeScreen: View {
    @EnvironmentObject var model: AppModel
    @State private var showMe = false
    @State private var muteTarget: MuteTarget?
    @State private var sheetFor: HomeSheet?

    var body: some View {
        ZStack {
            NightGlassBackground()
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    header
                    if let h = model.home {
                        status(h.status)
                        LazyVGrid(columns: [GridItem(.flexible(), spacing: Theme.gap), GridItem(.flexible(), spacing: Theme.gap)],
                                  spacing: Theme.gap) {
                            ForEach(h.shownCards) { c in
                                HomeCardView(card: c) { open(c) }
                            }
                        }
                        if !h.favourites.isEmpty { favourites(h.favourites) }
                    } else {
                        ProgressView().tint(Theme.text).frame(maxWidth: .infinity).padding(.top, 40)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
            }
            .refreshable { await model.reloadAll() }
        }
        .sheet(isPresented: $showMe) { MeScreen().environmentObject(model).environmentObject(PushManager.shared) }
        .sheet(item: $sheetFor) { s in DeviceSheet(entityId: s.id).environmentObject(model) }
        .muteDialog($muteTarget)
    }

    private var header: some View {
        HStack(spacing: 10) {
            Text("Home").font(AppFont.sora(30)).foregroundColor(Theme.text)
                .accessibilityAddTraits(.isHeader)
            Spacer()
            if model.canEditHome {
                Button { model.homePath.append(.editHome) } label: {
                    Image(systemName: "pencil").font(.system(size: 16, weight: .bold)).foregroundColor(Theme.text)
                        .frame(width: 40, height: 40)
                        .background(Circle().fill(Theme.control))
                        .overlay(Circle().stroke(Theme.controlBorder))
                }
                .accessibilityLabel("Edit Home")
            }
            Button { showMe = true } label: {
                Text(String((model.user?.name ?? model.user?.username ?? "?").prefix(1)).uppercased())
                    .font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                    .frame(width: 40, height: 40)
                    .background(Circle().fill(Theme.control))
                    .overlay(Circle().stroke(Theme.controlBorder))
            }
            .accessibilityLabel("Your account")
        }
        .padding(.top, 12)
    }

    @ViewBuilder private func status(_ s: HomeData.Status) -> some View {
        // ok: the green line. warn: "Needs a look" (no green line). danger: only the red banners. The
        // amber rows with Mute follow in every case.
        VStack(spacing: 10) {
            switch s.mode {
            case .ok:
                statusLine("All good at home", summary: s.summary, dot: Theme.teal, summaryColor: Theme.teal,
                           fill: Theme.teal.opacity(0.10), border: Theme.teal.opacity(0.35))
            case .warn:
                statusLine("Needs a look", summary: s.summary, dot: Theme.amber, summaryColor: Theme.muted,
                           fill: Theme.card, border: Theme.cardBorder)
            case .danger:
                ForEach(s.danger) { d in dangerRow(d) }
            }
            ForEach(s.warnings) { w in warningRow(w) }
        }
    }

    private func statusLine(_ title: String, summary: String, dot: Color, summaryColor: Color, fill: Color, border: Color) -> some View {
        HStack(spacing: 12) {
            Circle().fill(dot).frame(width: 10, height: 10).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                if !summary.isEmpty {
                    Text(summary).font(AppFont.manrope(12, .bold)).foregroundColor(summaryColor)
                }
            }
            Spacer(minLength: 0)
        }
        .glassCard(radius: 18, padding: 14, fill: fill, border: border)
        .accessibilityElement(children: .combine)
    }

    private func dangerRow(_ d: HomeData.Danger) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 16, weight: .bold)).foregroundColor(Theme.red)
                .frame(width: 34, height: 34)
                .background(Circle().fill(Theme.red.opacity(0.18)))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                Text(d.title).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                if !d.detail.isEmpty { Text(d.detail).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.coralText) }
            }
            Spacer(minLength: 8)
            if let a = d.action, let eid = d.entity {
                Button(a.label) { Task { await runAction(eid, a.command) } }
                    .buttonStyle(PillButtonStyle(height: 38, prominent: true))
            }
        }
        .glassCard(radius: 18, padding: 12, fill: Theme.red.opacity(0.14), border: Theme.red.opacity(0.45))
    }

    private func warningRow(_ w: HomeData.Warning) -> some View {
        let device = model.entity(w.entity)?.name ?? ""
        let problem = HomeScreen.problemText(w.text, device: device)
        return HStack(spacing: 10) {
            Image(systemName: "exclamationmark.triangle").font(.system(size: 14, weight: .bold)).foregroundColor(Theme.amber)
                .accessibilityHidden(true)
            Button { sheetFor = HomeSheet(id: w.entity) } label: {
                Text(w.text).font(AppFont.manrope(13, .bold)).foregroundColor(Theme.amber)
                    .multilineTextAlignment(.leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.plain)
            Button("Mute") { muteTarget = MuteTarget(entityId: w.entity, device: device, issue: w.issue, text: problem) }
                .font(AppFont.manrope(13, .heavy))
                .foregroundColor(Theme.amber)
                .frame(minHeight: 36)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Theme.amber.opacity(0.10)))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Theme.amber.opacity(0.35)))
    }

    /// "Motion Detector · Battery low" → "Battery low" (the dialog names the device itself).
    static func problemText(_ text: String, device: String) -> String {
        let prefix = device + " · "
        return !device.isEmpty && text.hasPrefix(prefix) ? String(text.dropFirst(prefix.count)) : text
    }

    private func favourites(_ ids: [String]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            CardHeader(text: "Favourites").padding(.horizontal, 4)
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4), spacing: 8) {
                ForEach(ids.prefix(HomeLogic.maxFavourites), id: \.self) { id in
                    FavouriteTile(entityId: id) { sheetFor = HomeSheet(id: id) }
                }
            }
        }
    }

    /// The card's purpose-built screen (the hub's `screen`), else its category's own list.
    private func open(_ card: HomeData.Card) {
        switch card.role {
        case "cameras": model.selectedTab = "cameras"
        case "climate": model.homePath.append(.climate)
        case "lights": model.homePath.append(.lights)
        case "security": model.homePath.append(.security)
        case "today": model.homePath.append(.today)
        case "people": model.homePath.append(.people)
        default: model.homePath.append(.category(card.category ?? card.id))
        }
    }

    private func runAction(_ entityId: String, _ command: String) async {
        guard let e = model.entity(entityId), let cmd = model.kind(of: e)?.command(command) else { return }
        await model.run(entityId, cmd)
    }
}

/// A device sheet opened from Home (by device id).
struct HomeSheet: Identifiable, Hashable {
    var id: String
}

/// A category card: its name and icon, the hub's value and detail, in the card's tone. The cameras card
/// shows its camera's picture.
struct HomeCardView: View {
    @EnvironmentObject var model: AppModel
    let card: HomeData.Card
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 8) {
                if let cam = card.camera, let e = model.entity(cam) {
                    RemoteImage(url: model.stillURL(e, height: 240)) { Theme.cameraBackground }
                        .frame(height: 56)
                        .frame(maxWidth: .infinity)
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .accessibilityHidden(true)
                } else {
                    HStack {
                        Text(card.title).font(AppFont.manrope(14, .heavy)).foregroundColor(Theme.text)
                        Spacer()
                        Image(systemName: style.symbol).font(.system(size: 17, weight: .semibold))
                            .foregroundColor(Color(hex: style.color))
                            .accessibilityHidden(true)
                    }
                }
                Spacer(minLength: 0)
                VStack(alignment: .leading, spacing: 1) {
                    if card.camera != nil { Text(card.title).font(AppFont.manrope(14, .heavy)).foregroundColor(Theme.text) }
                    if !card.value.isEmpty {
                        Text(card.value).font(AppFont.sora(CGFloat(HomeCards.valueSize(card.value)))).foregroundColor(Theme.text)
                            .lineLimit(1).minimumScaleFactor(0.6)
                    }
                    if !card.detail.isEmpty {
                        Text(card.detail).font(AppFont.manrope(12, .heavy)).foregroundColor(tint)
                            .lineLimit(2).minimumScaleFactor(0.8)
                    }
                }
            }
            .frame(height: 118, alignment: .topLeading)
            .glassCard(radius: 20, padding: 12, fill: card.tone == "danger" ? Theme.coral.opacity(0.12) : Theme.card,
                       border: card.tone == "danger" ? Theme.coral.opacity(0.4) : Theme.cardBorder)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityLabel([card.title, card.value, card.detail].filter { !$0.isEmpty }.joined(separator: ", "))
        .accessibilityAddTraits(.isButton)
    }

    /// Its screen's icon and colour; a category with no screen: its devices' most common icon, neutral.
    private var style: HomeCards.Style {
        if let s = HomeCards.style(role: card.role) { return s }
        let icon = CategoryLogic.mostCommonIcon(card.category ?? card.id, entities: model.allEntities, kinds: model.kinds)
        return HomeCards.Style(symbol: Symbols.name(icon), color: HomeCards.neutral)
    }

    private var tint: Color {
        switch card.tone {
        case "good": return Theme.teal
        case "warm": return Theme.amber
        case "danger": return Theme.coralText
        default: return Theme.muted
        }
    }
}

/// A favourite: tap does its main thing (on/off, lock/unlock…), a long press opens the device.
struct FavouriteTile: View {
    @EnvironmentObject var model: AppModel
    let entityId: String
    let openSheet: () -> Void

    var body: some View {
        if let e = model.entity(entityId) {
            let kind = model.kind(of: e)
            let lit = DeviceLogic.isLit(e, kind)
            let action = DeviceLogic.tapAction(e, kind)
            let state = LightRooms.brightness(e).flatMap { lit && $0 > 0 ? "On · \(Int($0.rounded()))%" : nil }
                ?? DeviceLogic.stateText(e, kind)
            Button {
                if let action { Task { await model.run(e.id, action) } } else { openSheet() }
            } label: {
                VStack(alignment: .leading, spacing: 4) {
                    Image(systemName: Symbols.name(DeviceLogic.icon(e, kind, kinds: model.kinds))).font(.system(size: 16, weight: .semibold))
                        .foregroundColor(lit ? Theme.amber : Theme.teal)
                        .accessibilityHidden(true)
                    Spacer(minLength: 0)
                    Text(e.name).font(AppFont.manrope(11, .heavy)).foregroundColor(Theme.text).lineLimit(2).minimumScaleFactor(0.8)
                    Text(state).font(AppFont.manrope(11, .bold)).foregroundColor(lit ? Theme.amber : Theme.muted).lineLimit(1)
                }
                .frame(maxWidth: .infinity, minHeight: 78, alignment: .topLeading)
                .padding(10)
                .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(lit ? Theme.amber.opacity(0.14) : Theme.card))
                .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(lit ? Theme.amber.opacity(0.4) : Theme.cardBorder))
            }
            .buttonStyle(.plain)
            .simultaneousGesture(LongPressGesture(minimumDuration: 0.5).onEnded { _ in openSheet() })
            .accessibilityElement(children: .combine)
            .accessibilityLabel("\(e.name), \(state)")
            .accessibilityHint(action.map { "\($0.label). Touch and hold for more" } ?? "Opens the device")
            .accessibilityAction(named: "More") { openSheet() }
        }
    }
}
