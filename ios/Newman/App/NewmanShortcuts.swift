import AppIntents

/// Siri and Shortcuts: "Turn on <device> with Newman", "Open <garage> with Newman", "Lock <lock>
/// with Newman"… The device is any of the hub's devices (DeviceQuery); the command is the kind's
/// own for that verb (WidgetLogic.command). The app refreshes the device names Siri knows after
/// loading (updateAppShortcutParameters).
struct NewmanShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: TurnOnDeviceIntent(), phrases: [
            "Turn on \(\.$device) with \(.applicationName)",
            "\(.applicationName) turn on \(\.$device)",
        ], shortTitle: "Turn On", systemImageName: "power")
        AppShortcut(intent: TurnOffDeviceIntent(), phrases: [
            "Turn off \(\.$device) with \(.applicationName)",
            "\(.applicationName) turn off \(\.$device)",
        ], shortTitle: "Turn Off", systemImageName: "poweroff")
        AppShortcut(intent: OpenDeviceIntent(), phrases: [
            "Open \(\.$device) with \(.applicationName)",
            "\(.applicationName) open \(\.$device)",
        ], shortTitle: "Open", systemImageName: "door.garage.open")
        AppShortcut(intent: CloseDeviceIntent(), phrases: [
            "Close \(\.$device) with \(.applicationName)",
            "\(.applicationName) close \(\.$device)",
        ], shortTitle: "Close", systemImageName: "door.garage.closed")
        AppShortcut(intent: LockDeviceIntent(), phrases: [
            "Lock \(\.$device) with \(.applicationName)",
            "\(.applicationName) lock \(\.$device)",
        ], shortTitle: "Lock", systemImageName: "lock")
        AppShortcut(intent: UnlockDeviceIntent(), phrases: [
            "Unlock \(\.$device) with \(.applicationName)",
            "\(.applicationName) unlock \(\.$device)",
        ], shortTitle: "Unlock", systemImageName: "lock.open")
    }
}
