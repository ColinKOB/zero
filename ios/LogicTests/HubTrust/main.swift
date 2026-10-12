// Checks the app's certificate pinning (Taffy/Model/HubTrust.swift) against a real Mac home's TLS port.
//   hubtrust-tests <https://127.0.0.1:PORT> <fingerprint> [pairing code]
// The fingerprint is the one zero-engine put in the pairing link. Without arguments only the offline checks run.
import Foundation

var failures = 0
func check(_ ok: Bool, _ what: String) { print(ok ? "ok  " : "FAIL", what); if !ok { failures += 1 } }

check(HubTrust.normalized("AB:CD" + String(repeating: "0", count: 60)) == "abcd" + String(repeating: "0", count: 60), "fingerprints are normalized")
check(HubTrust.normalized("xyz") == nil, "junk is not a fingerprint")
check(HubTrust.isLocal(URL(string: "https://Sams-Mac.local:8653")!), ".local is local")
check(HubTrust.isLocal(URL(string: "https://10.0.0.5:8650")!), "10.x is local")
check(!HubTrust.isLocal(URL(string: "https://alien.tail0000.ts.net:6443")!), "a Tailscale name is not local")
check(!HubTrust.isLocal(URL(string: "http://127.0.0.1:8650")!), "loopback is not local")

// Pairing rules (Fable's release QA, Oct 12): reading a link pins nothing; a pin is stored only after a pairing
// works; a different fingerprint for a pinned address is refused; unpairing forgets the pin.
UserDefaults.standard.removeObject(forKey: "hubPins")
let fpA = String(repeating: "a", count: 64), fpB = String(repeating: "b", count: 64)
let home = URL(string: "https://Sams-Mac.local:8653")!
let link = PairLink.parse("zero://pair?server=https%3A%2F%2FSams-Mac.local%3A8653&code=ABCD2345&fp=\(fpA.uppercased())")
check(link?.fingerprint == fpA && link?.code == "ABCD2345", "a link's fp is read and normalized")
check(HubTrust.pin(for: home) == nil && HubTrust.candidate(for: home) == nil, "parsing a link pins nothing")
check(HubTrust.pairingCheck(home, fingerprint: nil) == .refuse(HubTrust.unpinned), "a local address without fp is refused")
check(HubTrust.pairingCheck(URL(string: "http://Sams-Mac.local:8650")!, fingerprint: fpA) == .refuse(HubTrust.insecure),
      "plain http on the Wi-Fi is refused, fp or not")
check(HubTrust.pairingCheck(URL(string: "http://127.0.0.1:8650")!, fingerprint: nil) == .go(candidate: nil),
      "the Mac app's loopback line is allowed")
check(HubTrust.pairingCheck(URL(string: "https://alien.tail0000.ts.net:6443")!, fingerprint: nil) == .go(candidate: nil),
      "a Tailscale home without fp keeps the system's normal check")
check(HubTrust.pairingCheck(home, fingerprint: fpA) == .go(candidate: fpA), "a link's fp is a candidate for the pairing")
HubTrust.beginPairing(home, candidate: fpA)
check(HubTrust.effectivePin(for: home) == fpA && HubTrust.pin(for: home) == nil, "the candidate applies during the request only")
HubTrust.finishPairing(home, succeeded: false)
check(HubTrust.pin(for: home) == nil && HubTrust.effectivePin(for: home) == nil, "a failed pairing stores no pin")
HubTrust.beginPairing(home, candidate: fpA)
HubTrust.finishPairing(home, succeeded: true)
check(HubTrust.pin(for: home) == fpA, "the pin is stored after a successful pairing")
check(HubTrust.pairingCheck(home, fingerprint: fpB) == .refuse(HubTrust.mismatch), "a different fp for a pinned address is refused")
check(HubTrust.pin(for: home) == fpA, "the stored pin was not replaced")
check(HubTrust.pairingCheck(home, fingerprint: fpA) == .go(candidate: nil), "the same fp pairs again with the stored pin")
HubTrust.beginPairing(home, candidate: fpB)
HubTrust.finishPairing(home, succeeded: true)
check(HubTrust.pin(for: home) == fpA, "finishing a pairing never overwrites an existing pin")
HubTrust.removePin(for: home.absoluteString)
check(HubTrust.pin(for: home) == nil, "unpairing clears the pin")
check(HubTrust.pairingCheck(home, fingerprint: fpB) == .go(candidate: fpB), "after removal the reinstalled Mac can pair")
UserDefaults.standard.removeObject(forKey: "hubPins")

func get(_ url: String) async -> (Int, String) {
    var r = URLRequest(url: URL(string: url)!)
    r.timeoutInterval = 10
    do {
        let (data, resp) = try await HubSession.shared.data(for: r)
        return ((resp as? HTTPURLResponse)?.statusCode ?? 0, String(decoding: data, as: UTF8.self))
    } catch { return (-1, "\((error as? URLError)?.code.rawValue ?? 0)") }
}

let args = CommandLine.arguments
if args.count >= 3 {
    let server = args[1], fp = args[2]
    UserDefaults.standard.removeObject(forKey: "hubPins")
    var (code, _) = await get(server + "/api/health")
    check(code == -1, "a self-signed home with no pin is refused")
    HubTrust.setPin(String(repeating: "0", count: 64), for: server)
    (code, _) = await get(server + "/api/health")
    check(code == -1, "a wrong fingerprint is refused")
    HubTrust.setPin(fp, for: server)
    (code, _) = await get(server + "/api/health")
    check(code == 200, "the pinned certificate is accepted")
    if args.count >= 4 {
        var r = URLRequest(url: URL(string: server + "/api/pair")!)
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try! JSONSerialization.data(withJSONObject: ["code": args[3], "device_name": "Pin Test"])
        let (data, resp) = (try? await HubSession.shared.data(for: r)) ?? (Data(), URLResponse())
        let token = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["token"] as? String
        check((resp as? HTTPURLResponse)?.statusCode == 200 && (token?.count ?? 0) > 20, "pairing over the pinned connection works")
    }
    (code, _) = await get("https://www.apple.com/")
    check(code == 200, "an unpinned public address still gets the system's normal check")
    UserDefaults.standard.removeObject(forKey: "hubPins")
}
print(failures == 0 ? "all passed" : "\(failures) failed")
exit(failures == 0 ? 0 : 1)
