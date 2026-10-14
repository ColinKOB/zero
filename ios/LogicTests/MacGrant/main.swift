import CryptoKit
import Foundation

var failures = 0
func check(_ ok: Bool, _ name: String) {
    print((ok ? "ok   " : "FAIL ") + name)
    if !ok { failures += 1 }
}

let key = P256.Signing.PrivateKey()
let now = Date(timeIntervalSince1970: 1_791_650_000)
let expires = Int(now.timeIntervalSince1970) + 115
let (id, bot, task, said) = ("q2v8Zt0aLm", "Career", "Open Pages and read the document names", "can you open pages on my mac")
let sig = try! key.signature(for: MacGrant.message(id: id, bot: bot, task: task, said: said, expires: expires)).derRepresentation

check(MacGrant.verify(key: key.publicKey, signature: sig, id: id, bot: bot, task: task, said: said, expires: expires, now: now),
      "the Mac accepts the phone's signature over the request it shows")
check(!MacGrant.verify(key: key.publicKey, signature: sig, id: id, bot: bot, task: task + " and send them to Theo",
                       said: said, expires: expires, now: now),
      "a task changed after signing fails")
check(!MacGrant.verify(key: key.publicKey, signature: sig, id: id, bot: "Money", task: task, said: said, expires: expires, now: now),
      "another Zip's name fails")
check(!MacGrant.verify(key: key.publicKey, signature: sig, id: "other-id", bot: bot, task: task, said: said, expires: expires, now: now),
      "another request id fails")
check(!MacGrant.verify(key: key.publicKey, signature: sig, id: id, bot: bot, task: task, said: said, expires: expires,
                       now: now.addingTimeInterval(200)),
      "an expired request fails")
check(!MacGrant.verify(key: key.publicKey, signature: sig, id: id, bot: bot, task: task, said: said, expires: expires + 600,
                       now: now),
      "a later expiry than the one signed fails")
check(!MacGrant.verify(key: P256.Signing.PrivateKey().publicKey, signature: sig, id: id, bot: bot, task: task, said: said,
                       expires: expires, now: now),
      "another phone's key fails")
let a = MacGrant.message(id: id, bot: bot, task: "Open Pages\nRead", said: "x", expires: expires)
let b = MacGrant.message(id: id, bot: bot, task: "Open Pages", said: "Read\nx", expires: expires)
check(a != b, "fields with new lines stay apart")
let fp = MacGrant.fingerprint(key.publicKey.x963Representation)
check(fp.count == 19 && fp.split(separator: " ").count == 4, "the fingerprint is four groups of four: \(fp)")
check(fp == MacGrant.fingerprint(key.publicKey.x963Representation), "the fingerprint is the same every time")
check(fp != MacGrant.fingerprint(P256.Signing.PrivateKey().publicKey.x963Representation), "another key has another fingerprint")

print(failures == 0 ? "all MacGrant checks passed" : "\(failures) MacGrant checks failed")
exit(failures == 0 ? 0 : 1)
