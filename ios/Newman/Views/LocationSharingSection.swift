import CoreLocation
import SwiftUI
import UIKit

/// "Share my location" on the Me screen (GET /api/me/location): hidden when the hub doesn't offer
/// it; a switch for "choice"; on and locked ("Required by Chris") for "required". Turning it on asks
/// iOS for location in two plain steps - "While Using the App", then "Always" - each explained
/// first. Without "Always" it still shares while Newman is open.
struct LocationSharingSection: View {
    @EnvironmentObject var model: AppModel
    @ObservedObject private var sharing = LocationSharing.shared
    @State private var explain: Step?
    @State private var busy = false

    private enum Step: Identifiable {
        case whenInUse, always, settings
        var id: Int { hashValue }
    }

    var body: some View {
        let sw = SharingSwitch.state(sharing.settings)
        Group {
            if sw.visible {
                VStack(alignment: .leading, spacing: 0) {
                    Toggle(isOn: Binding(get: { sw.on }, set: { flip($0) })) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Share my location").font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                            Text(note(sw)).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                        }
                    }
                    .tint(Theme.teal)
                    .disabled(sw.locked || busy)
                    .frame(minHeight: 60)
                    if sw.on && !sharing.hasAlways {
                        Divider().overlay(Theme.divider)
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(sharing.hasWhenInUse ? "Allow \"Always\"" : "Allow location").font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                                Text(sharing.hasWhenInUse ? "So sharing keeps working when Newman is closed" : "Needed so your household can see you")
                                    .font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                            }
                            Spacer()
                            Button("Allow") { startPermission() }.buttonStyle(PillButtonStyle(height: 38))
                        }
                        .frame(minHeight: 60)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 4)
                .background(RoundedRectangle(cornerRadius: 20, style: .continuous).fill(Theme.card))
                .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(Theme.cardBorder))
            }
        }
        .task { await sharing.refresh() }
        .alert(title(explain), isPresented: Binding(get: { explain != nil }, set: { if !$0 { explain = nil } }), presenting: explain) { step in
            switch step {
            case .whenInUse:
                Button("Continue") { Task { await askWhenInUse() } }
                Button("Not now", role: .cancel) {}
            case .always:
                Button("Continue") { sharing.requestAlways() }
                Button("Not now", role: .cancel) {}
            case .settings:
                Button("Open Settings") {
                    if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                }
                Button("Not now", role: .cancel) {}
            }
        } message: { step in
            switch step {
            case .whenInUse: Text(SharingSwitch.permissionReason(sharing.settings) + " iOS will ask next.")
            case .always: Text(SharingSwitch.backgroundReason + " On the next prompt choose \"Change to Always Allow\".")
            case .settings: Text("Location is off for Newman. Turn it on in Settings → Newman → Location (\"Always\" keeps sharing when Newman is closed).")
            }
        }
    }

    private func title(_ step: Step?) -> String {
        switch step {
        case .whenInUse: return sharing.settings?.mode == "required" ? "Your household requires location sharing" : "Share your location"
        case .always: return "Keep sharing in the background"
        case .settings: return "Location is off for Newman"
        case nil: return ""
        }
    }

    private func note(_ sw: SharingSwitch.State) -> String {
        guard sw.on else { return sw.note }
        if !sharing.hasWhenInUse { return sw.note + " · location permission is off" }
        if !sharing.hasAlways { return sw.note + " · only while Newman is open" }
        return sw.note
    }

    private func flip(_ on: Bool) {
        if on {
            // Ask iOS first; turn sharing on at the hub once location is allowed.
            if sharing.hasWhenInUse { enable(true) } else { pendingEnable = true; startPermission() }
        } else {
            enable(false)
        }
    }

    @State private var pendingEnable = false

    private func enable(_ on: Bool) {
        busy = true
        Task {
            do {
                try await sharing.setEnabled(on)
                if on && !sharing.hasAlways { explain = .always }
            } catch {
                model.show((error as? APIError)?.message ?? error.localizedDescription, error: true)
            }
            busy = false
        }
    }

    private func startPermission() {
        switch sharing.authorization {
        case .notDetermined: explain = .whenInUse
        case .denied, .restricted: explain = .settings
        case .authorizedWhenInUse: explain = .always
        default:
            if pendingEnable { pendingEnable = false; enable(true) }
        }
    }

    private func askWhenInUse() async {
        let status = await sharing.requestWhenInUse()
        let ok = status == .authorizedWhenInUse || status == .authorizedAlways
        if pendingEnable {
            pendingEnable = false
            if ok { enable(true) }
        } else if ok && status != .authorizedAlways {
            explain = .always
        }
    }
}
