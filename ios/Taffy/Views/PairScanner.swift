import AVFoundation
import SwiftUI

/// Scans the pairing QR code (zero://pair?server=...&code=...) with the iPhone's camera. On a Mac there is no
/// scanner; the code screen offers Paste Link instead.
struct PairScannerSheet: View {
    @Environment(\.dismiss) private var dismiss
    let onFound: (PairLink) -> Void
    @State private var problem: String?
    @State private var wrongCode = false

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Scan Your Code")
                    .font(Theme.text(22, .bold)).foregroundStyle(.white)
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                Button { dismiss() } label: {
                    Image(systemName: "xmark").font(.system(size: 15, weight: .bold)).foregroundStyle(.white)
                        .frame(width: 36, height: 36).background(Color.white.opacity(0.18), in: Circle())
                }
                .accessibilityLabel("Close")
            }
            .padding(.horizontal, 20).padding(.top, 18).padding(.bottom, 14)
            ZStack {
                #if targetEnvironment(macCatalyst)
                Color.black
                #else
                if problem == nil {
                    QRCameraView(onCode: found, onProblem: { problem = $0 })
                } else {
                    Color.black
                }
                #endif
                RoundedRectangle(cornerRadius: 28, style: .continuous)
                    .stroke(Color.white.opacity(0.9), lineWidth: 3)
                    .frame(width: 240, height: 240)
                    .opacity(problem == nil ? 1 : 0)
                if let problem {
                    VStack(spacing: 12) {
                        Image(systemName: "camera.fill").font(.system(size: 34)).foregroundStyle(.white.opacity(0.8))
                        Text(problem).font(Theme.text(17)).foregroundStyle(.white)
                            .multilineTextAlignment(.center)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(32)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
            .padding(.horizontal, 16)
            Text(wrongCode ? "That code isn't a Zero code. Scan the code that came with your home."
                           : "Point the camera at the code you were given. It connects this \(Theme.device) to your home.")
                .font(Theme.text(16)).foregroundStyle(.white.opacity(0.85))
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 28).padding(.vertical, 20)
        }
        .background(Color.black.ignoresSafeArea())
        .environment(\.colorScheme, .dark)
    }

    private func found(_ text: String) {
        guard let link = PairLink.parse(text) else { wrongCode = true; return }
        onFound(link)
        dismiss()
    }
}

#if !targetEnvironment(macCatalyst)
/// The camera with a QR reader. Says plainly when there is no camera or no permission.
private struct QRCameraView: UIViewControllerRepresentable {
    let onCode: (String) -> Void
    let onProblem: (String) -> Void

    func makeUIViewController(context: Context) -> Controller {
        let c = Controller()
        c.onCode = onCode
        c.onProblem = onProblem
        return c
    }

    func updateUIViewController(_ controller: Controller, context: Context) {}

    final class Controller: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
        var onCode: (String) -> Void = { _ in }
        var onProblem: (String) -> Void = { _ in }
        private let session = AVCaptureSession()
        private var preview: AVCaptureVideoPreviewLayer?
        private var last: String?

        override func viewDidLoad() {
            super.viewDidLoad()
            view.backgroundColor = .black
            switch AVCaptureDevice.authorizationStatus(for: .video) {
            case .authorized: configure()
            case .notDetermined:
                AVCaptureDevice.requestAccess(for: .video) { ok in
                    DispatchQueue.main.async { ok ? self.configure() : self.denied() }
                }
            default: denied()
            }
        }

        private func denied() {
            onProblem("Zero can't use the camera. Turn on Camera for Zero in the Settings app, or type the code instead.")
        }

        private func configure() {
            guard let device = AVCaptureDevice.default(for: .video), let input = try? AVCaptureDeviceInput(device: device),
                  session.canAddInput(input) else {
                onProblem("This \(Theme.device) has no camera Zero can use. Type the code instead.")
                return
            }
            session.addInput(input)
            let output = AVCaptureMetadataOutput()
            guard session.canAddOutput(output) else { return onProblem("The camera isn't available right now. Type the code instead.") }
            session.addOutput(output)
            output.setMetadataObjectsDelegate(self, queue: .main)
            output.metadataObjectTypes = [.qr]
            let layer = AVCaptureVideoPreviewLayer(session: session)
            layer.videoGravity = .resizeAspectFill
            layer.frame = view.bounds
            view.layer.addSublayer(layer)
            preview = layer
            DispatchQueue.global(qos: .userInitiated).async { self.session.startRunning() }
        }

        override func viewDidLayoutSubviews() {
            super.viewDidLayoutSubviews()
            preview?.frame = view.bounds
        }

        override func viewWillDisappear(_ animated: Bool) {
            super.viewWillDisappear(animated)
            if session.isRunning { DispatchQueue.global().async { self.session.stopRunning() } }
        }

        func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput objects: [AVMetadataObject],
                            from connection: AVCaptureConnection) {
            guard let text = (objects.first as? AVMetadataMachineReadableCodeObject)?.stringValue, text != last else { return }
            last = text
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            onCode(text)
        }
    }
}
#endif

/// "Scan a Code" on the iPhone, "Paste Link" on the Mac: both fill the address and the code from a zero://pair link.
struct PairLinkButton: View {
    let onLink: (PairLink) -> Void
    @State private var scanning = false
    @State private var pasteProblem = false

    var body: some View {
        Group {
            if Theme.onMac {
                OnboardingSecondaryButton(pasteProblem ? "No Zero Link to Paste" : "Paste Link", symbol: "doc.on.clipboard") {
                    if let text = UIPasteboard.general.string, let link = PairLink.parse(text) {
                        pasteProblem = false
                        onLink(link)
                    } else {
                        pasteProblem = true
                    }
                }
            } else {
                OnboardingSecondaryButton("Scan a Code", symbol: "qrcode.viewfinder") { scanning = true }
            }
        }
        .sheet(isPresented: $scanning) { PairScannerSheet(onFound: onLink) }
        #if DEBUG
        .task { if CommandLine.arguments.contains("-scan") { try? await Task.sleep(for: .milliseconds(600)); scanning = true } }
        #endif
    }
}
