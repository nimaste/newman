import SwiftUI
import UIKit

/// The wall's own settings (behind the corner hold + device authentication): server,
/// certificate, Cloudflare token, dim delay, brightness levels, reload.
struct WallSettingsView: View {
    @EnvironmentObject var model: WallModel
    @Environment(\.dismiss) private var dismiss
    @State private var changingServer = false
    @State private var serverText = WallSettingsView.currentServer
    @State private var hasCert = CertificateStore.shared.isConfigured
    @State private var certName = CertificateStore.shared.certificateName
    @State private var importing = false
    @State private var cfId = ConnectionStore.shared.cfClientId ?? ""
    @State private var cfSecret = ConnectionStore.shared.cfClientSecret ?? ""
    @State private var confirmClear = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Newman hub") {
                    LabeledContent("Server", value: serverText)
                    Button("Change server…") { changingServer = true }
                    Button("Reload the wall") {
                        model.reload()
                        dismiss()
                    }
                }

                Section {
                    LabeledContent("Certificate", value: hasCert ? (certName ?? "Installed") : "None")
                    if importing {
                        CertificateImportView { _ in
                            refreshCert()
                            importing = false
                        }
                    } else {
                        Button(hasCert ? "Replace certificate…" : "Import certificate…") { importing = true }
                    }
                    if hasCert {
                        Button("Remove certificate", role: .destructive) {
                            CertificateStore.shared.clear()
                            refreshCert()
                        }
                    }
                } header: {
                    Text("Client certificate")
                } footer: {
                    Text("Presented when the hub's proxy asks for one (away from home). At home it isn't needed.")
                }

                Section {
                    TextField("Client ID", text: $cfId).textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField("Client secret", text: $cfSecret)
                    Button("Save token") {
                        ConnectionStore.shared.setCloudflare(id: cfId, secret: cfSecret)
                        model.loadHome()
                    }
                } header: {
                    Text("Cloudflare Access (optional)")
                } footer: {
                    Text("Only if the hub is behind Cloudflare Access with a service token. Leave both empty to stop using it.")
                }

                Section {
                    Stepper(value: $model.dimMinutes, in: 0...Double(WallBrightness.maxDimMinutes), step: 1) {
                        Text(model.dimMinutes == 0 ? "Never dim" : "Dim after \(Int(model.dimMinutes)) min without a touch")
                    }
                    Toggle("Use the iPad's own brightness while awake", isOn: Binding(
                        get: { model.awakeBrightness < 0 },
                        set: { model.awakeBrightness = $0 ? WallBrightness.system : 0.8 }))
                    if model.awakeBrightness >= 0 {
                        VStack(alignment: .leading) {
                            Text("Awake brightness \(Int((model.awakeBrightness * 100).rounded()))%")
                            Slider(value: $model.awakeBrightness, in: WallBrightness.floor...1)
                        }
                    }
                    VStack(alignment: .leading) {
                        Text("Dimmed brightness \(Int((model.dimBrightness * 100).rounded()))% (\(Int(WallBrightness.floor * 100))% at the lowest, so a dimmed screen still looks on)")
                        Slider(value: $model.dimBrightness, in: WallBrightness.floor...WallBrightness.maxDim)
                    }
                } header: {
                    Text("Screen")
                } footer: {
                    Text("Dimmed, the wall shows the time softly; the first touch only wakes it. The screen stays on while the wall shows unless the wall page lets it sleep. For a true kiosk, turn on Guided Access (see the README).")
                }

                Section {
                    Button("Sign the wall out of Newman…", role: .destructive) { confirmClear = true }
                } footer: {
                    Text("Forgets the wall page's sign-in and stored data on this tablet. The certificate and server stay.")
                }

                Section {
                    LabeledContent("Version", value: model.version)
                    LabeledContent("Bridge", value: "window.NewmanWall")
                }
            }
            .sheet(isPresented: $changingServer, onDismiss: refreshConnection) {
                ServerSheet(onSaved: { model.serverChanged() }) { changingServer = false }
            }
            .navigationTitle("Newman Wall")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
            .confirmationDialog("Sign the wall out?", isPresented: $confirmClear, titleVisibility: .visible) {
                Button("Sign out", role: .destructive) {
                    Task {
                        await model.clearWebsiteData()
                        dismiss()
                    }
                }
            }
        }
        .onAppear { model.wake() }
    }

    private func refreshCert() {
        hasCert = CertificateStore.shared.isConfigured
        certName = CertificateStore.shared.certificateName
    }

    /// "Home · 10.1.1.2" - the hub's name and the address in use.
    private static var currentServer: String {
        let store = ConnectionStore.shared
        let name = store.hub?.name.trimmingCharacters(in: .whitespaces)
        let parts = [name?.isEmpty == false ? name : nil, store.serverURL?.host].compactMap { $0 }
        return parts.isEmpty ? "None" : parts.joined(separator: " · ")
    }

    /// After the Server screen: it may have changed the hub, certificate or Cloudflare token.
    private func refreshConnection() {
        serverText = WallSettingsView.currentServer
        refreshCert()
        cfId = ConnectionStore.shared.cfClientId ?? ""
        cfSecret = ConnectionStore.shared.cfClientSecret ?? ""
    }
}
