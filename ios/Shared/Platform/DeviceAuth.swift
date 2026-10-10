import Foundation
import LocalAuthentication

/// Face ID / Touch ID with the device passcode as fallback (LAPolicy.deviceOwnerAuthentication).
enum DeviceAuth {
    enum Result {
        case ok
        case cancelled
        case failed(String)
    }

    /// "Face ID", "Touch ID", "Optic ID" or "Passcode", for labels.
    static var methodName: String {
        let ctx = LAContext()
        var err: NSError?
        _ = ctx.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &err)
        switch ctx.biometryType {
        case .faceID: return "Face ID"
        case .touchID: return "Touch ID"
        case .opticID: return "Optic ID"
        default: return "Passcode"
        }
    }

    /// Asks the person to prove it's them. With no passcode set on the device at all there is
    /// nothing to ask for, so it goes ahead (home defaults stay permissive).
    static func authenticate(reason: String) async -> Result {
        let ctx = LAContext()
        ctx.localizedCancelTitle = "Cancel"
        var err: NSError?
        guard ctx.canEvaluatePolicy(.deviceOwnerAuthentication, error: &err) else {
            if let code = err?.code, code == LAError.passcodeNotSet.rawValue { return .ok }
            return .failed(err?.localizedDescription ?? "This device can't confirm it's you.")
        }
        do {
            let ok = try await ctx.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason)
            return ok ? .ok : .cancelled
        } catch let e as LAError {
            switch e.code {
            case .userCancel, .appCancel, .systemCancel: return .cancelled
            default: return .failed(e.localizedDescription)
            }
        } catch {
            return .failed(error.localizedDescription)
        }
    }
}
