import SwiftUI

@main
struct TaffyApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var state: AppState

    init() {
        // Alerts arrive as notifications from the hub (Push.swift); the app does no background work of its own.
        let state = AppState()
        _state = State(initialValue: state)
        Push.shared.state = state
        WorkFiles.clear()   // opened attachments don't pile up on the phone
        #if !targetEnvironment(macCatalyst)
        // The call provider is registered with the system at launch, not at the first call: a call requested right
        // after the provider is made can be refused as an unknown provider, and then runs without the Dynamic Island.
        _ = CallKitBridge.shared
        #endif
    }

    /// DEBUG: -dark or -light forces the appearance, for screenshots of one app without changing the Mac's setting.
    private static var scheme: ColorScheme? {
        #if DEBUG
        if CommandLine.arguments.contains("-dark") { return .dark }
        if CommandLine.arguments.contains("-light") { return .light }
        #endif
        return nil
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(state)
                .preferredColorScheme(Self.scheme)
                // zero://pair?server=...&code=..., from a QR code read by the Camera app or a link in a message.
                .onOpenURL { state.open($0) }
                #if targetEnvironment(macCatalyst)
                .task { MacLink.shared.offerMoveToApplications() }
                #endif
        }
    }
}
