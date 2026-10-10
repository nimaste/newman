import AVFoundation
import SwiftUI
import UIKit

/// Full-screen camera that reads one QR code (the hub's newman://connect code) and hands it back.
struct QRScanSheet: View {
    var onCode: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var allowed: Bool?

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if allowed == true {
                QRScannerView(onCode: onCode).ignoresSafeArea()
                    .accessibilityLabel("Camera. Point it at the Newman QR code")
                RoundedRectangle(cornerRadius: 28, style: .continuous)
                    .stroke(Color.white.opacity(0.8), lineWidth: 3)
                    .frame(width: 240, height: 240)
                    .accessibilityHidden(true)
            } else if allowed == false {
                VStack(spacing: 14) {
                    Image(systemName: "camera.fill").font(.system(size: 36)).foregroundColor(Theme.muted)
                        .accessibilityHidden(true)
                    Text("Newman isn't allowed to use the camera.")
                        .font(AppFont.manrope(15, .bold)).foregroundColor(Theme.text).multilineTextAlignment(.center)
                    Button("Open Settings") {
                        if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                    }
                    .buttonStyle(PillButtonStyle(height: 44))
                }
                .padding(32)
            }
            VStack {
                HStack {
                    Spacer()
                    Button("Cancel") { dismiss() }.buttonStyle(PillButtonStyle())
                }
                .padding(16)
                Spacer()
            }
        }
        .task { allowed = await Self.cameraAllowed() }
    }

    static func cameraAllowed() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: return true
        case .notDetermined: return await AVCaptureDevice.requestAccess(for: .video)
        default: return false
        }
    }
}

/// AVFoundation's metadata scanner, QR codes only. Reports the first code once.
struct QRScannerView: UIViewControllerRepresentable {
    var onCode: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIViewController(context: Context) -> QRScannerController {
        context.coordinator.onCode = onCode
        let vc = QRScannerController()
        vc.coordinator = context.coordinator
        return vc
    }

    func updateUIViewController(_ vc: QRScannerController, context: Context) {
        context.coordinator.onCode = onCode
    }

    final class Coordinator: NSObject, AVCaptureMetadataOutputObjectsDelegate {
        var onCode: ((String) -> Void)?
        private var done = false

        func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput metadataObjects: [AVMetadataObject],
                            from connection: AVCaptureConnection) {
            guard !done,
                  let code = metadataObjects.compactMap({ ($0 as? AVMetadataMachineReadableCodeObject)?.stringValue }).first,
                  !code.isEmpty else { return }
            done = true
            DispatchQueue.main.async {
                UINotificationFeedbackGenerator().notificationOccurred(.success)
                self.onCode?(code)
            }
        }
    }
}

final class QRScannerController: UIViewController {
    var coordinator: QRScannerView.Coordinator?
    private let session = AVCaptureSession()
    private var preview: AVCaptureVideoPreviewLayer?
    /// Keeps the preview upright in any orientation (the wall iPad turns).
    private var rotation: AVCaptureDevice.RotationCoordinator?
    private var rotationObservation: NSKeyValueObservation?

    /// A wall-mounted iPad faces the room with its front camera; a phone scans with the back one.
    private static var camera: AVCaptureDevice? {
        let position: AVCaptureDevice.Position = UIDevice.current.userInterfaceIdiom == .pad ? .front : .back
        return AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: position)
            ?? AVCaptureDevice.default(for: .video)
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        guard let device = Self.camera,
              let input = try? AVCaptureDeviceInput(device: device),
              session.canAddInput(input) else { return }
        session.addInput(input)
        let output = AVCaptureMetadataOutput()
        guard session.canAddOutput(output) else { return }
        session.addOutput(output)
        // Delivered on a private queue; the coordinator hops to the main queue.
        output.setMetadataObjectsDelegate(coordinator, queue: DispatchQueue(label: "ca.nimaste.newman.qr"))
        if output.availableMetadataObjectTypes.contains(.qr) { output.metadataObjectTypes = [.qr] }
        let layer = AVCaptureVideoPreviewLayer(session: session)
        layer.videoGravity = .resizeAspectFill
        view.layer.addSublayer(layer)
        preview = layer
        let rotation = AVCaptureDevice.RotationCoordinator(device: device, previewLayer: layer)
        layer.connection?.videoRotationAngle = rotation.videoRotationAngleForHorizonLevelPreview
        rotationObservation = rotation.observe(\.videoRotationAngleForHorizonLevelPreview, options: [.new]) { [weak layer] r, _ in
            let angle = r.videoRotationAngleForHorizonLevelPreview
            DispatchQueue.main.async { layer?.connection?.videoRotationAngle = angle }
        }
        self.rotation = rotation
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        preview?.frame = view.bounds
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        let s = session
        DispatchQueue.global(qos: .userInitiated).async { if !s.isRunning { s.startRunning() } }
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        let s = session
        DispatchQueue.global(qos: .userInitiated).async { if s.isRunning { s.stopRunning() } }
    }
}
