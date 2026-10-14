import Foundation
import SwiftUI
import UIKit
import WebKit

enum DeleteEverythingResult: Equatable {
    case done
    case computerUnreachable
    case refused(String)
}

extension AppState {
    func deleteEverything(thisDeviceOnly: Bool = false) async -> DeleteEverythingResult {
        if paired, !demoMode, !fixtureMode, !thisDeviceOnly {
            do {
                try await client.deleteEverything()
            } catch {
                if let h = error as? HubClient.HubError {
                    switch h {
                    case .notPaired: break
                    case .http(let code, let message):
                        if code == 409 { return .refused(message) }
                        return .computerUnreachable
                    }
                } else {
                    return .computerUnreachable
                }
            }
            await client.unpair()
        }
        wipeThisDevice()
        return .done
    }

    func wipeThisDevice() {
        stopPolling()
        saveTask?.cancel()
        consentWaiters.forEach { $0.resume(returning: false) }
        consentWaiters = []
        aiConsentSheet = false
        client.token = nil
        HubTrust.removePin(for: client.server)
        client.server = ""
        client.demo = false
        chats = [:]; board = .empty; bots = []; working = [:]; mutedBots = []; proactivity = nil
        drafts = [:]; pinned = ["default"]; callVoice = "cove"
        onDeviceVoice = "george"
        callEngine = AppState.chatGPTEngine
        Task { await OnDeviceVoice.shared.remove() }
        UserDefaults.standard.removeObject(forKey: "onDeviceGreeting")
        pendingPair = nil
        pairUnreachable = false
        HomeModel.shared.clear()
        saveCallTranscripts = true
        feedCursor = 0
        updatedAt = nil
        lastError = nil
        offline = false
        macLinkNote = nil
        demoMode = false
        fixtureMode = false
        // The Keychain: the pairing and the Google voice key (iOS keeps these after the app is deleted).
        Keychain.delete("hubToken")
        GeminiKeyStore.remove()
        if let id = Bundle.main.bundleIdentifier { UserDefaults.standard.removePersistentDomain(forName: id) }
        aiConsent = .unasked
        UserDefaults.standard.removeObject(forKey: "aiConsent")
        UserDefaults.standard.removeObject(forKey: "aiConsentAt")
        clearSaved()
        WorkFiles.clear()
        WKWebsiteDataStore.default().removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(),
                                                modifiedSince: .distantPast) {}
        UIApplication.shared.unregisterForRemoteNotifications()
        paired = false
        showWelcome = false
        rehearsingOnboarding = false
        onboarding = true
    }
}
