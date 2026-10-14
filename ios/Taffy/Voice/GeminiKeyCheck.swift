import Foundation

/// Checks a Gemini key the way calls will use it: opens a Gemini Live session, waits for Google to say the
/// session is set up, and closes it. No audio is sent, so the check uses no minutes. The key travels in the
/// `x-goog-api-key` header, not the address, so it never lands in a URL or a log.
enum GeminiKeyCheck {
    static let model = "gemini-3.8-live"
    static var endpoint: URL {
        #if DEBUG
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "-voiceSetupCheckURL"), i + 1 < args.count, let url = URL(string: args[i + 1]) { return url }
        #endif
        return live
    }
    private static let live = URL(string: "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")!

    enum Result: Equatable {
        case works
        case rejected
        case limited
        case refused(String)
        case offline
    }

    static func run(_ key: String, timeout: TimeInterval = 12) async -> Result {
        var request = URLRequest(url: endpoint, timeoutInterval: timeout)
        request.setValue(key, forHTTPHeaderField: "x-goog-api-key")
        let session = URLSession(configuration: .ephemeral)
        let task = session.webSocketTask(with: request)
        task.resume()
        defer { session.invalidateAndCancel() }

        let result = await withTaskGroup(of: Result?.self) { group in
            group.addTask { await handshake(task) }
            group.addTask {
                try? await Task.sleep(for: .seconds(timeout))
                return nil
            }
            let first = await group.next() ?? nil
            task.cancel(with: .normalClosure, reason: nil)
            group.cancelAll()
            return first
        }
        return result ?? .offline
    }

    private static func handshake(_ task: URLSessionWebSocketTask) async -> Result {
        let setup = #"{"setup":{"model":"models/\#(model)","generationConfig":{"responseModalities":["AUDIO"]}}}"#
        do {
            try await task.send(.string(setup))
            let text: String
            switch try await task.receive() {
            case .string(let s): text = s
            case .data(let d): text = String(decoding: d, as: UTF8.self)
            @unknown default: text = ""
            }
            return text.contains("setupComplete") ? .works : classify(text)
        } catch {
            // A refused key closes the socket with a reason, or fails the upgrade with an HTTP status.
            if let reason = closeReason(task) { return classify(reason) }
            switch (task.response as? HTTPURLResponse)?.statusCode {
            case 401, 403: return .rejected
            case 429: return .limited
            case .some(let code) where code >= 400: return .refused("Google answered with error \(code).")
            default: return .offline
            }
        }
    }

    private static func closeReason(_ task: URLSessionWebSocketTask) -> String? {
        guard task.closeCode != .invalid else { return nil }
        let reason = task.closeReason.map { String(decoding: $0, as: UTF8.self) } ?? ""
        return reason.isEmpty ? "Closed with code \(task.closeCode.rawValue)." : reason
    }

    static func classify(_ message: String) -> Result {
        let m = message.lowercased()
        if m.contains("api key not valid") || m.contains("api_key_invalid") || m.contains("unauthenticated")
            || m.contains("invalid api key") || m.contains("api key expired") || m.contains("invalid authentication credentials") || m.contains("access_token_type_unsupported") {
            return .rejected
        }
        if m.contains("quota") || m.contains("resource_exhausted") || m.contains("rate limit") { return .limited }
        let trimmed = message.trimmingCharacters(in: .whitespacesAndNewlines)
        return .refused(trimmed.isEmpty ? "Google didn't say why." : String(trimmed.prefix(240)))
    }
}
