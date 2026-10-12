import SwiftUI
import UIKit

/// Google's Sign In with Google button, built to the iOS spec in Google's branding guidelines
/// (https://developers.google.com/identity/branding-guidelines):
/// - Google's own "G" (the `GoogleG` image set: g-logo.png from the guidelines page, unmodified; it is the same G as in
///   Google's downloadable sign-in asset pack, whose SVGs use a gradient asset catalogs cannot draw). Never redrawn,
///   recolored or stretched.
/// - The iOS padding at the 44-point reference height: 16 before the G, the G 20 points tall, 12 between the G and the
///   text, 16 after the text. The whole button scales evenly to the app's 52-point buttons, as the guidelines allow.
/// - Light: fill #FFFFFF, a 1-point #747775 stroke inside, text #1F1F1F. Dark: fill #131314, a 1-point #8E918F stroke
///   inside, text #E3E3E3. Pill shape.
/// - Text in Google Sans Medium, 14 on 20 at the reference height (bundled, Open Font License); the system font at
///   medium weight if it ever fails to load.
struct GoogleSignInButton: View {
    var title = "Sign In with Google"
    var busy = false
    var enabled = true
    let action: () -> Void
    @Environment(\.colorScheme) private var scheme

    /// Google's iOS reference button is 44 points tall; Zero's primary buttons are 52.
    private static let height: CGFloat = 52
    private static let scale = height / 44

    var body: some View {
        let dark = scheme == .dark
        let s = Self.scale
        let ink = dark ? Color(hex: "#E3E3E3") : Color(hex: "#1F1F1F")
        Button(action: action) {
            HStack(spacing: 12 * s) {
                Image("GoogleG")
                    .resizable()
                    .interpolation(.high)
                    .aspectRatio(contentMode: .fit)
                    .frame(height: 20 * s)
                    .accessibilityHidden(true)
                Text(busy ? "Waiting for Google" : title)
                    .font(Self.googleSans(14 * s))
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                if busy {
                    ProgressView().controlSize(.small).tint(ink)
                }
            }
            .foregroundStyle(ink)
            .padding(.horizontal, 16 * s)
            .frame(maxWidth: .infinity, minHeight: Self.height)
            .background(dark ? Color(hex: "#131314") : .white, in: Capsule())
            .overlay(Capsule().strokeBorder(dark ? Color(hex: "#8E918F") : Color(hex: "#747775"), lineWidth: 1))
            .opacity(enabled ? 1 : 0.38)
        }
        .buttonStyle(PressStyle())
        .disabled(!enabled || busy)
        .accessibilityLabel(busy ? "Waiting for Google" : title)
    }

    /// Google Sans at its Medium weight (the variable font's wght axis at 500).
    static func googleSans(_ size: CGFloat) -> Font {
        guard UIFont(name: "GoogleSans-Regular", size: size) != nil else {
            return .system(size: size, weight: .medium)
        }
        let wght = 0x7767_6874   // 'wght'
        let descriptor = UIFontDescriptor(fontAttributes: [.name: "GoogleSans-Regular"])
            .addingAttributes([UIFontDescriptor.AttributeName(rawValue: kCTFontVariationAttribute as String): [wght: 500]])
        return Font(UIFont(descriptor: descriptor, size: size))
    }
}
