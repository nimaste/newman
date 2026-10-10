import SwiftUI
import UniformTypeIdentifiers

/// Pick a .p12 file, type its password, validate and store it (CertificateStore). Shared by
/// the phone's onboarding and Me screen and the wall's settings.
struct CertificateImportView: View {
    var onDone: (String) -> Void

    @State private var picking = false
    @State private var fileData: Data?
    @State private var fileName = ""
    @State private var password = ""
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button {
                picking = true
            } label: {
                HStack {
                    Image(systemName: "doc.badge.plus")
                    Text(fileData == nil ? "Choose a .p12 file" : fileName).lineLimit(1)
                    Spacer()
                }
            }
            .buttonStyle(PillButtonStyle(height: 48))

            if fileData != nil {
                SecureField("Certificate password", text: $password)
                    .textContentType(.password)
                    .font(AppFont.manrope(16, .semibold))
                    .padding(.horizontal, 14)
                    .frame(minHeight: 48)
                    .background(RoundedRectangle(cornerRadius: 14).fill(Theme.control))
                    .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.controlBorder))
                    .foregroundColor(Theme.text)
                Button("Import certificate") { importIt() }
                    .buttonStyle(PillButtonStyle(height: 48, prominent: true))
            }
            if let error {
                Text(error).font(AppFont.manrope(13, .bold)).foregroundColor(Theme.coralText)
            }
        }
        .fileImporter(isPresented: $picking, allowedContentTypes: [UTType.pkcs12, UTType(filenameExtension: "pfx") ?? .data, .data],
                      allowsMultipleSelection: false) { result in
            switch result {
            case .success(let urls):
                guard let url = urls.first else { return }
                // Files from the Files app are security-scoped: open them before reading.
                let scoped = url.startAccessingSecurityScopedResource()
                defer { if scoped { url.stopAccessingSecurityScopedResource() } }
                do {
                    fileData = try Data(contentsOf: url)
                    fileName = url.lastPathComponent
                    error = nil
                } catch {
                    self.error = "Couldn't read that file: \(error.localizedDescription)"
                }
            case .failure(let e):
                error = e.localizedDescription
            }
        }
    }

    private func importIt() {
        guard let fileData else { return }
        do {
            let name = try CertificateStore.shared.importCertificate(p12Data: fileData, password: password,
                                                                      displayName: (fileName as NSString).deletingPathExtension)
            password = ""
            self.fileData = nil
            error = nil
            onDone(name)
        } catch {
            self.error = error.localizedDescription
        }
    }
}
