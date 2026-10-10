import SwiftUI
import UIKit

/// First run (and after signing out): connect to the hub first (ServerSetupView: found on the
/// network, QR code / link, or typed; certificate and Cloudflare token), then sign in (with the
/// 2FA code when the hub asks for it). "Server" on the sign-in screen goes back to the first part.
struct OnboardingView: View {
    @EnvironmentObject var model: AppModel

    enum Step { case server, signIn }

    @State private var step: Step = ConnectionStore.shared.serverURL == nil ? .server : .signIn
    @State private var username = ""
    @State private var password = ""
    @State private var code = ""
    @State private var needsCode = false
    @State private var working = false
    @State private var error: String?

    var body: some View {
        Group {
            switch step {
            case .server:
                ServerSetupView(link: $model.incomingLink,
                                onClose: ConnectionStore.shared.serverURL == nil ? nil : { step = .signIn }) {
                    error = nil
                    step = .signIn
                }
            case .signIn:
                signInScreen
            }
        }
        .onAppear {
            if model.incomingLink != nil { step = .server }
        }
        .onChange(of: model.incomingLink) { _, link in
            if link != nil { step = .server }
        }
    }

    private var signInScreen: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Image("Logo")
                    .resizable()
                    .frame(width: 64, height: 64)
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                    .padding(.top, 40)
                    .accessibilityHidden(true)
                Text(needsCode ? "Two-step sign-in" : "Sign in").font(AppFont.sora(28)).foregroundColor(Theme.text)
                    .accessibilityAddTraits(.isHeader)
                Text(needsCode ? "Enter the 6-digit code from your authenticator app." : "Use your Newman account.")
                    .font(AppFont.manrope(14, .semibold)).foregroundColor(Theme.muted)
                if !needsCode {
                    field("Username", text: $username, content: .username)
                    field("Password", text: $password, secure: true, content: .password)
                } else {
                    field("123456", text: $code, keyboard: .numberPad, content: .oneTimeCode)
                        .accessibilityLabel("6-digit code")
                }
                Button(working ? "Signing in…" : (needsCode ? "Verify" : "Sign in")) { Task { await signIn() } }
                    .buttonStyle(PillButtonStyle(height: 50, prominent: true))
                    .disabled(working || username.isEmpty || password.isEmpty || (needsCode && code.count < 6))
                HStack {
                    if needsCode {
                        Button("Back") {
                            needsCode = false
                            code = ""
                            error = nil
                        }
                        .buttonStyle(PillButtonStyle())
                    } else {
                        Button {
                            error = nil
                            step = .server
                        } label: {
                            Label("Server", systemImage: "server.rack")
                        }
                        .buttonStyle(PillButtonStyle())
                    }
                    Spacer()
                }
                Text(serverText)
                    .font(AppFont.manrope(12, .bold)).foregroundColor(Theme.dim)
                if let error {
                    Text(error).font(AppFont.manrope(14, .bold)).foregroundColor(Theme.coralText)
                }
            }
            .padding(20)
        }
        .onChange(of: error) { _, e in
            // VoiceOver hears why sign-in failed (the message appears below the focus).
            if let e { AccessibilityNotification.Announcement(e).post() }
        }
        .scrollDismissesKeyboard(.interactively)
    }

    /// "Home · home.example.com"
    private var serverText: String {
        let store = ConnectionStore.shared
        let name = store.hub?.name.trimmingCharacters(in: .whitespaces)
        return [name?.isEmpty == false ? name : nil, store.serverURL?.host].compactMap { $0 }.joined(separator: " · ")
    }

    private func field(_ placeholder: String, text: Binding<String>, secure: Bool = false, keyboard: UIKeyboardType = .default,
                       content: UITextContentType? = nil) -> some View {
        Group {
            if secure {
                SecureField(placeholder, text: text)
            } else {
                TextField(placeholder, text: text)
                    .keyboardType(keyboard)
            }
        }
        .textContentType(content)
        .textInputAutocapitalization(.never)
        .autocorrectionDisabled()
        .font(AppFont.manrope(16, .semibold))
        .foregroundColor(Theme.text)
        .padding(.horizontal, 14)
        .frame(minHeight: 50)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.control))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.controlBorder))
    }

    private func signIn() async {
        working = true
        error = nil
        defer { working = false }
        do {
            let outcome = try await APIClient.shared.login(username: username, password: password, code: needsCode ? code : nil,
                                                           kind: "app", deviceName: UIDevice.current.name)
            switch outcome {
            case .signedIn(let token, let user):
                ConnectionStore.shared.token = token
                password = ""
                code = ""
                needsCode = false
                await model.signedIn(user: user)
            case .needsCode(let message):
                if needsCode { error = message ?? "That code didn't work" }
                needsCode = true
                code = ""
            case .failed(let message):
                error = message
            }
        } catch {
            self.error = (error as? APIError)?.message ?? error.localizedDescription
        }
    }
}
