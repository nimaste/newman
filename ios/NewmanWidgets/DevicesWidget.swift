import AppIntents
import SwiftUI
import WidgetKit

// The home-screen device widgets, in two styles: "Devices – tiles" (icon, name, state) and
// "Devices – rows" (one full-width row per device: name and state). The person picks which devices,
// and how many, when editing the widget: up to 2 on a small one, 4 on a medium one, 8 on a large one,
// and the layout grows to fill it (one device fills the widget, two take half each…). A device that
// can be controlled is a button running its tap action (RunDeviceActionIntent, or
// RunSensitiveActionIntent - unlock first - for a sensitive one); a sensor opens the app on it.

struct DevicesConfig: WidgetConfigurationIntent {
    static var title: LocalizedStringResource = "Devices"
    static var description = IntentDescription("Newman devices to see and control.")

    @Parameter(title: "Devices", size: [.systemSmall: 2, .systemMedium: 4, .systemLarge: 8])
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
    /// Shown instead of the devices (not signed in, nothing chosen, hub unreachable with nothing kept).
    var message: String?
    /// The hub couldn't be reached: these are the last states seen.
    var stale = false

    static let sample = DevicesEntry(
        date: Date(),
        devices: [
            WidgetDevice(id: "sample.lock", name: "Front Door", state: "locked", stateLabel: "Locked", icon: "lock",
                         actions: [WidgetAction(command: "unlock", label: "Unlock", sensitive: true)], tone: "good"),
            WidgetDevice(id: "sample.light", name: "Kitchen", state: "on", stateLabel: "On", icon: "bulb",
                         actions: [WidgetAction(command: "turn_off", label: "Turn off")], tone: "active"),
            WidgetDevice(id: "sample.garage", name: "Garage Door", state: "closed", stateLabel: "Closed", icon: "garage",
                         actions: [WidgetAction(command: "open", label: "Open", sensitive: true)], tone: "good"),
            WidgetDevice(id: "sample.temp", name: "Living Room", icon: "thermometer",
                         reading: WidgetReading(value: .number(21.5), unit: "°C"), tone: ""),
        ],
        kinds: [:], confirmFirst: false)
}

extension WidgetFamily {
    /// The device widgets' size class (an iPad's extra large is laid out like a large one).
    var newmanSize: WidgetSize {
        switch self {
        case .systemSmall: return .small
        case .systemLarge, .systemExtraLarge: return .large
        default: return .medium
        }
    }
}

struct DevicesProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> DevicesEntry { .sample }

    func snapshot(for configuration: DevicesConfig, in context: Context) async -> DevicesEntry {
        if context.isPreview && (configuration.devices ?? []).isEmpty { return .sample }
        return await Self.load(configuration, family: context.family)
    }

    func timeline(for configuration: DevicesConfig, in context: Context) async -> Timeline<DevicesEntry> {
        let entry = await Self.load(configuration, family: context.family)
        let next = WidgetLogic.nextRefresh(now: Date().timeIntervalSince1970, lastActionAt: WidgetHub.lastActionAt)
        return Timeline(entries: [entry], policy: .after(Date(timeIntervalSince1970: next)))
    }

    static func load(_ configuration: DevicesConfig, family: WidgetFamily) async -> DevicesEntry {
        // A widget set up before the sizes' limits (four on a small one) shows its first ones.
        let ids = Array((configuration.devices ?? []).map(\.id).prefix(WidgetLogic.maxDevices(family.newmanSize)))
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
            return DevicesEntry(date: Date(), devices: devices, kinds: await kindsCall, confirmFirst: confirm,
                                message: devices.isEmpty ? "No devices chosen" : nil)
        } catch {
            let kept = WidgetHub.cachedStates(ids: ids)
            let text = (error as? APIError)?.message ?? error.localizedDescription
            return DevicesEntry(date: Date(), devices: kept, kinds: await kindsCall, confirmFirst: confirm,
                                message: kept.isEmpty ? text : nil, stale: true)
        }
    }
}

/// "Devices – tiles", with WidgetKit push updates: the hub pushes when devices change. The kind is
/// unchanged from the first (grid) version, so widgets already on a Home Screen keep working.
struct DevicesWidgetPushed: Widget {
    static let kind = "ca.nimaste.newman.devices"

    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: Self.kind, intent: DevicesConfig.self, provider: DevicesProvider()) { entry in
            DevicesWidgetView(entry: entry, style: .tiles)
        }
        .configurationDisplayName("Devices – tiles")
        .description("Large tiles for the devices you choose; tap one to run it.")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
        .pushHandler(NewmanWidgetPushHandler.self)
    }
}

/// "Devices – rows": one full-width row per device.
struct DeviceRowsWidget: Widget {
    static let kind = "ca.nimaste.newman.devices.rows"

    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: Self.kind, intent: DevicesConfig.self, provider: DevicesProvider()) { entry in
            DevicesWidgetView(entry: entry, style: .rows)
        }
        .configurationDisplayName("Devices – rows")
        .description("A row for each device you choose: its name and state; tap one to run it.")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
        .pushHandler(NewmanWidgetPushHandler.self)
    }
}

// MARK: views

enum DevicesStyle {
    case tiles, rows
}

/// A state's colour (WidgetLogic.tone): secure green, on / not secure amber, a leak red, else grey.
enum WidgetPalette {
    static func ink(_ tone: WidgetTone) -> Color {
        switch tone {
        case .good: return Theme.teal
        case .active: return Theme.amber
        case .alert: return Theme.red
        case .offline: return Theme.faint
        case .neutral: return Theme.muted
        }
    }

    static func fill(_ tone: WidgetTone) -> Color {
        switch tone {
        case .active: return Theme.amber.opacity(0.16)
        case .alert: return Theme.red.opacity(0.2)
        default: return Theme.card
        }
    }
}

struct DevicesWidgetView: View {
    var entry: DevicesEntry
    var style: DevicesStyle
    @Environment(\.widgetFamily) private var family

    private var size: WidgetSize { family.newmanSize }
    private var shown: [WidgetDevice] { Array(entry.devices.prefix(WidgetLogic.maxDevices(size))) }

    var body: some View {
        Group {
            if let m = entry.message ?? (shown.isEmpty ? "No devices chosen" : nil) {
                VStack(spacing: 6) {
                    Image(systemName: "house").font(.system(size: 22, weight: .semibold)).foregroundColor(Theme.teal)
                        .accessibilityHidden(true)
                    Text(m).font(.system(size: 15, weight: .semibold, design: .rounded))
                        .foregroundColor(Theme.text).multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if style == .rows {
                rows
            } else {
                tiles
            }
        }
        .containerBackground(for: .widget) { Theme.background }
        .widgetURL(openURL)
    }

    /// A small widget can't hold links: tapping outside a button opens the app (on the device, when there's one).
    private var openURL: URL? {
        if shown.count == 1, let url = WidgetLogic.deviceURL(shown[0].id) { return url }
        return URL(string: "newman://open")
    }

    private func kind(_ d: WidgetDevice) -> Kind? { d.kind.flatMap { entry.kinds[$0] } }

    private static func slice(_ list: [WidgetDevice], line: Int, lines: [Int]) -> [WidgetDevice] {
        let start = lines.prefix(line).reduce(0, +)
        return Array(list[start..<min(start + lines[line], list.count)])
    }

    private var tiles: some View {
        let list = shown
        let lines = WidgetLogic.lines(count: list.count,
                                      columns: WidgetLogic.tileColumns(count: list.count, wide: size == .medium))
        let gap: CGFloat = 6
        return GeometryReader { geo in
            let lineCount = CGFloat(max(lines.count, 1))
            let tileHeight = (geo.size.height - gap * (lineCount - 1)) / lineCount
            let compact = WidgetLogic.tileCompact(tileHeight: Double(tileHeight))
            VStack(spacing: gap) {
                ForEach(Array(lines.indices), id: \.self) { i in
                    HStack(spacing: gap) {
                        ForEach(Array(Self.slice(list, line: i, lines: lines).enumerated()), id: \.offset) { _, d in
                            DeviceCell(device: d, kind: kind(d), confirmFirst: entry.confirmFirst, canLink: size != .small) {
                                DeviceTileFace(device: d, kind: kind(d), stale: entry.stale, compact: compact,
                                               big: list.count == 1)
                            }
                        }
                    }
                    .frame(maxHeight: .infinity)
                }
            }
        }
    }

    private var rows: some View {
        let list = shown
        let gap: CGFloat = list.count > 4 ? 4 : 6
        return GeometryReader { geo in
            let n = CGFloat(max(list.count, 1))
            let rowHeight = (geo.size.height - gap * (n - 1)) / n
            let stacked = WidgetLogic.rowStacked(rowHeight: Double(rowHeight))
            VStack(spacing: gap) {
                ForEach(Array(list.enumerated()), id: \.offset) { _, d in
                    DeviceCell(device: d, kind: kind(d), confirmFirst: entry.confirmFirst, canLink: size != .small) {
                        DeviceRowFace(device: d, kind: kind(d), stale: entry.stale, stacked: stacked, big: list.count == 1)
                    }
                    .frame(maxHeight: .infinity)
                }
            }
        }
    }
}

/// A device's tap: its action as a button (sensitive ones authenticate first), else a link to it in
/// the app (medium and large widgets; a small one opens the app through its widget URL).
struct DeviceCell<Content: View>: View {
    var device: WidgetDevice
    var kind: Kind?
    var confirmFirst: Bool
    var canLink: Bool
    @ViewBuilder var content: () -> Content

    var body: some View {
        if let a = WidgetLogic.tapAction(device, kind) {
            if WidgetLogic.isSensitive(a, kind) {
                // Unlock, open…: iOS asks to unlock the phone first when it's run from a locked one.
                Button(intent: RunSensitiveActionIntent(device: device, action: a, confirmFirst: confirmFirst)) {
                    content()
                }
                .buttonStyle(.plain)
            } else {
                Button(intent: RunDeviceActionIntent(device: device, action: a)) {
                    content()
                }
                .buttonStyle(.plain)
            }
        } else if canLink, let url = WidgetLogic.deviceURL(device.id) {
            Link(destination: url) {
                content()
            }
        } else {
            content()
        }
    }
}

/// Name and state, the state in its tone's colour; VoiceOver reads "Front Door, Locked, Unlock".
private struct DeviceText: View {
    var device: WidgetDevice
    var kind: Kind?
    var big: Bool
    var nameLines: Int

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            DeviceName(device: device, big: big, lines: nameLines)
            DeviceState(device: device, kind: kind, big: big)
        }
    }
}

private struct DeviceName: View {
    var device: WidgetDevice
    var big: Bool
    var lines: Int

    var body: some View {
        Text(device.name)
            .font(.system(size: big ? 22 : 16, weight: .bold, design: .rounded))
            .foregroundColor(Theme.text)
            .lineLimit(lines)
            .minimumScaleFactor(0.7)
    }
}

private struct DeviceState: View {
    var device: WidgetDevice
    var kind: Kind?
    var big: Bool

    var body: some View {
        Text(WidgetLogic.stateText(device))
            .font(.system(size: big ? 19 : 15, weight: .semibold, design: .rounded))
            .foregroundColor(WidgetPalette.ink(WidgetLogic.tone(device, kind)))
            .lineLimit(1)
            .minimumScaleFactor(0.7)
    }
}

extension View {
    /// One accessibility element per device: its name, its state, then what a tap does.
    func deviceAccessibility(_ device: WidgetDevice, _ kind: Kind?, stale: Bool) -> some View {
        accessibilityElement(children: .ignore)
            .accessibilityLabel(device.name)
            .accessibilityValue(WidgetLogic.stateText(device) + (stale ? ", last known" : ""))
            .accessibilityHint(WidgetLogic.tapAction(device, kind)?.label ?? "Opens Newman")
    }
}

struct DeviceTileFace: View {
    var device: WidgetDevice
    var kind: Kind?
    var stale: Bool
    /// Too short to stack: the icon goes beside the name and state.
    var compact: Bool
    /// The only device: everything larger.
    var big: Bool

    private var tone: WidgetTone { WidgetLogic.tone(device, kind) }

    private var icon: some View {
        Image(systemName: Symbols.name(device.icon ?? kind.map { DeviceLogic.kindIcon($0) }))
            .font(.system(size: big ? 30 : (compact ? 20 : 22), weight: .semibold))
            .foregroundColor(WidgetPalette.ink(tone))
    }

    var body: some View {
        Group {
            if compact {
                HStack(spacing: 10) {
                    icon
                    DeviceText(device: device, kind: kind, big: big, nameLines: 1)
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 12)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            } else {
                VStack(alignment: .leading, spacing: 2) {
                    icon
                    Spacer(minLength: 2)
                    DeviceText(device: device, kind: kind, big: big, nameLines: big ? 2 : 3)
                }
                .padding(10)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(WidgetPalette.fill(tone)))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(Theme.cardBorder, lineWidth: 1))
        .opacity(stale || tone == .offline ? 0.6 : 1)
        .deviceAccessibility(device, kind, stale: stale)
    }
}

struct DeviceRowFace: View {
    var device: WidgetDevice
    var kind: Kind?
    var stale: Bool
    /// Tall enough for the state under the name; otherwise it sits on the right.
    var stacked: Bool
    var big: Bool

    private var tone: WidgetTone { WidgetLogic.tone(device, kind) }

    var body: some View {
        Group {
            if stacked {
                DeviceText(device: device, kind: kind, big: big, nameLines: 2)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            } else {
                HStack(spacing: 8) {
                    DeviceName(device: device, big: big, lines: 1)
                    Spacer(minLength: 4)
                    DeviceState(device: device, kind: kind, big: big)
                        .layoutPriority(1)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .padding(.horizontal, 12)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(WidgetPalette.fill(tone)))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Theme.cardBorder, lineWidth: 1))
        .opacity(stale || tone == .offline ? 0.6 : 1)
        .deviceAccessibility(device, kind, stale: stale)
    }
}
