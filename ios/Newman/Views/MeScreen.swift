import PhotosUI
import SwiftUI
import UIKit

/// The account screen (PhoneMe board): who you are, home/away and certificate status, the
/// server, the certificate (import / replace / remove), Cloudflare token, Face ID for sensitive
/// actions, notifications, and signing out of this phone.
struct MeScreen: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var push: PushManager
    @Environment(\.dismiss) private var dismiss
    @State private var importing = false
    @State private var certName = CertificateStore.shared.certificateName
    @State private var hasCert = CertificateStore.shared.isConfigured
    @State private var confirmSignOut = false
    @State private var confirmRemoveCert = false
    @State private var editingCF = false
    @State private var cfId = ConnectionStore.shared.cfClientId ?? ""
    @State private var cfSecret = ConnectionStore.shared.cfClientSecret ?? ""
    @State private var changingServer = false
    @State private var serverText = MeScreen.currentServer

    var body: some View {
        ZStack {
            NightGlassBackground(teal: false)
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        Button { dismiss() } label: { Label("Back", systemImage: "chevron.left") }.buttonStyle(PillButtonStyle())
                        Spacer()
                    }
                    identity
                    MyLookSection()
                    statusSection
                    securitySection
                    LocationSharingSection()
                    connectionSection
                    notificationsSection
                    Button(role: .destructive) { confirmSignOut = true } label: {
                        Text("Sign out of this phone").frame(maxWidth: .infinity)
                    }
                    .font(AppFont.manrope(15, .heavy))
                    .foregroundColor(Theme.coralText)
                    .frame(minHeight: 52)
                    .background(RoundedRectangle(cornerRadius: 16).fill(Theme.coral.opacity(0.08)))
                    .overlay(RoundedRectangle(cornerRadius: 16).stroke(Theme.coral.opacity(0.4)))
                    Text("Newman \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "") · hub \(model.meta?.version ?? "?")")
                        .font(AppFont.manrope(12, .bold)).foregroundColor(Theme.dim).frame(maxWidth: .infinity)
                }
                .padding(16)
            }
        }
        .task {
            await model.reloadAuthState()
            await push.refreshAuthorization()
            if model.peopleStatus == .loading { await model.reloadPeople() }
        }
        .confirmationDialog("Sign out of this phone?", isPresented: $confirmSignOut, titleVisibility: .visible) {
            Button("Sign out", role: .destructive) {
                Task {
                    await model.signOut()
                    dismiss()
                }
            }
        } message: {
            Text("Your certificate and server stay on this phone.")
        }
        .confirmationDialog("Remove the certificate?", isPresented: $confirmRemoveCert, titleVisibility: .visible) {
            Button("Remove", role: .destructive) {
                CertificateStore.shared.clear()
                refreshCert()
            }
        } message: {
            Text("Away from home you may not be able to reach Newman without it.")
        }
        .sheet(isPresented: $importing) {
            ZStack {
                Theme.background.ignoresSafeArea()
                VStack(alignment: .leading, spacing: 14) {
                    Text(hasCert ? "Replace certificate" : "Import certificate").font(AppFont.sora(22)).foregroundColor(Theme.text)
                        .accessibilityAddTraits(.isHeader)
                    Text("Choose the .p12 file and type its password. It's checked before it replaces anything.")
                        .font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted)
                    CertificateImportView { _ in
                        refreshCert()
                        importing = false
                        model.show("Certificate saved")
                        Task { await model.reloadAuthState() }
                    }
                    Spacer()
                }
                .padding(20)
            }
            .presentationDetents([.medium])
        }
    }

    private var identity: some View {
        HStack(spacing: 14) {
            if let me = model.mePerson {
                PersonAvatar(person: me, index: model.people.firstIndex { $0.id == me.id } ?? 0, size: 60)
            } else {
                Text(PeopleText.initial(model.user?.name ?? "N"))
                    .font(AppFont.manrope(24, .heavy))
                    .foregroundColor(Theme.background)
                    .frame(width: 60, height: 60)
                    .background(Circle().fill(Theme.blue))
                    .cappedDynamicType(.xxLarge)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(model.user?.name ?? model.user?.username ?? "Signed in").font(AppFont.sora(24)).foregroundColor(Theme.text)
                Text([model.user?.role.map { $0.capitalized }, model.authState?.connectionText].compactMap { $0 }.joined(separator: " · "))
                    .font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted)
            }
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }

    private var statusSection: some View {
        section {
            row("Where", value: model.authState.map { $0.isHome ? "At home" : "Away from home" } ?? "…")
            divider
            row("Client certificate", value: model.authState.map { $0.hasCertificate ? "Presented" : ($0.isHome ? "Not needed at home" : "Not presented") } ?? "…")
            divider
            row("Live updates", value: model.liveStatus == .open ? "Connected" : (model.liveStatus == .down ? "Reconnecting…" : "Connecting…"))
        }
    }

    private var securitySection: some View {
        section {
            Toggle(isOn: $model.biometricsEnabled) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(DeviceAuth.methodName) to unlock, open or disarm").font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                    Text("On this phone").font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                }
            }
            .tint(Theme.teal)
            .frame(minHeight: 60)
        }
    }

    private var connectionSection: some View {
        section {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Server").font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                    Text(serverText).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                }
                .accessibilityElement(children: .combine)
                Spacer()
                Button("Change") { changingServer = true }.buttonStyle(PillButtonStyle(height: 38))
                    .accessibilityLabel("Change server")
            }
            .frame(minHeight: 60)
            .sheet(isPresented: $changingServer, onDismiss: refreshConnection) {
                ServerSheet(leavingHub: { await model.leaveHub() }, onSaved: { model.serverChanged() }) { changingServer = false }
            }
            divider
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Certificate").font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                    Text(hasCert ? (certName ?? "Installed") : "None").font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                }
                .accessibilityElement(children: .combine)
                Spacer()
                Button(hasCert ? "Replace" : "Import") { importing = true }.buttonStyle(PillButtonStyle(height: 38))
                    .accessibilityLabel(hasCert ? "Replace certificate" : "Import certificate")
                if hasCert {
                    Button("Remove") { confirmRemoveCert = true }.buttonStyle(PillButtonStyle(height: 38))
                        .accessibilityLabel("Remove certificate")
                }
            }
            .frame(minHeight: 60)
            divider
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Cloudflare Access token").font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                        Text(ConnectionStore.shared.hasCloudflareToken ? "Set" : "Not used").font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                    }
                    .accessibilityElement(children: .combine)
                    Spacer()
                    Button(editingCF ? "Cancel" : "Edit") { editingCF.toggle() }.buttonStyle(PillButtonStyle(height: 38))
                        .accessibilityLabel(editingCF ? "Cancel editing the Cloudflare token" : "Edit the Cloudflare token")
                }
                if editingCF {
                    TextField("Client ID", text: $cfId).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .padding(10).background(RoundedRectangle(cornerRadius: 12).fill(Theme.control)).foregroundColor(Theme.text)
                    SecureField("Client secret", text: $cfSecret)
                        .padding(10).background(RoundedRectangle(cornerRadius: 12).fill(Theme.control)).foregroundColor(Theme.text)
                    Button("Save") {
                        ConnectionStore.shared.setCloudflare(id: cfId, secret: cfSecret)
                        editingCF = false
                        model.foreground()
                    }
                    .buttonStyle(PillButtonStyle(prominent: true))
                }
            }
            .padding(.vertical, 10)
        }
    }

    private var notificationsSection: some View {
        section {
            row("Notifications", value: notificationText)
            if push.authorization == .denied {
                divider
                Button("Open Settings") {
                    if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                }
                .buttonStyle(PillButtonStyle(height: 38))
                .padding(.vertical, 8)
            }
            if let err = push.lastError {
                divider
                Text(err).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.coralText).padding(.vertical, 8)
            }
        }
    }

    private var notificationText: String {
        switch push.authorization {
        case .denied: return "Off in Settings"
        case .notDetermined: return "Not asked yet"
        default:
            if !push.registered { return "Registering…" }
            return push.personId == nil ? "On · ask an admin to link this phone" : "On"
        }
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
        return parts.isEmpty ? "—" : parts.joined(separator: " · ")
    }

    /// After the Server screen: it may have changed the hub, certificate or Cloudflare token.
    private func refreshConnection() {
        serverText = MeScreen.currentServer
        refreshCert()
        cfId = ConnectionStore.shared.cfClientId ?? ""
        cfSecret = ConnectionStore.shared.cfClientSecret ?? ""
    }

    private var divider: some View { Divider().overlay(Theme.divider) }

    private func row(_ title: String, value: String) -> some View {
        HStack {
            Text(title).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
            Spacer()
            Text(value).font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted).multilineTextAlignment(.trailing)
        }
        .frame(minHeight: 56)
        .accessibilityElement(children: .combine)
    }

    private func section<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 0) { content() }
            .padding(.horizontal, 14)
            .padding(.vertical, 4)
            .background(RoundedRectangle(cornerRadius: 20, style: .continuous).fill(Theme.card))
            .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(Theme.cardBorder))
    }
}

/// Your colour and photo, as your family sees you (People tab, maps, the wall): twelve swatches
/// (PUT /api/me/profile) and a photo from the library, made square and at most 1024 px here, then
/// uploaded (PUT /api/me/photo; the hub makes it 256 px) - or removed. Hidden until the hub lists
/// this account's person (older hubs, or an account without one).
struct MyLookSection: View {
    @EnvironmentObject var model: AppModel
    @State private var pick: PhotosPickerItem?
    @State private var uploading = false
    @State private var savingColor: String?

    var body: some View {
        if let me = model.mePerson {
            let index = model.people.firstIndex { $0.id == me.id } ?? 0
            let current = savingColor ?? PersonLook.normalized(me.color) ?? PersonLook.presets[index % PersonLook.fallbackCount]
            let swatches = PersonLook.presets + (PersonLook.presets.contains { PersonLook.same($0, current) } ? [] : [current])
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 12) {
                    PersonAvatar(person: me, index: index, size: 52).accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("How your family sees you").font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                        Text("Your colour and photo on maps and in People").font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityAddTraits(.isHeader)
                    Spacer(minLength: 0)
                    if uploading || savingColor != nil { ProgressView().tint(Theme.text) }
                }
                Text("Colour").font(AppFont.manrope(13, .heavy)).foregroundColor(Theme.muted).accessibilityAddTraits(.isHeader)
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 6), spacing: 12) {
                    ForEach(swatches, id: \.self) { hex in
                        let on = PersonLook.same(hex, current)
                        Button { choose(hex, current: current) } label: {
                            ZStack {
                                Circle().fill(Color(hex: PersonLook.rgb(hex) ?? 0x7FB8FF)).frame(width: 34, height: 34)
                                if on {
                                    Circle().stroke(Color.white, lineWidth: 3).frame(width: 42, height: 42)
                                    Image(systemName: "checkmark").font(.system(size: 13, weight: .heavy)).foregroundColor(Theme.background)
                                }
                            }
                            .frame(width: 44, height: 44)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(Self.colourName(hex))
                        .accessibilityAddTraits(on ? .isSelected : [])
                    }
                }
                Text("Photo").font(AppFont.manrope(13, .heavy)).foregroundColor(Theme.muted).accessibilityAddTraits(.isHeader)
                HStack(spacing: 8) {
                    PhotosPicker(selection: $pick, matching: .images) {
                        Text(me.photo == nil ? "Choose photo" : "Change photo")
                    }
                    .buttonStyle(PillButtonStyle(height: 38))
                    .disabled(uploading)
                    if me.photo != nil {
                        Button("Remove photo") {
                            Task {
                                uploading = true
                                await model.removeMyPhoto()
                                uploading = false
                            }
                        }
                        .buttonStyle(PillButtonStyle(height: 38))
                        .disabled(uploading)
                    }
                }
            }
            .padding(14)
            .background(RoundedRectangle(cornerRadius: 20, style: .continuous).fill(Theme.card))
            .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(Theme.cardBorder))
            .onChange(of: pick) { _, item in
                guard let item else { return }
                Task { await upload(item) }
            }
        }
    }

    /// What VoiceOver calls a swatch (PersonLook.presets, in order); "Custom colour" for any other.
    static func colourName(_ hex: String) -> String {
        let names = ["Blue", "Purple", "Teal", "Amber", "Coral", "Green",
                     "Pink", "Sky blue", "Lavender", "Yellow", "Tan", "Slate"]
        guard let i = PersonLook.presets.firstIndex(where: { PersonLook.same($0, hex) }), i < names.count else { return "Custom colour" }
        return names[i]
    }

    @MainActor private func choose(_ hex: String, current: String) {
        guard savingColor == nil, !PersonLook.same(hex, current) else { return }
        savingColor = hex
        Task {
            await model.setMyColor(hex)
            savingColor = nil
        }
    }

    @MainActor private func upload(_ item: PhotosPickerItem) async {
        uploading = true
        defer {
            uploading = false
            pick = nil
        }
        guard let data = try? await item.loadTransferable(type: Data.self), let image = UIImage(data: data),
              let jpeg = Self.squareJPEG(image) else {
            model.show("That picture couldn't be read", error: true)
            return
        }
        if await model.setMyPhoto(jpeg) { model.show("Photo saved") }
    }

    /// The centred square, at most 1024 px, as a JPEG under the hub's 3 MB.
    static func squareJPEG(_ image: UIImage) -> Data? {
        let w = Double(image.size.width * image.scale)
        let h = Double(image.size.height * image.scale)
        guard w > 0, h > 0 else { return nil }
        let crop = PersonLook.squareCrop(width: w, height: h)
        let side = PersonLook.fitted(width: crop.side, height: crop.side).width
        let k = side / crop.side
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: side, height: side), format: format)
        let square = renderer.image { _ in
            // draw(in:) applies the photo's orientation.
            image.draw(in: CGRect(x: -crop.x * k, y: -crop.y * k, width: w * k, height: h * k))
        }
        var quality: CGFloat = 0.85
        var jpeg = square.jpegData(compressionQuality: quality)
        while let d = jpeg, d.count > PersonLook.uploadMaxBytes, quality > 0.3 {
            quality -= 0.2
            jpeg = square.jpegData(compressionQuality: quality)
        }
        return jpeg
    }
}