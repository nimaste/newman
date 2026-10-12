import AppIntents
import SwiftUI
import WidgetKit

// "Home status": the house at a glance (GET /api/widgets/home - everything with a secure state, from the
// kind metadata). Green "All secure" with a detail line ("2 locks · garage door closed"); otherwise red,
// naming what isn't secure ("Side Door unlocked") with a button that fixes it (Lock, Close). The medium
// size adds the list (Locks: All locked / Garage door: Closed / Alarm: Disarmed) and up to two buttons.

struct HomeStatusConfig: WidgetConfigurationIntent {
    static var title: LocalizedStringResource = "Home Status"
    static var description = IntentDescription("Whether everything is locked and closed.")

    @Parameter(title: "Confirm First", description: "Ask before a sensitive action, such as opening.", default: false)
    var confirmFirst: Bool

    init() {}
}

struct HomeStatusEntry: TimelineEntry {
    var date: Date
    var home: WidgetHome?
    var confirmFirst: Bool
    var message: String?
    var stale = false

    static let sample = HomeStatusEntry(
        date: Date(),
        home: WidgetHome(secure: true, headline: "All secure", detail: "2 locks · garage door closed", problems: [],
                         groups: [WidgetHomeGroup(kind: "lock", label: "Locks", text: "All locked", secure: true, icon: "lock"),
                                  WidgetHomeGroup(kind: "garage", label: "Garage door", text: "Closed", secure: true, icon: "garage"),
                                  WidgetHomeGroup(kind: "alarm", label: "Alarm", text: "Disarmed", secure: nil, icon: "alarm")],
                         ids: []),
        confirmFirst: false)
}

struct HomeStatusProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> HomeStatusEntry { .sample }

    func snapshot(for configuration: HomeStatusConfig, in context: Context) async -> HomeStatusEntry {
        if context.isPreview && !WidgetHub.isSignedIn { return .sample }
        return await Self.load(configuration)
    }

    func timeline(for configuration: HomeStatusConfig, in context: Context) async -> Timeline<HomeStatusEntry> {
        let entry = await Self.load(configuration)
        let next = WidgetLogic.nextRefresh(now: Date().timeIntervalSince1970, lastActionAt: WidgetHub.lastActionAt)
        return Timeline(entries: [entry], policy: .after(Date(timeIntervalSince1970: next)))
    }

    static func load(_ configuration: HomeStatusConfig) async -> HomeStatusEntry {
        let confirm = configuration.confirmFirst
        guard WidgetHub.isSignedIn else {
            return HomeStatusEntry(date: Date(), home: nil, confirmFirst: confirm, message: "Sign in to Newman")
        }
        do {
            return HomeStatusEntry(date: Date(), home: try await WidgetHub.home(), confirmFirst: confirm)
        } catch {
            let err = error as? APIError
            if err?.status == 404 {
                return HomeStatusEntry(date: Date(), home: nil, confirmFirst: confirm,
                                       message: "Update the Newman hub to see this")
            }
            let kept = WidgetHub.cachedHome
            return HomeStatusEntry(date: Date(), home: kept, confirmFirst: confirm,
                                   message: kept == nil ? (err?.message ?? error.localizedDescription) : nil, stale: true)
        }
    }
}

struct HomeStatusWidget: Widget {
    static let kind = "ca.nimaste.newman.home"

    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: Self.kind, intent: HomeStatusConfig.self, provider: HomeStatusProvider()) { entry in
            HomeStatusView(entry: entry)
        }
        .configurationDisplayName("Home status")
        .description("All secure, or what isn't - with a tap to lock or close it.")
        .supportedFamilies([.systemSmall, .systemMedium])
        .pushHandler(NewmanWidgetPushHandler.self)
    }
}

struct HomeStatusView: View {
    var entry: HomeStatusEntry
    @Environment(\.widgetFamily) private var family

    private var secure: Bool? { entry.home?.secure }

    private var background: LinearGradient {
        let colors: [Color]
        switch secure {
        case .some(true): colors = [Color(hex: 0x0F6B55), Color(hex: 0x0A3F35)]
        case .some(false): colors = [Color(hex: 0x9B2C2C), Color(hex: 0x5C1616)]
        case .none: colors = [Theme.background, Theme.background]
        }
        return LinearGradient(colors: colors, startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    var body: some View {
        Group {
            if let home = entry.home, entry.message == nil {
                if family == .systemSmall {
                    small(home)
                } else {
                    medium(home)
                }
            } else {
                VStack(spacing: 6) {
                    Image(systemName: "house").font(.system(size: 22, weight: .semibold)).foregroundColor(Theme.teal)
                        .accessibilityHidden(true)
                    Text(entry.message ?? "").font(.system(size: 15, weight: .semibold, design: .rounded))
                        .foregroundColor(Theme.text).multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .foregroundColor(.white)
        .opacity(entry.stale ? 0.75 : 1)
        .containerBackground(for: .widget) { background }
        .widgetURL(URL(string: "newman://open"))
    }

    private var symbol: String {
        switch secure {
        case .some(true): return "checkmark.shield.fill"
        case .some(false): return "exclamationmark.shield.fill"
        case .none: return "shield"
        }
    }

    private func headline(_ home: WidgetHome, size: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Image(systemName: symbol).font(.system(size: 22, weight: .semibold)).accessibilityHidden(true)
            Spacer(minLength: 2)
            Text(home.headline)
                .font(.system(size: size, weight: .bold, design: .rounded))
                .lineLimit(2)
                .minimumScaleFactor(0.7)
            if let detail = home.detail, !detail.isEmpty {
                Text(detail)
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .opacity(0.85)
                    .lineLimit(2)
                    .minimumScaleFactor(0.8)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private func small(_ home: WidgetHome) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            headline(home, size: 20)
            if let p = home.fixable.first {
                FixButton(problem: p, confirmFirst: entry.confirmFirst, wide: true)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private func medium(_ home: WidgetHome) -> some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                headline(home, size: 22)
                let fixes = Array(home.fixable.prefix(2))
                if !fixes.isEmpty {
                    HStack(spacing: 6) {
                        ForEach(fixes) { p in
                            FixButton(problem: p, confirmFirst: entry.confirmFirst, wide: fixes.count == 1)
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            VStack(alignment: .leading, spacing: 6) {
                ForEach(Array(home.groupList.prefix(4).enumerated()), id: \.offset) { _, g in
                    VStack(alignment: .leading, spacing: 0) {
                        Text(g.label)
                            .font(.system(size: 12, weight: .semibold, design: .rounded))
                            .opacity(0.75)
                            .lineLimit(1)
                        Text(g.text)
                            .font(.system(size: 15, weight: .bold, design: .rounded))
                            .lineLimit(1)
                            .minimumScaleFactor(0.75)
                    }
                    .accessibilityElement(children: .combine)
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
    }
}

/// "Lock" / "Close": runs the problem's fix (sensitive ones authenticate first, like a device tile).
struct FixButton: View {
    var problem: WidgetHomeProblem
    var confirmFirst: Bool
    var wide: Bool

    var body: some View {
        if let fix = problem.fix {
            if fix.isSensitive {
                Button(intent: RunSensitiveActionIntent(device: problem.device, action: fix, confirmFirst: confirmFirst)) {
                    face(fix)
                }
                .buttonStyle(.plain)
            } else {
                Button(intent: RunDeviceActionIntent(device: problem.device, action: fix)) {
                    face(fix)
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func face(_ fix: WidgetAction) -> some View {
        Text(fix.label)
            .font(.system(size: 15, weight: .bold, design: .rounded))
            .lineLimit(1)
            .minimumScaleFactor(0.7)
            .foregroundColor(Color(hex: 0x5C1616))
            .padding(.vertical, 7)
            .padding(.horizontal, 12)
            .frame(maxWidth: wide ? .infinity : nil)
            .background(Capsule().fill(Color.white))
            .accessibilityLabel("\(fix.label) \(problem.name)")
    }
}
