import Foundation
import SwiftUI
import UIKit
import WebKit

/// Settings, Delete Everything: removes the person's data from this device and from their own computer.
///
/// Order matters. The computer goes first, because it needs the pairing this phone holds. If the computer cannot be
/// reached, nothing is deleted and the person chooses: try again later, or delete only what is on this device. The
/// device's own data goes last and is always complete: the pairing, the Google voice key, saved chats, drafts,
/// settings, opened attachments, website sign-ins and the notification registration.
enum DeleteEverythingResult: Equatable {
    case done
    /// The computer could not be reached. Nothing was deleted anywhere.
    case computerUnreachable
    /// The computer said no (a Zip is answering), in the computer's own plain words.
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
                    case .notPaired: break   // already signed out of the computer; clear this device
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

    /// Everything the app keeps on this device, then back to the first screen.
    func wipeThisDevice() {
        stopPolling()
        saveTask?.cancel()
        consentWaiters.forEach { $0.resume(returning: false) }
        consentWaiters = []
        aiConsentSheet = false
        // In memory first, so the property observers cannot write old values back.
        client.token = nil
        HubTrust.removePin(for: client.server)   // this home's certificate pin goes with it
        client.server = ""
        client.demo = false
        chats = [:]; board = .empty; bots = []; working = [:]; mutedBots = []; proactivity = nil
        drafts = [:]; pinned = ["default"]; callVoice = "cove"
        onDeviceVoice = "george"
        callEngine = AppState.chatGPTEngine
        // The On-Device Voice's files, with its greeting takes (they say the person's name).
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
        // The settings file, the saved copy of the chats, opened attachments and website sign-ins.
        if let id = Bundle.main.bundleIdentifier { UserDefaults.standard.removePersistentDomain(forName: id) }
        aiConsent = .unasked
        UserDefaults.standard.removeObject(forKey: "aiConsent")
        UserDefaults.standard.removeObject(forKey: "aiConsentAt")
        clearSaved()
        WorkFiles.clear()
        WKWebsiteDataStore.default().removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(),
                                                modifiedSince: .distantPast) {}
        UIApplication.shared.unregisterForRemoteNotifications()
        // Back to the first screen of the onboarding.
        paired = false
        showWelcome = false
        rehearsingOnboarding = false
        onboarding = true
    }
}
