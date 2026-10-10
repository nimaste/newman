import AppIntents
import Foundation

// The App Intents behind widget buttons, Control Center controls and Siri / Shortcuts. Compiled
// into the app and the widget extension (whichever the system runs them in); they talk to the hub
// through WidgetHub (shared Keychain: address, session, Cloudflare token, client certificate).
//
// Sensitive actions (the kind metadata's / hub's `sensitive` flag: unlock, open the garage,
// disarm…) never run from a locked phone. iOS's authentication policy is fixed per intent type, so
// they get their own types with `.requiresAuthentication` (RunSensitiveActionIntent, and Siri's
// Unlock / Open); the rest are `.alwaysAllowed` and refuse a sensitive command while the phone is
// locked (PhoneLock). Siri / Shortcuts never ask a confirmation question.

/// A failure in Siri's / the widget's words.
struct NewmanIntentError: Error, CustomLocalizedStringResourceConvertible {
    var message: String
    var localizedStringResource: LocalizedStringResource { "\(message)" }

    static func from(_ error: Error) -> NewmanIntentError {
        if let e = error as? NewmanIntentError { return e }
        if let e = error as? APIError { return NewmanIntentError(message: e.message) }
        return NewmanIntentError(message: error.localizedDescription)
    }
}

/// Whether the phone is unlocked right now, from any process (the app or the widget extension): a
/// tiny App Group file written with complete file protection can only be read while unlocked.
enum PhoneLock {
    static var isUnlocked: Bool {
        guard let dir = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: WidgetHub.appGroup) else {
            return false
        }
        let url = dir.appendingPathComponent("unlocked-probe")
        if (try? Data(contentsOf: url)) != nil { return true }
        // There but unreadable: locked. Missing: create it (only possible while unlocked).
        if FileManager.default.fileExists(atPath: url.path) { return false }
        do {
            try Data("1".utf8).write(to: url, options: [.atomic, .completeFileProtection])
        } catch {
            return false
        }
        return (try? Data(contentsOf: url)) != nil
    }
}

extension AppIntent {
    /// The widget's / control's "Confirm first" question before a sensitive action (throws if the
    /// person cancels). Those widgets and controls only exist on iOS 26 (NewmanWidgets), but the app
    /// (iOS 17) compiles these intents too: there it asks without the wording.
    func askFirst(_ question: IntentDialog) async throws {
        if #available(iOS 18.0, *) {
            try await requestConfirmation(dialog: question)
        } else {
            try await requestConfirmation()
        }
    }
}

/// Sends a widget tile's / control button's command and redraws the widgets.
enum DeviceActionRunner {
    static func run(deviceId: String, command: String) async throws {
        guard !deviceId.isEmpty, !command.isEmpty else {
            // A control without a device / action yet: just redraw.
            WidgetRefresh.reloadAll()
            return
        }
        do {
            try await WidgetHub.run(deviceId: deviceId, command: command)
        } catch {
            throw NewmanIntentError.from(error)
        }
        // A moment for the device to report its new state before the widget redraws.
        try? await Task.sleep(nanoseconds: 1_200_000_000)
        WidgetRefresh.reloadAll()
    }
}

/// A widget tile's / control button's action that isn't sensitive: one command to one device, runs
/// from the Lock Screen and Control Center without unlocking. The device, command and wording are
/// filled in by the widget from the hub's state, so running it needs no lookups.
struct RunDeviceActionIntent: AppIntent {
    static var title: LocalizedStringResource = "Run a Newman Device Action"
    static var isDiscoverable: Bool = false
    static var openAppWhenRun: Bool = false
    static let authenticationPolicy: IntentAuthenticationPolicy = .alwaysAllowed

    @Parameter(title: "Device ID") var deviceId: String
    @Parameter(title: "Device Name") var deviceName: String
    @Parameter(title: "Command") var command: String
    @Parameter(title: "Action") var label: String
    /// Set by a control whose device's action is sensitive (one control type can't switch intent
    /// types): then it refuses while the phone is locked instead of running.
    @Parameter(title: "Sensitive", default: false) var sensitive: Bool
    @Parameter(title: "Confirm First", default: false) var confirmFirst: Bool

    init() {}

    init(device: WidgetDevice, action: WidgetAction, sensitive: Bool = false, confirmFirst: Bool = false) {
        self.deviceId = device.id
        self.deviceName = device.name
        self.command = action.command
        self.label = action.label
        self.sensitive = sensitive
        self.confirmFirst = confirmFirst
    }

    func perform() async throws -> some IntentResult {
        if sensitive && !PhoneLock.isUnlocked {
            throw NewmanIntentError(message: "Unlock your iPhone to \(label.lowercased()) \(deviceName).")
        }
        if sensitive && confirmFirst && !deviceId.isEmpty && !command.isEmpty {
            try await askFirst("\(label) \(deviceName)?")
        }
        try await DeviceActionRunner.run(deviceId: deviceId, command: command)
        return .result()
    }
}

/// A widget tile's / control button's SENSITIVE action (unlock, open, disarm…): iOS asks for Face ID
/// / passcode first when it's run from a locked phone. "Confirm first" (the widget's / control's
/// own setting, off by default) asks before sending it.
struct RunSensitiveActionIntent: AppIntent {
    static var title: LocalizedStringResource = "Run a Sensitive Newman Device Action"
    static var isDiscoverable: Bool = false
    static var openAppWhenRun: Bool = false
    static let authenticationPolicy: IntentAuthenticationPolicy = .requiresAuthentication

    @Parameter(title: "Device ID") var deviceId: String
    @Parameter(title: "Device Name") var deviceName: String
    @Parameter(title: "Command") var command: String
    @Parameter(title: "Action") var label: String
    @Parameter(title: "Confirm First", default: false) var confirmFirst: Bool

    init() {}

    init(device: WidgetDevice, action: WidgetAction, confirmFirst: Bool) {
        self.deviceId = device.id
        self.deviceName = device.name
        self.command = action.command
        self.label = action.label
        self.confirmFirst = confirmFirst
    }

    func perform() async throws -> some IntentResult {
        if confirmFirst && !deviceId.isEmpty && !command.isEmpty {
            try await askFirst("\(label) \(deviceName)?")
        }
        try await DeviceActionRunner.run(deviceId: deviceId, command: command)
        return .result()
    }
}

/// "Turn on / off / open / close / lock / unlock <device>": the kind's command for that verb.
enum DeviceVerbRunner {
    /// `authenticated`: the calling intent is `.requiresAuthentication`, so iOS has already made sure
    /// the phone is unlocked. Otherwise a sensitive command is refused while it's locked.
    static func run(_ verb: DeviceVerb, _ device: DeviceEntity, authenticated: Bool = false) async throws -> String {
        do {
            async let kindsCall = WidgetHub.kinds()
            guard let d = try await WidgetHub.states(ids: [device.id]).first else {
                throw NewmanIntentError(message: "Newman can't find \(device.name).")
            }
            let kinds = await kindsCall
            guard d.isAvailable else { throw NewmanIntentError(message: "\(d.name) is offline.") }
            let kind = d.kind.flatMap { kinds[$0] }
            guard let cmd = WidgetLogic.command(verb, kind) else {
                throw NewmanIntentError(message: "\(d.name) can't do “\(verb.label.lowercased())”.")
            }
            let sensitive = WidgetLogic.isSensitive(command: cmd.id, d, kind)
            if WidgetLogic.mustUnlockFirst(sensitive: sensitive, intentAuthenticates: authenticated,
                                           phoneUnlocked: !sensitive || authenticated || PhoneLock.isUnlocked) {
                throw NewmanIntentError(message: "Unlock your iPhone to \(verb.label.lowercased()) \(d.name).")
            }
            try await WidgetHub.run(deviceId: d.id, command: cmd.id)
            WidgetRefresh.reloadAll()
            return "\(verb.past) \(d.name)."
        } catch {
            throw NewmanIntentError.from(error)
        }
    }
}

struct TurnOnDeviceIntent: AppIntent {
    static var title: LocalizedStringResource = "Turn On a Device"
    static var description = IntentDescription("Turns on a Newman device.")
    static let authenticationPolicy: IntentAuthenticationPolicy = .alwaysAllowed
    @Parameter(title: "Device") var device: DeviceEntity
    static var parameterSummary: some ParameterSummary { Summary("Turn on \(\.$device)") }
    init() {}
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let said = try await DeviceVerbRunner.run(.turnOn, device)
        return .result(dialog: "\(said)")
    }
}

struct TurnOffDeviceIntent: AppIntent {
    static var title: LocalizedStringResource = "Turn Off a Device"
    static var description = IntentDescription("Turns off a Newman device.")
    static let authenticationPolicy: IntentAuthenticationPolicy = .alwaysAllowed
    @Parameter(title: "Device") var device: DeviceEntity
    static var parameterSummary: some ParameterSummary { Summary("Turn off \(\.$device)") }
    init() {}
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let said = try await DeviceVerbRunner.run(.turnOff, device)
        return .result(dialog: "\(said)")
    }
}

/// Opening (the garage…) is sensitive: the phone must be unlocked.
struct OpenDeviceIntent: AppIntent {
    static var title: LocalizedStringResource = "Open a Device"
    static var description = IntentDescription("Opens a Newman garage door, cover or other device that opens.")
    static let authenticationPolicy: IntentAuthenticationPolicy = .requiresAuthentication
    @Parameter(title: "Device") var device: DeviceEntity
    static var parameterSummary: some ParameterSummary { Summary("Open \(\.$device)") }
    init() {}
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let said = try await DeviceVerbRunner.run(.open, device, authenticated: true)
        return .result(dialog: "\(said)")
    }
}

struct CloseDeviceIntent: AppIntent {
    static var title: LocalizedStringResource = "Close a Device"
    static var description = IntentDescription("Closes a Newman garage door, cover or other device that closes.")
    static let authenticationPolicy: IntentAuthenticationPolicy = .alwaysAllowed
    @Parameter(title: "Device") var device: DeviceEntity
    static var parameterSummary: some ParameterSummary { Summary("Close \(\.$device)") }
    init() {}
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let said = try await DeviceVerbRunner.run(.close, device)
        return .result(dialog: "\(said)")
    }
}

struct LockDeviceIntent: AppIntent {
    static var title: LocalizedStringResource = "Lock a Device"
    static var description = IntentDescription("Locks a Newman lock.")
    static let authenticationPolicy: IntentAuthenticationPolicy = .alwaysAllowed
    @Parameter(title: "Device") var device: DeviceEntity
    static var parameterSummary: some ParameterSummary { Summary("Lock \(\.$device)") }
    init() {}
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let said = try await DeviceVerbRunner.run(.lock, device)
        return .result(dialog: "\(said)")
    }
}

/// Unlocking is sensitive: the phone must be unlocked.
struct UnlockDeviceIntent: AppIntent {
    static var title: LocalizedStringResource = "Unlock a Device"
    static var description = IntentDescription("Unlocks a Newman lock.")
    static let authenticationPolicy: IntentAuthenticationPolicy = .requiresAuthentication
    @Parameter(title: "Device") var device: DeviceEntity
    static var parameterSummary: some ParameterSummary { Summary("Unlock \(\.$device)") }
    init() {}
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let said = try await DeviceVerbRunner.run(.unlock, device, authenticated: true)
        return .result(dialog: "\(said)")
    }
}

/// A Control Center switch: on/off for a device with an on/off pair (WidgetLogic.toggle). Runs while
/// locked; a sensitive on/off command is refused until the phone is unlocked. SetValueIntent is
/// iOS 18+ (this file is also compiled into the iOS 17 app).
@available(iOS 18.0, *)
struct SetDeviceOnIntent: SetValueIntent {
    static var title: LocalizedStringResource = "Turn a Device On or Off"
    static var isDiscoverable: Bool = false
    static let authenticationPolicy: IntentAuthenticationPolicy = .alwaysAllowed

    @Parameter(title: "Device") var device: DeviceEntity
    @Parameter(title: "On") var value: Bool

    init() {}

    init(device: DeviceEntity) {
        self.device = device
    }

    func perform() async throws -> some IntentResult {
        _ = try await DeviceVerbRunner.run(value ? .turnOn : .turnOff, device)
        return .result()
    }
}
