import SwiftUI
import UIKit

final class LoginFieldReader {
    weak var field: UITextField?
    var text: String { field?.text ?? "" }
}

/// A username or password field backed by UIKit. Password AutoFill (on the Mac especially) can fill the field
/// without SwiftUI's binding ever changing, which left Save disabled. Here every edit notification updates the
/// binding, a light check every half second catches silent fills while the field is on screen, and the reader lets
/// Save read the field itself.
struct LoginField: UIViewRepresentable {
    let placeholder: String
    @Binding var text: String
    var secure = false
    var contentType: UITextContentType
    var reader: LoginFieldReader
    var focusOnAppear = false
    var onReturn: (() -> Void)? = nil

    func makeUIView(context: Context) -> UITextField {
        let f = UITextField()
        f.placeholder = placeholder
        f.isSecureTextEntry = secure
        f.textContentType = contentType
        f.autocapitalizationType = .none
        f.autocorrectionType = .no
        f.spellCheckingType = .no
        f.font = UIFont.preferredFont(forTextStyle: .body)
        f.adjustsFontForContentSizeCategory = true
        f.textColor = UIColor(Theme.ink)
        f.returnKeyType = onReturn == nil ? .done : .next
        f.delegate = context.coordinator
        f.setContentHuggingPriority(.defaultLow, for: .horizontal)
        f.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        f.addTarget(context.coordinator, action: #selector(Coordinator.edited(_:)),
                    for: [.editingChanged, .editingDidEnd, .valueChanged])
        NotificationCenter.default.addObserver(context.coordinator, selector: #selector(Coordinator.notified(_:)),
                                               name: UITextField.textDidChangeNotification, object: f)
        context.coordinator.watch(f)
        reader.field = f
        if focusOnAppear {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { f.becomeFirstResponder() }
        }
        return f
    }

    func updateUIView(_ f: UITextField, context: Context) {
        context.coordinator.parent = self
        // Only a value set from code goes into the field. An empty binding never clears it, because the binding can
        // lag behind a fill the field got silently.
        if !text.isEmpty, f.text != text { f.text = text }
    }

    static func dismantleUIView(_ f: UITextField, coordinator: Coordinator) { coordinator.stop() }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, UITextFieldDelegate {
        var parent: LoginField
        private var timer: Timer?

        init(_ parent: LoginField) { self.parent = parent }

        @objc func edited(_ f: UITextField) { sync(f) }
        @objc func notified(_ n: Notification) { if let f = n.object as? UITextField { sync(f) } }

        func sync(_ f: UITextField) {
            let t = f.text ?? ""
            if t != parent.text { parent.text = t }
        }

        func watch(_ f: UITextField) {
            timer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self, weak f] _ in
                guard let self, let f else { return }
                MainActor.assumeIsolated { self.sync(f) }
            }
        }

        func stop() {
            timer?.invalidate()
            NotificationCenter.default.removeObserver(self)
        }

        func textFieldShouldReturn(_ f: UITextField) -> Bool {
            if let r = parent.onReturn { r() } else { f.resignFirstResponder() }
            return true
        }
    }
}
