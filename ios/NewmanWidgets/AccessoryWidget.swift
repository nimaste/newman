import AppIntents
import SwiftUI
import WidgetKit

// Lock Screen widgets (circular, rectangular, inline): one device's state. Tapping runs its tap
// action when it has one (RunDeviceActionIntent, or RunSensitiveActionIntent - unlock first - for a
// sensitive one), else opens the app on that device.

struct DeviceAccessoryConfig: WidgetConfigurationIntent {
    static var title: LocalizedStringResource = "Device"
    static var description = IntentDescription("One Newman device on the Lock Screen.")

    @Parameter(title: "Device") var device: DeviceEntity?

    @Parameter(title: "Tap Runs Its Action", default: true) var tapRuns: Bool

    @Parameter(title: "Confirm First", description: "Ask before a sensitive action, such as unlocking or opening.",
               default: false)
    var confirmFirst: Bool

    init() {}
}

struct AccessoryEntry: TimelineEntry {
    var date: Date
    var device: WidgetDevice?
    var kind: Kind?
    var tapRuns: Bool
    var confirmFirst: Bool
    var stale = false

    static let sample = AccessoryEntry(
        date: Date(),
        device: WidgetDevice(id: "sample.lock", name: "Front door", state: "locked", stateLabel: "Locked", icon: "lock"),
        kind: nil, tapRuns: true, confirmFirst: false)
}

struct AccessoryProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> AccessoryEntry { .sample }

    func snapshot(for configuration: DeviceAccessoryConfig, in context: Context) async -> AccessoryEntry {
        if context.isPreview && configuration.device == nil { return .sample }
        return await Self.load(configuration)
    }

    func timeline(for configuration: DeviceAccessoryConfig, in context: Context) async -> Timeline<AccessoryEntry> {
        let entry = await Self.load(configuration)
        let next = WidgetLogic.nextRefresh(now: Date().timeIntervalSince1970, lastActionAt: WidgetHub.lastActionAt)
        return Timeline(entries: [entry], policy: .after(Date(timeIntervalSince1970: next)))
    }

    static func load(_ c: DeviceAccessoryConfig) async -> AccessoryEntry {
        guard let id = c.device?.id, WidgetHub.isSignedIn else {
            return AccessoryEntry(date: Date(), device: nil, kind: nil, tapRuns: c.tapRuns, confirmFirst: c.confirmFirst)
        }
        async let kindsCall = WidgetHub.kinds()
        var stale = false
        var device: WidgetDevice?
        do {
            device = try await WidgetHub.states(ids: [id]).first
        } catch {
            device = WidgetHub.cachedStates(ids: [id]).first
            stale = true
        }
        let kinds = await kindsCall
        return AccessoryEntry(date: Date(), device: device, kind: device?.kind.flatMap { kinds[$0] }, tapRuns: c.tapRuns,
                              confirmFirst: c.confirmFirst, stale: stale)
    }
}

/// The Lock Screen device widget, with WidgetKit push updates. The kind is unchanged from the first
/// version, so widgets already on a Lock Screen keep working.
struct DeviceAccessoryWidgetPushed: Widget {
    static let kind = "ca.nimaste.newman.device.accessory"

    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: Self.kind, intent: DeviceAccessoryConfig.self, provider: AccessoryProvider()) { entry in
            AccessoryView(entry: entry)
        }
        .configurationDisplayName("Device")
        .description("One Newman device's state; tap to run its action.")
        .supportedFamilies([.accessoryCircular, .accessoryRectangular, .accessoryInline])
        .pushHandler(NewmanWidgetPushHandler.self)
    }
}

struct AccessoryView: View {
    var entry: AccessoryEntry
    @Environment(\.widgetFamily) private var family

    var body: some View {
        content
            .accessibilityElement(children: .combine)
            .accessibilityLabel(entry.device?.name ?? "Newman")
            .accessibilityValue(spokenValue)
            .accessibilityHint(spokenHint)
            .containerBackground(for: .widget) { Color.clear }
            .widgetURL(entry.device.flatMap { URL(string: "newman://device?id=\(Self.query($0.id))") } ?? URL(string: "newman://open"))
    }

    static func query(_ s: String) -> String {
        s.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed.subtracting(CharacterSet(charactersIn: "&=+#"))) ?? s
    }

    @ViewBuilder private var content: some View {
        if let d = entry.device, entry.tapRuns, let a = WidgetLogic.tapAction(d, entry.kind) {
            if WidgetLogic.isSensitive(a, entry.kind) {
                // Unlock, open…: iOS asks to unlock the phone first (this is the Lock Screen).
                Button(intent: RunSensitiveActionIntent(device: d, action: a, confirmFirst: entry.confirmFirst)) {
                    face
                }
                .buttonStyle(.plain)
            } else {
                Button(intent: RunDeviceActionIntent(device: d, action: a)) {
                    face
                }
                .buttonStyle(.plain)
            }
        } else {
            face
        }
    }

    private var symbol: String {
        guard let d = entry.device else { return Symbols.name("home") }
        return Symbols.name(d.icon ?? entry.kind.map { DeviceLogic.kindIcon($0) })
    }

    private var state: String { entry.device.map { WidgetLogic.stateText($0) } ?? "Choose a device" }

    /// VoiceOver: the device and its state as the hub words it, then what a tap does.
    private var spokenValue: String { state + (entry.stale ? ", last known" : "") }
    private var spokenHint: String {
        guard entry.tapRuns, let d = entry.device, let a = WidgetLogic.tapAction(d, entry.kind) else { return "Opens Newman" }
        return a.label
    }

    @ViewBuilder private var face: some View {
        switch family {
        case .accessoryCircular:
            ZStack {
                AccessoryWidgetBackground()
                VStack(spacing: 1) {
                    Image(systemName: symbol).font(.system(size: 17, weight: .semibold))
                    Text(state).font(.system(size: 9, weight: .bold, design: .rounded)).lineLimit(1).minimumScaleFactor(0.6)
                }
                .padding(4)
            }
            .widgetAccentable()
        case .accessoryInline:
            Label(entry.device.map { "\($0.name) \(state)" } ?? state, systemImage: symbol)
        default:
            HStack(spacing: 8) {
                Image(systemName: symbol).font(.system(size: 22, weight: .semibold)).widgetAccentable()
                VStack(alignment: .leading, spacing: 1) {
                    Text(entry.device?.name ?? "Newman").font(.system(size: 14, weight: .bold, design: .rounded)).lineLimit(1)
                    Text(state).font(.system(size: 13, weight: .semibold, design: .rounded)).lineLimit(1)
                    if entry.tapRuns, let d = entry.device, let a = WidgetLogic.tapAction(d, entry.kind) {
                        Text(a.label).font(.system(size: 11, weight: .semibold, design: .rounded)).opacity(0.7).lineLimit(1)
                    }
                }
                Spacer(minLength: 0)
            }
            .opacity(entry.stale ? 0.7 : 1)
        }
    }
}
