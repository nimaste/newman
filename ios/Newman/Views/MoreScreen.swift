import SwiftUI

/// The screens under the More tab.
enum MoreRoute: Hashable {
    case alerts
    case devices
    case automations
    case automation(String)
}

/// The fifth tab: Alerts (with its count), every device, and the automations, each pushed in a
/// navigation stack (AppModel.morePath, so a notification can open More › Alerts).
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
                        case .automations: AutomationsScreen()
                        case .automation(let id): AutomationDetailScreen(automationId: id)
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

    var body: some View {
        let ents = model.allEntities
        ZStack {
            NightGlassBackground(teal: false)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Text("More").font(AppFont.sora(28)).foregroundColor(Theme.text)
                        .padding(.top, 12)
                        .accessibilityAddTraits(.isHeader)
                    VStack(spacing: 0) {
                        row(.alerts, symbol: "bell", tint: Theme.coral, title: "Alerts",
                            subtitle: "Doorbell, cameras, locks and everything else that happened",
                            badge: model.unseenAlerts)
                        Divider().overlay(Theme.divider)
                        row(.devices, symbol: "square.grid.2x2", tint: Theme.blue, title: "Devices",
                            subtitle: DeviceDirectory.countLine(ents),
                            warn: ents.contains(where: DeviceDirectory.needsAttention))
                        Divider().overlay(Theme.divider)
                        row(.automations, symbol: "bolt", tint: Color(hex: 0xB79CFF), title: "Automations",
                            subtitle: model.automations.map(AutomationList.countLine) ?? "What the house does by itself")
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
    }

    private func row(_ route: MoreRoute, symbol: String, tint: Color, title: String, subtitle: String,
                     badge: Int = 0, warn: Bool = false) -> some View {
        NavigationLink(value: route) {
            HStack(spacing: 14) {
                Image(systemName: symbol)
                    .font(.system(size: 19, weight: .semibold))
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
        .buttonStyle(.plain)
    }
}
