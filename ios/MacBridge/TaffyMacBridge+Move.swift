import AppKit

/// Moves Zero into the Applications folder when it was opened from somewhere it should not live: the disk image,
/// Downloads, the Desktop, or the temporary copy macOS runs an app from before it is moved (App Translocation).
/// Mac permissions such as Accessibility and Screen Recording belong to the copy that asked for them, so a Zero set up
/// from the disk image would lose them once it is moved. The app asks this bundle once at launch, before setup.
extension TaffyMacBridge {
    @objc(offerMoveToApplications)
    func offerMoveToApplications() {
        DispatchQueue.main.async { Self.offerMove() }
    }

    private static func offerMove() {
        let here = Bundle.main.bundleURL.resolvingSymlinksInPath()
        let original = translocatedOriginal(here) ?? here
        guard needsMove(original.path) || here.path.contains("/AppTranslocation/") else { return }

        NSApp.activate(ignoringOtherApps: true)
        let ask = NSAlert()
        ask.messageText = "Move Zero to Applications?"
        ask.informativeText = "Zero needs to be in your Applications folder to set up and keep working. It will move itself there and open again."
        ask.addButton(withTitle: "Move to Applications")
        ask.addButton(withTitle: "Quit")
        guard ask.runModal() == .alertFirstButtonReturn else { return quit() }
        do {
            let dest = try copyToApplications(here)
            relaunch(dest, ejecting: diskImageVolume(original))
        } catch {
            NSLog("Zero could not move itself: %@", String(describing: error))
            let failed = NSAlert()
            failed.messageText = "Zero Could Not Move Itself"
            failed.informativeText = "Drag Zero into the Applications folder in Finder, then open it from there."
            failed.addButton(withTitle: "Show Zero in Finder")
            failed.runModal()
            NSWorkspace.shared.activateFileViewerSelecting([original])
            quit()
        }
    }

    /// Places Zero should not run from. Applications folders, Xcode's build folders and test copies are left alone.
    static func needsMove(_ path: String) -> Bool {
        let home = NSHomeDirectory()
        return path.hasPrefix("/Volumes/") || path.hasPrefix(home + "/Downloads/") || path.hasPrefix(home + "/Desktop/")
    }

    /// /Applications, or ~/Applications when this account cannot write there. An older Zero goes to the Trash first.
    private static func copyToApplications(_ app: URL) throws -> URL {
        let fm = FileManager.default
        var folder = URL(fileURLWithPath: "/Applications")
        if !fm.isWritableFile(atPath: folder.path) {
            folder = fm.homeDirectoryForCurrentUser.appendingPathComponent("Applications")
            try fm.createDirectory(at: folder, withIntermediateDirectories: true)
        }
        #if DEBUG
        // Tests move into a scratch folder, never over the Zero already in Applications.
        if let scratch = ProcessInfo.processInfo.environment["ZERO_MOVE_TO"] { folder = URL(fileURLWithPath: scratch) }
        #endif
        let dest = folder.appendingPathComponent("Zero.app")
        if fm.fileExists(atPath: dest.path) { try fm.trashItem(at: dest, resultingItemURL: nil) }
        try fm.copyItem(at: app, to: dest)
        // The copy keeps the download's quarantine mark, which would make macOS run it from a temporary place again.
        // Gatekeeper already checked this signed, notarized app when it first opened.
        let strip = Process()
        strip.executableURL = URL(fileURLWithPath: "/usr/bin/xattr")
        strip.arguments = ["-dr", "com.apple.quarantine", dest.path]
        try strip.run()
        strip.waitUntilExit()
        return dest
    }

    /// Opens the moved copy once this one has quit, and ejects the disk image it came from.
    private static func relaunch(_ dest: URL, ejecting volume: String?) {
        let pid = ProcessInfo.processInfo.processIdentifier
        var script = "while /bin/kill -0 \(pid) 2>/dev/null; do /bin/sleep 0.2; done; "
        if let volume { script += "/usr/bin/hdiutil detach -quiet \"$1\" >/dev/null 2>&1; " }
        script += "/usr/bin/open \"$0\""
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = ["-c", script, dest.path] + (volume.map { [$0] } ?? [])
        try? p.run()
        quit()
    }

    /// NSApp.terminate does nothing in a Catalyst app, and nothing is set up yet, so this copy simply exits.
    private static func quit() { exit(0) }

    /// "/Volumes/Zero" when the app came from a mounted disk image.
    private static func diskImageVolume(_ app: URL) -> String? {
        let parts = app.path.split(separator: "/")
        guard parts.count >= 2, parts[0] == "Volumes" else { return nil }
        return "/Volumes/\(parts[1])"
    }

    /// Where a translocated app really is, from the Security framework's SecTranslocateCreateOriginalPathForURL.
    private static func translocatedOriginal(_ app: URL) -> URL? {
        guard app.path.contains("/AppTranslocation/"),
              let handle = dlopen("/System/Library/Frameworks/Security.framework/Security", RTLD_LAZY),
              let sym = dlsym(handle, "SecTranslocateCreateOriginalPathForURL") else { return nil }
        typealias Original = @convention(c) (CFURL, UnsafeMutablePointer<Unmanaged<CFError>?>?) -> Unmanaged<CFURL>?
        let fn = unsafeBitCast(sym, to: Original.self)
        return fn(app as CFURL, nil)?.takeRetainedValue() as URL?
    }
}
