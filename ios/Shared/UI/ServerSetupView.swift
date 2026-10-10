import SwiftUI
import UIKit

/// Connect to your Newman - before (and without) signing in: hubs found on this network, the
/// hub's QR code or newman://connect link, or a typed address; the client certificate and
/// Cloudflare Access token; a test (GET /api/hello). Saves the hub's id, name, home and outside
/// addresses (on the phone HubRoute picks between them from then on; the wall stays on the
/// address that answered here). Shared by the phone and the wall.
struct ServerSetupView: View {
    /// A newman://connect link waiting to be shown (taken and cleared here).
    var link: Binding<HubCandidate?> = .constant(nil)
    /// Shown as "Back" when there's somewhere to go back to.
    var onClose: (() -> Void)?
    var saveTitle = "Continue"
    /// Called before a different hub replaces the one in use (the phone signs out of it).
    var leavingHub: (() async -> Void)?
    var onSaved: () -> Void

    @StateObject private var browser = HubBrowser()
    @State private var address = ServerSetupView.savedAddress
    /// From Bonjour or a link; nil means the typed address.
    @State private var candidate: HubCandidate?
    @State private var testing = false
    @State private var result: HubConnect.TestResult?
    @State private var tested: HubCandidate?
    @State private var scanning = false
    @State private var lookedAWhile = false
    @State private var showSecurity = false
    @State private var hasCert = CertificateStore.shared.isConfigured
    @State private var certName = CertificateStore.shared.certificateName
    @State private var cfId = ConnectionStore.shared.cfClientId ?? ""
    @State private var cfSecret = ConnectionStore.shared.cfClientSecret ?? ""
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// The security section opens with a short ease, or at once with Reduce Motion.
    private var revealAnimation: Animation? { reduceMotion ? nil : .easeOut(duration: 0.2) }

    private static var savedAddress: String {
        let store = ConnectionStore.shared
        return (store.hub?.outsideURL ?? store.hub?.localURL ?? store.serverURL)?.absoluteString ?? ""
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                if let onClose {
                    HStack {
                        Button { onClose() } label: { Label("Back", systemImage: "chevron.left") }.buttonStyle(PillButtonStyle())
                        Spacer()
                    }
                }
                Image("Logo")
                    .resizable()
                    .frame(width: 64, height: 64)
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                    .padding(.top, onClose == nil ? 40 : 0)
                    .accessibilityHidden(true)
                Text("Connect to your Newman").font(AppFont.sora(28)).foregroundColor(Theme.text)
                    .accessibilityAddTraits(.isHeader)
                Text("Pick your hub, scan its code or type its address.")
                    .font(AppFont.manrope(14, .semibold)).foregroundColor(Theme.muted)

                nearby

                Button { scanning = true } label: {
                    HStack {
                        Image(systemName: "qrcode.viewfinder")
                        Text("Scan QR code")
                        Spacer()
                    }
                }
                .buttonStyle(PillButtonStyle(height: 48))

                heading("Address")
                field("https://home.example.com", text: $address, keyboard: .URL)

                security

                outcome

                HStack {
                    Button(testing ? "Testing…" : "Test connection") { Task { await test() } }
                        .buttonStyle(PillButtonStyle(height: 50))
                        .disabled(testing || address.trimmingCharacters(in: .whitespaces).isEmpty)
                    Spacer()
                    Button(saveTitle) { Task { await save() } }
                        .buttonStyle(PillButtonStyle(height: 50, prominent: true))
                        .disabled(testing || address.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .padding(20)
        }
        .scrollDismissesKeyboard(.interactively)
        .onAppear {
            browser.start()
            takeLink()
        }
        .onDisappear { browser.stop() }
        .task {
            try? await Task.sleep(nanoseconds: 6_000_000_000)
            lookedAWhile = true
        }
        .onChange(of: link.wrappedValue) { takeLink() }
        .onChange(of: address) { _, text in
            // Typing replaces a hub picked from the list or a code.
            if let c = candidate, c.shown?.absoluteString != text { candidate = nil }
            if tested != currentCandidate() { result = nil }
        }
        .sheet(isPresented: $scanning) {
            QRScanSheet { code in
                scanning = false
                scanned(code)
            }
        }
    }

    // MARK: sections

    @ViewBuilder private var nearby: some View {
        heading("On this network")
        if browser.hubs.isEmpty {
            HStack(spacing: 10) {
                if !lookedAWhile { ProgressView().tint(Theme.muted) }
                Text(lookedAWhile ? "No hubs found on this network." : "Looking for hubs…")
                    .font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted)
                Spacer()
            }
            .frame(minHeight: 44)
        } else {
            VStack(spacing: 0) {
                ForEach(Array(browser.hubs.enumerated()), id: \.element.id) { index, hub in
                    if index > 0 { Divider().overlay(Theme.divider) }
                    Button { pick(hub) } label: {
                        HStack(spacing: 12) {
                            Image(systemName: "house.fill").foregroundColor(Theme.teal)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(hub.name).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                                Text(hub.address.map(Self.hostPort) ?? "Finding its address…")
                                    .font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                            }
                            Spacer()
                            if let c = hub.candidate, c == candidate {
                                Image(systemName: "checkmark.circle.fill").foregroundColor(Theme.teal)
                            }
                        }
                        .frame(minHeight: 56)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .disabled(hub.address == nil)
                    .accessibilityLabel(hub.name)
                    .accessibilityValue(hub.address.map(Self.hostPort) ?? "Finding its address")
                    .accessibilityAddTraits(hub.candidate != nil && hub.candidate == candidate ? .isSelected : [])
                }
            }
            .padding(.horizontal, 14)
            .background(RoundedRectangle(cornerRadius: 20, style: .continuous).fill(Theme.card))
            .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(Theme.cardBorder))
        }
    }

    @ViewBuilder private var security: some View {
        Button { withAnimation(revealAnimation) { showSecurity.toggle() } } label: {
            HStack {
                Image(systemName: "lock.shield")
                Text("Connection security")
                Spacer()
                Text(securitySummary).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                Image(systemName: showSecurity ? "chevron.up" : "chevron.down")
            }
        }
        .buttonStyle(PillButtonStyle(height: 48))
        .accessibilityLabel("Connection security")
        .accessibilityValue("\(securitySummary), \(showSecurity ? "shown" : "hidden")")
        .accessibilityHint(showSecurity ? "Hides the certificate and token" : "Shows the certificate and token")
        if showSecurity {
            VStack(alignment: .leading, spacing: 12) {
                heading("Client certificate")
                if hasCert {
                    HStack {
                        Label(certName ?? "Installed", systemImage: "checkmark.seal.fill")
                            .font(AppFont.manrope(15, .bold)).foregroundColor(Theme.teal).lineLimit(1)
                        Spacer()
                        Button("Remove") {
                            CertificateStore.shared.clear()
                            refreshCert()
                        }
                        .buttonStyle(PillButtonStyle(height: 38))
                    }
                }
                CertificateImportView { _ in refreshCert() }
                heading("Cloudflare Access token")
                field("Client ID", text: $cfId)
                field("Client secret", text: $cfSecret, secure: true)
            }
            .padding(14)
            .background(RoundedRectangle(cornerRadius: 20, style: .continuous).fill(Theme.card))
            .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(Theme.cardBorder))
        }
    }

    @ViewBuilder private var outcome: some View {
        switch result {
        case .ok(_, let active, let name):
            HStack(spacing: 10) {
                Image(systemName: "checkmark.circle.fill").foregroundColor(Theme.teal)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Found \(name)").font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                    Text(Self.hostPort(active)).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                }
            }
        case .failed(let message):
            Text(message).font(AppFont.manrope(14, .bold)).foregroundColor(Theme.coralText)
        case nil:
            EmptyView()
        }
    }

    private var securitySummary: String {
        var parts: [String] = []
        if hasCert { parts.append("Certificate") }
        if !cfId.trimmingCharacters(in: .whitespaces).isEmpty && !cfSecret.trimmingCharacters(in: .whitespaces).isEmpty {
            parts.append("Token")
        }
        return parts.isEmpty ? "None" : parts.joined(separator: " · ")
    }

    // MARK: actions

    @MainActor private func pick(_ hub: HubBrowser.Found) {
        guard let c = hub.candidate else { return }
        use(c)
    }

    @MainActor private func takeLink() {
        guard let waiting = link.wrappedValue else { return }
        link.wrappedValue = nil
        use(waiting)
    }

    @MainActor private func scanned(_ code: String) {
        if let link = ConnectLink.parse(code) {
            use(link)
        } else if code.lowercased().hasPrefix("http"), let url = ServerAddress.normalize(code) {
            candidate = nil
            address = url.absoluteString
            Task { await test() }
        } else {
            result = .failed("That code isn't a Newman code.")
        }
    }

    /// A hub from the list, a code or a link: show it and test it.
    @MainActor private func use(_ c: HubCandidate) {
        candidate = c
        address = c.shown?.absoluteString ?? ""
        result = nil
        Task { await test() }
    }

    @MainActor private func currentCandidate() -> HubCandidate? {
        if let candidate { return candidate }
        return ServerAddress.normalize(address).map { HubCandidate.typed($0) }
    }

    @discardableResult
    @MainActor private func test() async -> HubConnect.TestResult? {
        guard let c = currentCandidate() else {
            result = .failed(HubCheck.notAddress)
            return nil
        }
        // The test uses the security settings as typed.
        ConnectionStore.shared.setCloudflare(id: cfId, secret: cfSecret)
        testing = true
        let r = await HubConnect.test(c)
        testing = false
        tested = c
        result = r
        if case .failed(let message) = r,
           [HubCheck.needsCertificate, HubCheck.certificateRejected, HubCheck.needsCloudflare, HubCheck.cloudflareRejected].contains(message) {
            withAnimation(revealAnimation) { showSecurity = true }
        }
        return r
    }

    @MainActor private func save() async {
        var r = result
        if r == nil || tested != currentCandidate() { r = await test() }
        guard case .ok(let hub, let active, _)? = r else { return }
        let store = ConnectionStore.shared
        if let leavingHub, !HubPlan.isSameHub(oldHub: store.hub, oldAddress: store.serverURL, newHub: hub, newAddress: active) {
            await leavingHub()
        }
        store.hub = hub
        store.serverURL = active
        onSaved()
    }

    @MainActor private func refreshCert() {
        hasCert = CertificateStore.shared.isConfigured
        certName = CertificateStore.shared.certificateName
        result = nil
    }

    // MARK: pieces

    private static func hostPort(_ url: URL) -> String {
        guard let host = url.host else { return url.absoluteString }
        return url.port.map { "\(host):\($0)" } ?? host
    }

    private func heading(_ text: String) -> some View {
        Text(text).font(AppFont.manrope(13, .heavy)).foregroundColor(Theme.muted)
            .accessibilityAddTraits(.isHeader)
    }

    private func field(_ placeholder: String, text: Binding<String>, secure: Bool = false,
                       keyboard: UIKeyboardType = .default) -> some View {
        Group {
            if secure {
                SecureField(placeholder, text: text)
            } else {
                TextField(placeholder, text: text)
                    .keyboardType(keyboard)
            }
        }
        .textInputAutocapitalization(.never)
        .autocorrectionDisabled()
        .font(AppFont.manrope(16, .semibold))
        .foregroundColor(Theme.text)
        .padding(.horizontal, 14)
        .frame(minHeight: 50)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.control))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.controlBorder))
    }
}

/// The Server screen over a running app (phone: Me or a link; wall: its settings or a link).
struct ServerSheet: View {
    var link: Binding<HubCandidate?> = .constant(nil)
    var leavingHub: (() async -> Void)?
    var onSaved: () -> Void = {}
    var close: () -> Void

    var body: some View {
        ZStack {
            NightGlassBackground()
            ServerSetupView(link: link, onClose: close, saveTitle: "Save", leavingHub: leavingHub) {
                onSaved()
                close()
            }
        }
    }
}