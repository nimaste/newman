import AppIntents
import SwiftUI
import WidgetKit

// The home-screen widget: a grid of device tiles (icon, name, state). A tile that can be
// controlled is a button running the device's tap action (RunDeviceActionIntent, or
// RunSensitiveActionIntent - unlock first - for a sensitive one); a sensor shows
// its reading. Small 2×2, medium 4×2, large 4×4 tiles.

struct DevicesConfig: WidgetConfigurationIntent {
    static var title: LocalizedStringResource = "Devices"
    static var description = IntentDescription("Newman devices to see and control.")

    @Parameter(title: "Devices", size: [.systemSmall: 4, .systemMedium: 8, .systemLarge: 16])
    var devices: [DeviceEntity]?

    @Parameter(title: "Confirm First", description: "Ask before a sensitive action, such as unlocking or opening.",
               default: false)
    var confirmFirst: Bool

    init() {}
}

struct DevicesEntry: TimelineEntry {
    var date: Date
    var devices: [WidgetDevice]
    var kinds: [String: Kind]
    var confirmFirst: Bool
    /// Shown instead of the tiles (not signed in, nothing chosen, hub unreachable with nothing kept).
    var message: String?
    /// The hub couldn't be reached: these are the last states seen.
    var stale = false

    static let sample = DevicesEntry(
        date: Date(),
        devices: [
            WidgetDevice(id: "sample.light", name: "Kitchen", state: "on", stateLabel: "On", icon: "bulb",
                         actions: [WidgetAction(command: "turn_off", label: "Turn off")]),
            WidgetDevice(id: "sample.lock", name: "Front door", state: "locked", stateLabel: "Locked", icon: "lock",
                         actions: [WidgetAction(command: "unlock", label: "Unlock", sensitive: true)]),
            WidgetDevice(id: "sample.garage", name: "Garage", state: "closed", stateLabel: "Closed", icon: "garage",
                         actions: [WidgetAction(command: "open", label: "Open", sensitive: true)]),
            WidgetDevice(id: "sample.temp", name: "Living room", icon: "thermometer",
                         reading: WidgetReading(value: .number(21.5), unit: "°C")),
        ],
        kinds: [:], confirmFirst: false)
}

struct DevicesProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> DevicesEntry { .sample }

    func snapshot(for configuration: DevicesConfig, in context: Context) async -> DevicesEntry {
        if context.isPreview && (configuration.devices ?? []).isEmpty { return .sample }
        return await Self.load(configuration)
    }

    func timeline(for configuration: DevicesConfig, in context: Context) async -> Timeline<DevicesEntry> {
        let entry = await Self.load(configuration)
        let next = WidgetLogic.nextRefresh(now: Date().timeIntervalSince1970, lastActionAt: WidgetHub.lastActionAt)
        return Timeline(entries: [entry], policy: .after(Date(timeIntervalSince1970: next)))
    }

    static func load(_ configuration: DevicesConfig) async -> DevicesEntry {
        let ids = (configuration.devices ?? []).map(\.id)
        let confirm = configuration.confirmFirst
        guard WidgetHub.isSignedIn else {
            return DevicesEntry(date: Date(), devices: [], kinds: [:], confirmFirst: confirm, message: "Sign in to Newman")
        }
        guard !ids.isEmpty else {
            return DevicesEntry(date: Date(), devices: [], kinds: [:], confirmFirst: confirm, message: "No devices chosen")
        }
        async let kindsCall = WidgetHub.kinds()
        do {
            let devices = try await WidgetHub.states(ids: ids)
            return DevicesEntry(date: Date(), devices: devices, kinds: await kindsCall, confirmFirst: confirm)
        } catch {
            let kept = WidgetHub.cachedStates(ids: ids)
            let text = (error as? APIError)?.message ?? error.localizedDescription
            return DevicesEntry(date: Date(), devices: kept, kinds: await kindsCall, confirmFirst: confirm,
                                message: kept.isEmpty ? text : nil, stale: true)
        }
    }
}

/// The devices widget, with WidgetKit push updates: the hub pushes when devices change. The kind
/// is unchanged from the first version, so widgets already on a Home Screen keep working.
struct DevicesWidgetPushed: Widget {
    static let kind = "ca.nimaste.newman.devices"

    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: Self.kind, intent: DevicesConfig.self, provider: DevicesProvider()) { entry in
            DevicesWidgetView(entry: entry)
        }
        .configurationDisplayName("Devices")
        .description("See and control your Newman devices.")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
        .pushHandler(NewmanWidgetPushHandler.self)
    }
}

// MARK: views

struct DevicesWidgetView: View {
    var entry: DevicesEntry
    @Environment(\.widgetFamily) private var family

    private var columns: Int { family == .systemSmall ? 2 : 4 }
    private var rows: Int { family == .systemLarge ? 4 : 2 }

    var body: some View {
        Group {
            if let m = entry.message {
                VStack(spacing: 6) {
                    Image(systemName: "house").font(.system(size: 22, weight: .semibold)).foregroundColor(Theme.teal)
                        .accessibilityHidden(true)
                    Text(m).font(.system(size: 13, weight: .semibold, design: .rounded))
                        .foregroundColor(Theme.text).multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                grid
            }
        }
        .containerBackground(for: .widget) { Theme.background }
        .widgetURL(URL(string: "newman://open"))
    }

    private var grid: some View {
        let shown = Array(entry.devices.prefix(columns * rows))
        let lines = stride(from: 0, to: max(shown.count, 1), by: columns).map { Array(shown[$0..<min($0 + columns, shown.count)]) }
        return VStack(spacing: 6) {
            ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                HStack(spacing: 6) {
                    ForEach(line) { d in
                        DeviceTile(device: d, kind: d.kind.flatMap { entry.kinds[$0] }, confirmFirst: entry.confirmFirst,
                                   stale: entry.stale)
                    }
                    if line.count < columns {
                        ForEach(0..<(columns - line.count), id: \.self) { _ in Color.clear.frame(maxWidth: .infinity) }
                    }
                }
                .frame(maxHeight: .infinity)
            }
            if lines.count < rows && family != .systemSmall {
                Spacer(minLength: 0)
            }
        }
    }
}

struct DeviceTile: View {
    var device: WidgetDevice
    var kind: Kind?
    var confirmFirst: Bool
    var stale: Bool

    var body: some View {
        if let a = WidgetLogic.tapAction(device, kind) {
            if WidgetLogic.isSensitive(a, kind) {
                // Unlock, open…: iOS asks to unlock the phone first when it's run from a locked one.
                Button(intent: RunSensitiveActionIntent(device: device, action: a, confirmFirst: confirmFirst)) {
                    face
                }
                .buttonStyle(.plain)
            } else {
                Button(intent: RunDeviceActionIntent(device: device, action: a)) {
                    face
                }
                .buttonStyle(.plain)
            }
        } else {
            face
        }
    }

    private var lit: Bool { WidgetLogic.isLit(device, kind) }
    private var warning: Bool { WidgetLogic.isWarning(device, kind) }

    private var tint: Color {
        if !device.isAvailable { return Theme.faint }
        if warning { return Theme.amber }
        return lit ? Theme.amberInk : Theme.teal
    }

    private var face: some View {
        VStack(alignment: .leading, spacing: 2) {
            Image(systemName: Symbols.name(device.icon ?? kind.map { DeviceLogic.kindIcon($0) }))
                .font(.system(size: 15, weight: .semibold))
                .foregroundColor(tint)
            Spacer(minLength: 0)
            Text(device.name)
                .font(.system(size: 11, weight: .bold, design: .rounded))
                .foregroundColor(lit ? Theme.amberInk : Theme.text)
                .lineLimit(2)
                .minimumScaleFactor(0.8)
            Text(WidgetLogic.stateText(device))
                .font(.system(size: 10, weight: .semibold, design: .rounded))
                .foregroundColor(lit ? Theme.amberInk.opacity(0.8) : (warning ? Theme.amber : Theme.muted))
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .padding(7)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(lit ? Theme.amber : Theme.card))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Theme.cardBorder, lineWidth: 1))
        .opacity(stale || !device.isAvailable ? 0.6 : 1)
        // VoiceOver: "Front door, Locked, Unlock" (the hub's state wording, then what a tap does).
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(device.name)
        .accessibilityValue(WidgetLogic.stateText(device) + (stale ? ", last known" : ""))
        .accessibilityHint(WidgetLogic.tapAction(device, kind)?.label ?? "")
    }
}
