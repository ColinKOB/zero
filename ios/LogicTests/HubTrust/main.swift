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
