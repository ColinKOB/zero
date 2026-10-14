import AVFoundation
import Foundation
import WebRTC

/// The live audio connection for ChatGPT-voice calls. The hub hands the phone's offer to Codex on
/// alien, which opens the realtime session; audio then flows between this iPhone and the voice
/// directly. Captions and the bot's replies come from the hub separately (see CallEngine).
final class RealtimeLink: NSObject {
    private static let factory: RTCPeerConnectionFactory = {
        RTCInitializeSSL()
        let audio = RTCAudioSessionConfiguration.webRTC()
        audio.category = AVAudioSession.Category.playAndRecord.rawValue
        audio.mode = AVAudioSession.Mode.voiceChat.rawValue
        audio.categoryOptions = [.defaultToSpeaker, .allowBluetoothHFP, .allowBluetoothA2DP]
        RTCAudioSessionConfiguration.setWebRTC(audio)
        return RTCPeerConnectionFactory(encoderFactory: RTCDefaultVideoEncoderFactory(),
                                        decoderFactory: RTCDefaultVideoDecoderFactory())
    }()

    private var pc: RTCPeerConnection?
    private var mic: RTCAudioTrack?
    private var channel: RTCDataChannel?
    private var gathered: CheckedContinuation<Void, Never>?

    /// Called on the main thread when the connection drops for good.
    var onDisconnect: (() -> Void)?
    /// Called once on the main thread when audio is flowing both ways.
    var onConnect: (() -> Void)?

    var micEnabled: Bool {
        get { mic?.isEnabled ?? false }
        set { mic?.isEnabled = newValue }
    }

    /// Silences the voice on this phone without touching the session (used to cut it off).
    var voiceEnabled = true {
        didSet {
            for t in pc?.transceivers ?? [] where t.mediaType == .audio {
                t.receiver.track?.isEnabled = voiceEnabled
            }
        }
    }

    /// How loud the voice plays on this phone, 0 to 1: turned down at once when the person talks over it, and to
    /// nothing while a phone call rings or the call is on hold (CallEngine.applyPause).
    var voiceVolume: Double = 1 {
        didSet {
            guard voiceVolume != oldValue else { return }
            for t in pc?.transceivers ?? [] where t.mediaType == .audio {
                (t.receiver.track as? RTCAudioTrack)?.source.volume = voiceVolume   // a gain: 1 is unchanged
            }
        }
    }

    /// The type of each message on the realtime events channel, on the main thread (saved with the call, to learn
    /// what the voice's service reports there).
    var onChannelMessage: ((String) -> Void)?

    /// Builds the connection and returns the offer to send to the hub, with ICE candidates included.
    func makeOffer() async throws -> String {
        let config = RTCConfiguration()
        config.sdpSemantics = .unifiedPlan
        config.continualGatheringPolicy = .gatherOnce
        let none = RTCMediaConstraints(mandatoryConstraints: nil, optionalConstraints: nil)
        guard let pc = Self.factory.peerConnection(with: config, constraints: none, delegate: self) else {
            throw URLError(.cannotConnectToHost)
        }
        self.pc = pc
        let track = Self.factory.audioTrack(with: Self.factory.audioSource(with: none), trackId: "mic")
        pc.add(track, streamIds: ["taffy"])
        mic = track
        channel = pc.dataChannel(forLabel: "oai-events", configuration: RTCDataChannelConfiguration())
        channel?.delegate = self

        let offer: RTCSessionDescription = try await withCheckedThrowingContinuation { c in
            pc.offer(for: none) { sdp, error in
                if let sdp { c.resume(returning: sdp) } else { c.resume(throwing: error ?? URLError(.unknown)) }
            }
        }
        try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, Error>) in
            pc.setLocalDescription(offer) { error in
                if let error { c.resume(throwing: error) } else { c.resume() }
            }
        }
        if pc.iceGatheringState != .complete {
            await withTaskGroup(of: Void.self) { group in
                group.addTask { await withCheckedContinuation { self.gathered = $0 } }
                group.addTask { try? await Task.sleep(for: .seconds(3)) }
                await group.next()
                group.cancelAll()
                self.finishGathering()
            }
        }
        guard let sdp = pc.localDescription?.sdp else { throw URLError(.cannotConnectToHost) }
        return sdp
    }

    func accept(answer sdp: String) async throws {
        guard let pc else { throw URLError(.cannotConnectToHost) }
        try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, Error>) in
            pc.setRemoteDescription(RTCSessionDescription(type: .answer, sdp: sdp)) { error in
                if let error { c.resume(throwing: error) } else { c.resume() }
            }
        }
    }

    /// Loudspeaker or earpiece, like the Speaker button in the Phone app.
    static func setSpeaker(_ on: Bool) {
        let session = RTCAudioSession.sharedInstance()
        session.lockForConfiguration()
        defer { session.unlockForConfiguration() }
        var options: AVAudioSession.CategoryOptions = [.allowBluetoothHFP, .allowBluetoothA2DP]
        if on { options.insert(.defaultToSpeaker) }
        try? session.setCategory(.playAndRecord, mode: .voiceChat, options: options)
        try? session.overrideOutputAudioPort(on ? .speaker : .none)
    }

    /// Current loudness of the mic and of the voice, each from 0 to 1.
    func levels() async -> (mic: Float, voice: Float) {
        guard let pc else { return (0, 0) }
        return await withCheckedContinuation { c in
            pc.statistics { report in
                var mic: Float = 0, voice: Float = 0
                for s in report.statistics.values {
                    guard let level = (s.values["audioLevel"] as? NSNumber)?.floatValue else { continue }
                    if s.type == "media-source" { mic = level }
                    if s.type == "inbound-rtp" { voice = level }
                }
                c.resume(returning: (mic, voice))
            }
        }
    }

    func close() {
        finishGathering()
        onDisconnect = nil
        onConnect = nil
        onChannelMessage = nil
        channel?.delegate = nil
        channel?.close()
        pc?.close()
        pc = nil
        mic = nil
    }

    private func finishGathering() {
        let c = gathered
        gathered = nil
        c?.resume()
    }
}

extension RealtimeLink: RTCPeerConnectionDelegate {
    func peerConnection(_ pc: RTCPeerConnection, didChange state: RTCIceGatheringState) {
        if state == .complete { finishGathering() }
    }

    func peerConnection(_ pc: RTCPeerConnection, didChange state: RTCPeerConnectionState) {
        if state == .connected {
            DispatchQueue.main.async { [weak self] in
                let c = self?.onConnect
                self?.onConnect = nil
                c?()
            }
            return
        }
        guard state == .failed || state == .closed else { return }
        DispatchQueue.main.async { [weak self] in self?.onDisconnect?() }
    }

    func peerConnection(_ pc: RTCPeerConnection, didChange stateChanged: RTCSignalingState) {}
    func peerConnection(_ pc: RTCPeerConnection, didAdd stream: RTCMediaStream) {}
    func peerConnection(_ pc: RTCPeerConnection, didRemove stream: RTCMediaStream) {}
    func peerConnectionShouldNegotiate(_ pc: RTCPeerConnection) {}
    func peerConnection(_ pc: RTCPeerConnection, didChange newState: RTCIceConnectionState) {}
    func peerConnection(_ pc: RTCPeerConnection, didGenerate candidate: RTCIceCandidate) {}
    func peerConnection(_ pc: RTCPeerConnection, didRemove candidates: [RTCIceCandidate]) {}
    func peerConnection(_ pc: RTCPeerConnection, didOpen dataChannel: RTCDataChannel) {}
}

extension RealtimeLink: RTCDataChannelDelegate {
    func dataChannelDidChangeState(_ dataChannel: RTCDataChannel) {}

    func dataChannel(_ dataChannel: RTCDataChannel, didReceiveMessageWith buffer: RTCDataBuffer) {
        var type = "binary"
        if !buffer.isBinary {
            let obj = try? JSONSerialization.jsonObject(with: buffer.data) as? [String: Any]
            type = (obj?["type"] as? String).map { String($0.prefix(60)) } ?? "other"
        }
        DispatchQueue.main.async { [weak self] in self?.onChannelMessage?(type) }
    }
}
