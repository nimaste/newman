import AppIntents
import SwiftUI
import WidgetKit

// iOS 18 controls (Control Center, Lock Screen, Action button): a switch for a device with an
// on/off pair, and a button for a one-shot action (the device's tap action right now: open /
// close the garage, lock / unlock…).

struct DeviceToggleConfig: ControlConfigurationIntent {
    static var title: LocalizedStringResource = "Newman Switch"
    @Parameter(title: "Device") var device: DeviceEntity?
    init() {}
}

struct DeviceButtonConfig: ControlConfigurationIntent {
    static var title: LocalizedStringResource = "Newman Button"
    @Parameter(title: "Device") var device: DeviceEntity?
    @Parameter(title: "Confirm First", description: "Ask before a sensitive action, such as unlocking or opening.",
               default: false)
    var confirmFirst: Bool
    init() {}
}

/// What a control shows now.
struct ControlValue {
    var device: DeviceEntity
    var state: WidgetDevice?
    var kind: Kind?
    var symbol: String
    var confirmFirst = false

    static func placeholder(_ entity: DeviceEntity?) -> ControlValue {
        ControlValue(device: entity ?? DeviceEntity(id: "", name: "Choose a device"), state: nil, kind: nil,
                     symbol: Symbols.name(entity?.icon ?? "home"))
    }

    static func load(_ entity: DeviceEntity?, confirmFirst: Bool = false) async -> ControlValue {
        guard let entity, !entity.id.isEmpty, WidgetHub.isSignedIn else { return placeholder(entity) }
        async let kindsCall = WidgetHub.kinds()
        let d = (try? await WidgetHub.states(ids: [entity.id]).first) ?? WidgetHub.cachedStates(ids: [entity.id]).first
        let kinds = await kindsCall
        let kind = d?.kind.flatMap { kinds[$0] }
        return ControlValue(device: entity, state: d, kind: kind,
                            symbol: Symbols.name(d?.icon ?? kind.map { DeviceLogic.kindIcon($0) } ?? entity.icon),
                            confirmFirst: confirmFirst)
    }

    var name: String { state?.name ?? device.name }
    var isOn: Bool { state.map { WidgetLogic.isOn($0, kind) } ?? false }
    var action: WidgetAction? { state.flatMap { WidgetLogic.tapAction($0, kind) } }
    var stateText: String { state.map { WidgetLogic.stateText($0) } ?? "" }
}

struct DeviceToggleProvider: AppIntentControlValueProvider {
    func previewValue(configuration: DeviceToggleConfig) -> ControlValue { .placeholder(configuration.device) }

    func currentValue(configuration: DeviceToggleConfig) async throws -> ControlValue {
        await ControlValue.load(configuration.device)
    }
}

struct DeviceButtonProvider: AppIntentControlValueProvider {
    func previewValue(configuration: DeviceButtonConfig) -> ControlValue { .placeholder(configuration.device) }

    func currentValue(configuration: DeviceButtonConfig) async throws -> ControlValue {
        await ControlValue.load(configuration.device, confirmFirst: configuration.confirmFirst)
    }
}

struct DeviceToggleControl: ControlWidget {
    static let kind = "ca.nimaste.newman.control.toggle"

    var body: some ControlWidgetConfiguration {
        AppIntentControlConfiguration(kind: Self.kind, provider: DeviceToggleProvider()) { value in
            ControlWidgetToggle(value.name, isOn: value.isOn, action: SetDeviceOnIntent(device: value.device)) { on in
                Label(value.state == nil ? "" : (on ? "On" : "Off"), systemImage: value.symbol)
            }
        }
        .displayName("Newman Switch")
        .description("Turn a Newman device on or off.")
    }
}

struct DeviceButtonControl: ControlWidget {
    static let kind = "ca.nimaste.newman.control.button"

    var body: some ControlWidgetConfiguration {
        AppIntentControlConfiguration(kind: Self.kind, provider: DeviceButtonProvider()) { value in
            Self.button(value)
        }
        .displayName("Newman Button")
        .description("Run a Newman device's action: open or close the garage, lock or unlock…")
    }

    /// The device's action right now (the one that isn't its current state). A sensitive one
    /// (unlock, open…) is a RunSensitiveActionIntent, so from the Lock Screen / Control Center iOS
    /// asks to unlock first; the rest run while locked. Without an action (offline, not set up)
    /// the button does nothing but redraw.
    static func button(_ value: ControlValue) -> some ControlWidgetTemplate {
        // One button type (the control builder takes no if/else): the intent itself refuses a
        // sensitive action while the phone is locked.
        ControlWidgetButton(action: plainIntent(value)) {
            label(value)
        }
    }

    static func label(_ value: ControlValue) -> some View {
        Label {
            Text(value.name)
            Text(value.action?.label ?? value.stateText)
        } icon: {
            Image(systemName: value.symbol)
        }
    }

    static func plainIntent(_ value: ControlValue) -> RunDeviceActionIntent {
        guard let d = value.state, let a = value.action else {
            let i = RunDeviceActionIntent()
            i.deviceId = ""
            i.deviceName = value.name
            i.command = ""
            i.label = ""
            return i
        }
        return RunDeviceActionIntent(device: d, action: a, sensitive: WidgetLogic.isSensitive(a, value.kind),
                                     confirmFirst: value.confirmFirst)
    }
}

