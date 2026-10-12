import SwiftUI
import UIKit

/// The Mac message field. SwiftUI's multi-line TextField inserts a new line on Return before any key
/// handler sees it, so on the Mac this text view catches Return itself: Return sends, Shift-Return
/// starts a new line, like Grok Bot for Mac. It grows with the text up to eight lines.
struct MacComposerField: UIViewRepresentable {
    @Binding var text: String
    let placeholder: String
    /// A new value puts the field in focus (a choice card's own answer slid into it).
    var focusRequest: Int = 0
    /// Gets the text as it is in the field, so a send never depends on the binding having caught up.
    /// Escape: returns true when it closed something (the reply header), so the key goes no further.
    var onEscape: () -> Bool = { false }
    /// Returns true when the text was sent; only then does the field clear.
    var onSubmit: (String) -> Bool

    private static let font = UIFont.systemFont(ofSize: 15)
    private static let maxLines: CGFloat = 8

    final class Field: UITextView {
        var onSubmit: (String) -> Bool = { _ in false }
        var onEscape: () -> Bool = { false }
        /// Sends what is in the field and clears it here, so the field never waits on the binding.
        func submit() {
            guard onSubmit(text ?? "") else { return }
            text = ""
            updatePlaceholder()
            delegate?.textViewDidChange?(self)
        }
        let placeholderLabel = UILabel()
        /// Shift is tracked from key presses, since a newline can arrive as inserted text.
        var shiftDown = false

        override func pressesBegan(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
            for p in presses where p.key?.keyCode == .keyboardLeftShift || p.key?.keyCode == .keyboardRightShift {
                shiftDown = true
            }
            if presses.first?.key?.keyCode == .keyboardEscape, onEscape() { return }
            if let key = presses.first?.key, key.keyCode == .keyboardReturnOrEnter,
               !key.modifierFlags.contains(.shift), markedTextRange == nil {
                submit()
                return
            }
            super.pressesBegan(presses, with: event)
        }

        override func pressesEnded(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
            for p in presses where p.key?.keyCode == .keyboardLeftShift || p.key?.keyCode == .keyboardRightShift {
                shiftDown = false
            }
            super.pressesEnded(presses, with: event)
        }

        func updatePlaceholder() { placeholderLabel.isHidden = !text.isEmpty }
    }

    final class Coordinator: NSObject, UITextViewDelegate {
        var parent: MacComposerField
        var focused = 0
        init(_ parent: MacComposerField) { self.parent = parent; focused = parent.focusRequest }

        func textView(_ v: UITextView, shouldChangeTextIn range: NSRange, replacementText text: String) -> Bool {
            guard text == "\n", let f = v as? Field, !f.shiftDown, f.markedTextRange == nil else { return true }
            f.submit()
            return false
        }

        func textViewDidChange(_ v: UITextView) {
            parent.text = v.text
            (v as? Field)?.updatePlaceholder()
            v.invalidateIntrinsicContentSize()
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> Field {
        let v = Field()
        v.font = Self.font
        v.backgroundColor = .clear
        v.textContainerInset = UIEdgeInsets(top: 8, left: 0, bottom: 8, right: 0)
        v.textContainer.lineFragmentPadding = 0
        v.isScrollEnabled = true
        v.delegate = context.coordinator
        v.onSubmit = onSubmit
        v.onEscape = onEscape
        v.text = text
        v.placeholderLabel.text = placeholder
        v.placeholderLabel.font = Self.font
        v.placeholderLabel.textColor = .placeholderText
        v.placeholderLabel.translatesAutoresizingMaskIntoConstraints = false
        v.addSubview(v.placeholderLabel)
        NSLayoutConstraint.activate([
            v.placeholderLabel.leadingAnchor.constraint(equalTo: v.leadingAnchor),
            v.placeholderLabel.topAnchor.constraint(equalTo: v.topAnchor, constant: 8),
        ])
        v.updatePlaceholder()
        return v
    }

    func updateUIView(_ v: Field, context: Context) {
        context.coordinator.parent = self
        v.onSubmit = onSubmit
        v.onEscape = onEscape
        v.placeholderLabel.text = placeholder
        // While Colin is typing, the field is the source of truth; a render with an older draft must not
        // overwrite what he just typed. Drafts restored from elsewhere arrive while it is not focused.
        if v.text != text, !v.isFirstResponder {
            v.text = text
            v.updatePlaceholder()
        }
        if focusRequest != context.coordinator.focused {
            context.coordinator.focused = focusRequest
            DispatchQueue.main.async { v.becomeFirstResponder() }
        }
    }

    func sizeThatFits(_ proposal: ProposedViewSize, uiView v: Field, context: Context) -> CGSize? {
        let width = proposal.width ?? 400
        let fitting = v.sizeThatFits(CGSize(width: width, height: .greatestFiniteMagnitude)).height
        let maxHeight = Self.font.lineHeight * Self.maxLines + 16
        return CGSize(width: width, height: min(max(fitting, Self.font.lineHeight + 16), maxHeight))
    }
}
