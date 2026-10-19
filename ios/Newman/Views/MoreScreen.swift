import SwiftUI

/// The screens under the More tab.
enum MoreRoute: Hashable {
    case alerts
    case devices
    case people
    case automations
    case automation(String)
    case newAutomation
    case editHome
}

/// The fourth tab: Alerts (with its count), People, Automations, Edit Home and the account, each
/// pushed in a navigation stack (AppModel.morePath, so a notification can open More › Alerts).
struct MoreTab: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        NavigationStack(path: $model.morePath) {
            MoreScreen()
                .toolbar(.hidden, for: .navigationBar)
                .navigationDestination(for: MoreRoute.self) { route in
                    Group {
                        switch route {
                        case .alerts: AlertsScreen()
                        case .devices: DevicesScreen()
                        case .people: PeopleScreen()
                        case .automations: AutomationsScreen()
                        case .automation(let id): AutomationEditorScreen(automationId: id)
                        case .newAutomation: AutomationEditorScreen(automationId: nil)
                        case .editHome: EditHomeScreen()
                        }
                    }
                    .navigationTitle("")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbarBackground(.hidden, for: .navigationBar)
                    .toolbarColorScheme(.dark, for: .navigationBar)
                }
        }
        .onChange(of: model.morePath) { _, path in
            if path.last == .alerts { model.markAlertsSeen() }
        }
    }
}

struct MoreScreen: View {
    @EnvironmentObject var model: AppModel
    @State private var showMe = false

    var body: some View {
        ZStack {
            NightGlassBackground(teal: false)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Text("More").font(AppFont.sora(28)).foregroundColor(Theme.text)
                        .padding(.top, 12)
                        .accessibilityAddTraits(.isHeader)
                    VStack(spacing: 0) {
                        row(.alerts, symbol: "bell", tint: Theme.coral, title: "Alerts",
                            subtitle: "Cameras, doorbell, locks, leaks",
                            badge: model.unseenAlerts)
                        Divider().overlay(Theme.divider)
                        row(.people, symbol: "person.2", tint: Theme.teal, title: "People",
                            subtitle: MoreLines.people(model.people))
                        Divider().overlay(Theme.divider)
                        row(.automations, symbol: "bolt", tint: Color(hex: 0xB79CFF), title: "Automations",
                            subtitle: model.automations.map(MoreLines.automations) ?? "What the house does by itself")
                    }
                    .glassCard(padding: 14)
                    VStack(spacing: 0) {
                        if model.canEditHome {
                            row(.editHome, symbol: "slider.horizontal.3", tint: Theme.blue, title: "Edit Home",
                                subtitle: "Cards and favourites")
                            Divider().overlay(Theme.divider)
                        }
                        Button { showMe = true } label: {
                            rowFace(symbol: nil, tint: Theme.blue, title: model.user?.name ?? "Account",
                                    subtitle: MoreLines.account)
                        }
                        .buttonStyle(.plain)
                    }
                    .glassCard(padding: 14)
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 32)
            }
            .refreshable {
                await model.reloadAll()
                await model.reloadAutomations()
            }
        }
        .task { await model.reloadAutomations() }
        .sheet(isPresented: $showMe) {
            MeScreen().environmentObject(model).environmentObject(PushManager.shared)
        }
    }

    private func row(_ route: MoreRoute, symbol: String, tint: Color, title: String, subtitle: String,
                     badge: Int = 0, warn: Bool = false) -> some View {
        NavigationLink(value: route) {
            rowFace(symbol: symbol, tint: tint, title: title, subtitle: subtitle, badge: badge, warn: warn)
        }
        .buttonStyle(.plain)
    }

    /// A row's look; no symbol = the account's initial.
    private func rowFace(symbol: String?, tint: Color, title: String, subtitle: String,
                         badge: Int = 0, warn: Bool = false) -> some View {
            HStack(spacing: 14) {
                Group {
                    if let symbol {
                        Image(systemName: symbol).font(.system(size: 19, weight: .semibold))
                    } else {
                        Text(String(title.prefix(1)).uppercased()).font(AppFont.manrope(18, .heavy))
                    }
                }
                    .foregroundColor(tint)
                    .frame(width: 44, height: 44)
                    .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(tint.opacity(0.18)))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(AppFont.manrope(16, .heavy)).foregroundColor(Theme.text)
                    Text(subtitle).font(AppFont.manrope(13, .bold))
                        .foregroundColor(warn ? Theme.coralText : Theme.muted)
                        .lineLimit(2)
                }
                Spacer(minLength: 8)
                if badge > 0 {
                    Text("\(badge)")
                        .font(AppFont.manrope(12, .heavy)).foregroundColor(.white)
                        .padding(.horizontal, 7)
                        .frame(minWidth: 24, minHeight: 24)
                        .background(Capsule().fill(Theme.red))
                        .accessibilityLabel("\(badge) new")
                }
                Image(systemName: "chevron.right").font(.system(size: 14, weight: .bold)).foregroundColor(Theme.dim)
                    .accessibilityHidden(true)
            }
            .frame(minHeight: 64)
            .contentShape(Rectangle())
    }
}
