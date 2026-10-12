import Foundation

/// The hub's side of an On-Device Voice call (hub/taffy_hub/ondevice.py): start, one streamed reply per turn,
/// confirming a speculative turn, and what was actually said.
extension HubClient {
    struct LocalCallStart: Decodable {
        let call_id: String
        let greeting: String
        /// Said instead after a long wait (hub voice.greeting(late=True)); older hubs leave it out.
        let greeting_late: String?
        let voice: String
    }

    /// `chosen`: the person picked `voice` on this device since it last told the hub, so it becomes the home's
    /// voice; otherwise the hub uses the home's voice and says which (one voice on every device, #33, #34).
    func startLocalVoice(bot: String, voice: String, reason: String, chosen: Bool) async throws -> LocalCallStart {
        struct Body: Encodable { let voice: String; let reason: String; let voice_chosen: Bool }
        var r = try request("/api/bots/\(bot)/voice/local", method: "POST",
                            body: Body(voice: voice, reason: reason, voice_chosen: chosen))
        r.timeoutInterval = 20
        return try await send(r, as: LocalCallStart.self)
    }

    /// Starts a ChatGPT-voice call. Returns the hub's call id, its answer to the offer and the voice it used.
    func startVoiceCall(bot: String, offer: String, voice: String, chosen: Bool) async throws
        -> (callID: String, answer: String, voice: String?) {
        struct Body: Encodable { let sdp: String; let voice: String; let voice_chosen: Bool }
        struct Resp: Decodable { let call_id: String; let sdp: String; let voice: String? }
        var r = try request("/api/bots/\(bot)/voice", method: "POST", body: Body(sdp: offer, voice: voice, voice_chosen: chosen))
        r.timeoutInterval = 45
        let resp = try await send(r, as: Resp.self)
        return (resp.call_id, resp.sdp, resp.voice)
    }

    /// The call's audio is connected: the bot says hello. `waitedMs` is how long the person waited since tapping
    /// Call, so a slow start gets an honest hello (#18). Tried twice, since a call without a hello sounds dead.
    func voiceReady(call: String, waitedMs: Int) async {
        struct Body: Encodable { let waited_ms: Int }
        struct Ok: Decodable { let ok: Bool }
        for attempt in 0..<2 {
            guard let r = try? request("/api/voice/\(call)/ready", method: "POST", body: Body(waited_ms: waitedMs)) else { return }
            if (try? await send(r, as: Ok.self)) != nil { return }
            if attempt == 0 { try? await Task.sleep(for: .seconds(1)) }
        }
    }

    /// Whether ChatGPT's voice can take a call now, and the greeting the next call opens with.
    struct VoiceEngines: Decodable {
        struct ChatGPT: Decodable {
            let ok: Bool
            let reason: String?
            let minutes_left: Int?
        }
        let chatgpt: ChatGPT
        let greeting: String?
        let greeting_late: String?
        /// The home's voices, the same on every device (newer hubs only).
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

    /// One reply, as it is written: text pieces, then done. Cancelling the stream tells the hub to stop.
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
