// Loads the built TaffyMacBridge.bundle and runs one engine command through `engine:events:`, the same call the
// Mac app makes, printing each event. Bundle.main is this tool, so its folder needs an `Engine` folder beside it
// (run-bridge-test.sh links the staged installer there).
//   bridge-harness <path to TaffyMacBridge.bundle> <action> [args...]
import AppKit

let argv = CommandLine.arguments
guard argv.count >= 3, let bundle = Bundle(url: URL(fileURLWithPath: argv[1])), (try? bundle.loadAndReturnError()) != nil,
      let cls = bundle.principalClass as? NSObject.Type else { print("could not load the bridge"); exit(2) }
let bridge = cls.init()
let command: NSDictionary = ["action": argv[2], "args": Array(argv.dropFirst(3))]
let block: @convention(block) (NSDictionary) -> Void = { d in
    if d["type"] as? String == "line" { print("line:", d["text"] as? String ?? "") }
    if d["type"] as? String == "exit" {
        print("exit:", d["code"] as? Int ?? -1)
        exit(Int32(d["code"] as? Int ?? 1))
    }
}
_ = bridge.perform(NSSelectorFromString("engine:events:"), with: command, with: unsafeBitCast(block, to: AnyObject.self))
RunLoop.main.run()
