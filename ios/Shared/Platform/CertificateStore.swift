import Foundation
import Security

/// The optional mTLS client certificate (GChat's MTLSStore): the raw .p12 (base64) and its
/// password in the Keychain. It survives sign-out on purpose - away from home it's what lets
/// you reach the sign-in page at all.
final class CertificateStore {
    static let shared = CertificateStore()

    private enum K {
        static let p12 = "mtls_p12_base64"
        static let password = "mtls_p12_password"
        static let name = "mtls_cert_name"
    }

    enum ImportError: LocalizedError {
        case wrongPassword
        case noIdentity
        case unreadable(OSStatus)

        var errorDescription: String? {
            switch self {
            case .wrongPassword: return "That password doesn't open this certificate."
            case .noIdentity: return "This file has no certificate with a private key in it."
            case .unreadable(let s): return "This file couldn't be read as a .p12 certificate (error \(s))."
            }
        }
    }

    private init() {}

    var isConfigured: Bool { KeychainService.get(key: K.p12) != nil }
    var certificateName: String? { KeychainService.get(key: K.name) }

    /// Validates the file (it must open with the password and hold an identity) before storing it.
    @discardableResult
    func importCertificate(p12Data: Data, password: String, displayName: String) throws -> String {
        let summary = try CertificateStore.validate(p12Data: p12Data, password: password)
        KeychainService.save(key: K.p12, value: p12Data.base64EncodedString())
        KeychainService.save(key: K.password, value: password)
        let name = summary.isEmpty ? displayName : summary
        KeychainService.save(key: K.name, value: name)
        return name
    }

    func clear() {
        KeychainService.delete(key: K.p12)
        KeychainService.delete(key: K.password)
        KeychainService.delete(key: K.name)
    }

    /// Re-imported from the stored blob on every TLS challenge, so a certificate imported or
    /// removed mid-session (by either process) applies to the very next connection.
    func loadIdentity() -> (identity: SecIdentity, certificates: [SecCertificate])? {
        guard let b64 = KeychainService.get(key: K.p12), let data = Data(base64Encoded: b64) else { return nil }
        let password = KeychainService.get(key: K.password) ?? ""
        return try? CertificateStore.importIdentity(p12Data: data, password: password)
    }

    /// The URLCredential to answer NSURLAuthenticationMethodClientCertificate with, if any.
    func credential() -> URLCredential? {
        guard let (identity, chain) = loadIdentity() else { return nil }
        // The whole chain from the .p12, as GChat does (proven against the user's Caddy mTLS).
        return URLCredential(identity: identity, certificates: chain, persistence: .forSession)
    }

    static func importIdentity(p12Data: Data, password: String) throws -> (identity: SecIdentity, certificates: [SecCertificate]) {
        let options: [String: Any] = [kSecImportExportPassphrase as String: password]
        var raw: CFArray?
        let status = SecPKCS12Import(p12Data as CFData, options as CFDictionary, &raw)
        if status == errSecAuthFailed || status == errSecPkcs12VerifyFailure { throw ImportError.wrongPassword }
        guard status == errSecSuccess else { throw ImportError.unreadable(status) }
        guard let items = raw as? [[String: Any]], let first = items.first,
              let identityRef = first[kSecImportItemIdentity as String] else { throw ImportError.noIdentity }
        // swiftlint:disable:next force_cast
        let identity = identityRef as! SecIdentity
        let chain = first[kSecImportItemCertChain as String] as? [SecCertificate] ?? []
        return (identity, chain)
    }

    /// "Chris's iPhone (expires …)" style summary: the certificate's subject.
    static func validate(p12Data: Data, password: String) throws -> String {
        let (identity, _) = try importIdentity(p12Data: p12Data, password: password)
        var cert: SecCertificate?
        SecIdentityCopyCertificate(identity, &cert)
        guard let cert else { return "" }
        return (SecCertificateCopySubjectSummary(cert) as String?) ?? ""
    }
}
