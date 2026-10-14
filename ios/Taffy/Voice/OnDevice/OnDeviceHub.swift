import Foundation

extension HubClient {
    struct LocalCallStart: Decodable {
        let call_id: String
        let greeting: String
        let greeting_late: String?
        let voice: String
    }

    func startLocalVoice(bot: String, voice: String, reason: String, chosen: Bool) async throws -> LocalCallStart {
        struct Body: Encodable { let voice: String; let reason: String; let voice_chosen: Bool }
        var r = try request("/api/bots/\(bot)/voice/local", method: "POST",
                            body: Body(voice: voice, reason: reason, voice_chosen: chosen))
        r.timeoutInterval = 20
        return try await send(r, as: LocalCallStart.self)
    }

    func startVoiceCall(bot: String, offer: String, voice: String, chosen: Bool) async throws
        -> (callID: String, answer: String, voice: String?) {
        struct Body: Encodable { let sdp: String; let voice: String; let voice_chosen: Bool }
        struct Resp: Decodable { let call_id: String; let sdp: String; let voice: String? }
        var r = try request("/api/bots/\(bot)/voice", method: "POST", body: Body(sdp: offer, voice: voice, voice_chosen: chosen))
        r.timeoutInterval = 45
        let resp = try await send(r, as: Resp.self)
        return (resp.call_id, resp.sdp, resp.voice)
    }

    func voiceReady(call: String, waitedMs: Int) async {
        struct Body: Encodable { let waited_ms: Int }
        struct Ok: Decodable { let ok: Bool }
        for attempt in 0..<2 {
            guard let r = try? request("/api/voice/\(call)/ready", method: "POST", body: Body(waited_ms: waitedMs)) else { return }
            if (try? await send(r, as: Ok.self)) != nil { return }
            if attempt == 0 { try? await Task.sleep(for: .seconds(1)) }
        }
    }

    struct VoiceEngines: Decodable {
        struct ChatGPT: Decodable {
            let ok: Bool
            let reason: String?
            let minutes_left: Int?
        }
        let chatgpt: ChatGPT
        let greeting: String?
        let greeting_late: String?
        let voice: String?
        let on_device_voice: String?
    }

    func voiceEngines(timeout: TimeInterval = 4) async -> VoiceEngines? {
        guard var r = try? request("/api/voice/engines") else { return nil }
        r.timeoutInterval = timeout
        return try? await send(r, as: VoiceEngines.self)
    }

    struct ReplyEvent: Decodable {
        let type: String
        let text: String?
        let handoff: String?
        let error: Bool?
    }

    func voiceTurn(call: String, n: Int, text: String?, news: Int?, speculative: Bool) -> AsyncThrowingStream<ReplyEvent, Error> {
        struct Body: Encodable { let n: Int; let text: String?; let news: Int?; let speculative: Bool }
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var r = try self.request("/api/voice/\(call)/turn", method: "POST",
                                             body: Body(n: n, text: text, news: news, speculative: speculative))
                    r.timeoutInterval = 60
                    let (bytes, resp) = try await HubSession.shared.bytes(for: r)
                    let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
                    guard code == 200 else { throw HubError.http(code, "") }
                    for try await line in bytes.lines {
                        guard line.hasPrefix("data:") else { continue }
                        let json = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                        if let ev = try? JSONDecoder().decode(ReplyEvent.self, from: Data(json.utf8)) {
                            continuation.yield(ev)
                            if ev.type == "done" { break }
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    func voiceConfirm(call: String, n: Int) async {
        struct Body: Encodable { let n: Int }
        struct Ok: Decodable { let ok: Bool }
        guard var r = try? request("/api/voice/\(call)/confirm", method: "POST", body: Body(n: n)) else { return }
        r.timeoutInterval = 10
        _ = try? await send(r, as: Ok.self)
    }

    func voiceSaid(call: String, n: Int, spoken: String, cutOff: Bool, firstAudioMs: Int?) async {
        struct Body: Encodable { let n: Int; let spoken: String; let cut_off: Bool; let first_audio_ms: Int? }
        struct Ok: Decodable { let ok: Bool }
        guard var r = try? request("/api/voice/\(call)/said", method: "POST",
                                   body: Body(n: n, spoken: spoken, cut_off: cutOff, first_audio_ms: firstAudioMs)) else { return }
        r.timeoutInterval = 10
        _ = try? await send(r, as: Ok.self)
    }
}
