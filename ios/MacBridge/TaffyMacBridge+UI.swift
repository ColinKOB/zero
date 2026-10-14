import AppKit
import ApplicationServices

/// Reading an app's controls and acting on them without the mouse.
///
/// Oct 10 head-to-head (docs/qa/2026-10-10-mac-computer-use.md): Zero's own engine was 5 to 10 times faster per
/// action than the other engines on Colin's Mac and never took his mouse, but it lost on System Settings and on
/// pop-up menus. These fixes come from what the other engines do: rows are selected rather than pressed, a pop-up
/// menu is opened and its option pressed, a typed value ends with a real key event so search fields and web pages
/// notice it, scrolling moves the scroll bar through accessibility, and every control gets a name, even a switch
/// with no title of its own. The way unlabeled controls borrow the words on their row, and the idea of scrolling
/// and clicking through accessibility first, follow trycua's cua-driver (MIT License, Copyright (c) 2025 trycua).
extension TaffyMacBridge {
    struct Item {
        let element: AXUIElement
        let role: String
        let label: String
        let value: String?
        let center: CGPoint?
        let depth: Int
        var name: String
    }

    static let listedRoles: Set<String> = [
        "AXButton", "AXTextField", "AXTextArea", "AXLink", "AXMenuItem", "AXCheckBox", "AXRadioButton",
        "AXPopUpButton", "AXComboBox", "AXTab", "AXStaticText", "AXCell", "AXSearchField", "AXSecureTextField",
        "AXMenuBarItem", "AXMenuButton", "AXSlider", "AXSwitch", "AXDisclosureTriangle", "AXRow", "AXIncrementor",
    ]
    static let maxListed = 400

    static func attr(_ e: AXUIElement, _ name: String) -> AnyObject? {
        var v: AnyObject?
        return AXUIElementCopyAttributeValue(e, name as CFString, &v) == .success ? v : nil
    }

    static func actions(_ e: AXUIElement) -> [String] {
        var names: CFArray?
        return AXUIElementCopyActionNames(e, &names) == .success ? (names as? [String] ?? []) : []
    }

    static func frame(_ e: AXUIElement) -> CGRect? {
        guard let pv = attr(e, kAXPositionAttribute), let sv = attr(e, kAXSizeAttribute) else { return nil }
        var p = CGPoint.zero, s = CGSize.zero
        AXValueGetValue(pv as! AXValue, .cgPoint, &p)
        AXValueGetValue(sv as! AXValue, .cgSize, &s)
        return s.width > 0 && s.height > 0 ? CGRect(origin: p, size: s) : nil
    }

    static func clean(_ s: String, _ limit: Int = 120) -> String {
        let one = s.replacingOccurrences(of: "\n", with: " ").trimmingCharacters(in: .whitespaces)
        return one.count > limit ? String(one.prefix(limit - 1)) + "…" : one
    }

    // MARK: Reading

    func readItems(_ app: NSRunningApplication) -> (items: [Item], windows: Int) {
        let root = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(root, 2)
        var found: [Item] = []
        func walk(_ e: AXUIElement, depth: Int) {
            guard found.count < Self.maxListed, depth < 30 else { return }
            let role = Self.attr(e, kAXRoleAttribute) as? String ?? ""
            if Self.listedRoles.contains(role) {
                let title = Self.attr(e, kAXTitleAttribute) as? String
                let desc = Self.attr(e, kAXDescriptionAttribute) as? String
                let raw = Self.attr(e, kAXValueAttribute)
                let text = raw as? String
                let placeholder = Self.attr(e, kAXPlaceholderValueAttribute as String) as? String
                let isField = ["AXTextField", "AXTextArea", "AXSearchField", "AXComboBox", "AXSecureTextField"].contains(role)
                let isSwitch = ["AXCheckBox", "AXSwitch", "AXRadioButton", "AXDisclosureTriangle"].contains(role)
                var label = [title, desc, isField ? placeholder : text].compactMap { $0 }.first { !$0.isEmpty } ?? ""
                if role == "AXStaticText" { label = text ?? label }
                var value: String? = nil
                if isField { value = role == "AXSecureTextField" ? nil : text }
                if isSwitch, let n = raw as? NSNumber { value = n.intValue == 0 ? "off" : (n.intValue == 1 ? "on" : "mixed") }
                if role == "AXPopUpButton" || role == "AXMenuButton" { value = text }
                if role == "AXSlider", let n = raw as? NSNumber { value = "\(n)" }
                let center = Self.frame(e).map { CGPoint(x: $0.midX, y: $0.midY) }
                let keep = role == "AXRow" ? true : center != nil
                if keep, !(role == "AXStaticText" && label.isEmpty) {
                    found.append(Item(element: e, role: role, label: label, value: value, center: center, depth: depth,
                                      name: label))
                }
            }
            let kids = Self.attr(e, kAXChildrenAttribute) as? [AXUIElement] ?? []
            for child in kids { walk(child, depth: depth + 1) }
        }
        var windows = Self.attr(root, kAXWindowsAttribute) as? [AXUIElement] ?? []
        if let focused = Self.attr(root, kAXFocusedWindowAttribute) {
            let f = focused as! AXUIElement
            windows.removeAll { CFEqual($0, f) }
            windows.insert(f, at: 0)
        }
        for w in windows { walk(w, depth: 0) }
        if let menu = Self.attr(root, kAXMenuBarAttribute) { walk(menu as! AXUIElement, depth: 0) }
        return (Self.borrowNames(found), windows.count)
    }

    static func borrowNames(_ items: [Item]) -> [Item] {
        var out = items
        let borrowers: Set<String> = ["AXCheckBox", "AXSwitch", "AXTextField", "AXSearchField", "AXTextArea",
                                      "AXPopUpButton", "AXComboBox", "AXSlider", "AXRadioButton", "AXRow", "AXCell"]
        for i in out.indices where borrowers.contains(out[i].role) && (out[i].name.isEmpty || out[i].role == "AXRow" || out[i].role == "AXCell") {
            if out[i].role == "AXRow" || out[i].role == "AXCell" {
                var j = i + 1
                while j < out.count, out[j].depth > out[i].depth {
                    if out[j].role == "AXStaticText", !out[j].label.isEmpty { out[i].name = out[j].label; break }
                    j += 1
                }
                if !out[i].name.isEmpty { continue }
            }
            guard let c = out[i].center else { continue }
            let row = out.enumerated().filter { k, o in
                k != i && o.role == "AXStaticText" && !o.label.isEmpty
                    && o.center.map { abs($0.y - c.y) <= 10 && abs($0.x - c.x) <= 450 } == true
            }.min { a, b in abs(a.element.center!.x - c.x) < abs(b.element.center!.x - c.x) }
            if let row { out[i].name = row.element.label; continue }
            let beside = out.enumerated().first { k, o in
                k != i && !o.label.isEmpty && o.role != "AXStaticText" && o.center.map { abs($0.y - c.y) <= 6 } == true
            }
            if let beside { out[i].name = beside.element.label }
        }
        return out
    }

    func workingApp() -> NSRunningApplication? {
        let app = targetPid != 0 ? NSRunningApplication(processIdentifier: targetPid) : nil
        return (app?.isTerminated == false ? app : nil) ?? NSWorkspace.shared.frontmostApplication
    }

    func uiList(_ args: [String: Any], _ ok: (Any) -> Void, _ fail: (String) -> Void) {
        if needAccessibility(fail) { return }
        guard let app = workingApp() else { return fail("No app is in front.") }
        if let reason = appRefusal(app.processIdentifier) { return fail(reason) }
        targetPid = app.processIdentifier
        let (found, windowCount) = readItems(app)
        items = found
        elements = found.map(\.element)
        let name = app.localizedName ?? "unknown"
        var lines: [String] = ["App: \(name). Press an element by its number with mac_press, or by its name."]
        if windowCount == 0 {
            lines.append("\(name) has no window on this screen. It may be full screen or in another Space, or have no "
                         + "window open. mac_open with front true brings it here.")
        }
        let query = (args["query"] as? String ?? "").lowercased().trimmingCharacters(in: .whitespaces)
        var row: Item? = nil
        for (i, item) in found.enumerated() {
            if item.role == "AXRow" { row = item } else if let r = row, item.depth <= r.depth { row = nil }
            if let r = row, item.role != "AXRow", item.role == "AXCell" || item.role == "AXStaticText", item.name == r.name {
                continue
            }
            let line = Self.describe(item, number: i + 1)
            if !query.isEmpty, !line.lowercased().contains(query) { continue }
            lines.append(String(repeating: "  ", count: min(item.depth, 6)) + line)
        }
        if found.count >= Self.maxListed {
            lines.append("The list stops at \(Self.maxListed) controls. Give query to look for a word, or scroll.")
        }
        ok(lines.joined(separator: "\n"))
    }

    static func describe(_ item: Item, number: Int) -> String {
        let role = item.role.dropFirst(2)
        var shown = item.role == "AXSecureTextField" ? "(password field)" : clean(item.name)
        if item.label.isEmpty && !item.name.isEmpty && item.role != "AXRow" && item.role != "AXCell" { shown += " (by its row)" }
        var line = "[\(number)] \(role) \"\(shown)\""
        if let v = item.value, item.role != "AXSecureTextField", !v.isEmpty, v != item.label { line += " = \"\(clean(v, 80))\"" }
        if let c = item.center { line += " at (\(Int(c.x)), \(Int(c.y)))" } else { line += " (scrolled out of view)" }
        return line
    }

    // MARK: Finding a control by name (the free fast mode)

    static func ranked(_ phrase: String, in items: [Item], roles: Set<String>? = nil) -> [(score: Double, index: Int)] {
        let stop: Set<String> = ["the", "a", "an", "my", "me", "in", "into", "on", "onto", "for", "of", "to", "it", "this",
                                 "that", "please", "and", "with", "under", "list", "app", "apps", "settings", "setting",
                                 "click", "tap", "press", "open", "go", "choose", "select", "pick", "type", "fill",
                                 "write", "turn", "toggle", "tick", "check", "uncheck", "enter", "put", "hit", "use", "show",
                                 "field", "box", "area", "bar", "button", "checkbox", "switch", "off"]
        let hints: [String: Set<String>] = [
            "button": ["AXButton"], "field": ["AXTextField", "AXSearchField", "AXTextArea", "AXComboBox"],
            "box": ["AXTextField", "AXTextArea", "AXCheckBox", "AXSearchField"], "bar": ["AXTextField", "AXSearchField"],
            "area": ["AXTextArea"], "checkbox": ["AXCheckBox"], "switch": ["AXCheckBox", "AXSwitch"], "tick": ["AXCheckBox"],
            "toggle": ["AXCheckBox", "AXSwitch"], "turn": ["AXCheckBox", "AXSwitch"], "menu": ["AXMenuBarItem", "AXMenuItem"],
            "tab": ["AXRadioButton", "AXTab"], "type": ["AXTextField", "AXTextArea", "AXSearchField"],
            "fill": ["AXTextField", "AXTextArea"], "write": ["AXTextArea", "AXTextField"],
            "choose": ["AXPopUpButton", "AXMenuItem", "AXRadioButton"], "search": ["AXTextField", "AXSearchField"],
        ]
        let synonyms: [String: String] = ["copy": "duplicate", "refresh": "reload", "previous": "back",
                                          "address": "smart search field", "url": "smart search field"]
        func words(_ s: String) -> [String] {
            s.lowercased().split { !($0.isLetter || $0.isNumber || $0 == "&") }.map(String.init)
        }
        let said = words(phrase)
        let wanted = said.reduce(into: Set<String>()) { $0.formUnion(hints[$1] ?? []) }
        var content = said.flatMap { words(synonyms[$0] ?? $0) }.filter { !stop.contains($0) }
        if content.isEmpty { content = said }
        guard !content.isEmpty else { return [] }
        var scored: [(score: Double, index: Int)] = []
        for (i, item) in items.enumerated() {
            if let roles, !roles.contains(item.role) { continue }
            let have = words(item.name)
            guard !have.isEmpty else { continue }
            let hit = content.filter { c in have.contains { $0 == c || (c.count > 3 && $0.hasPrefix(String(c.prefix(4)))) } }.count
            guard hit > 0 else { continue }
            var s = Double(hit) / Double(content.count) + 0.5 * Double(hit) / Double(have.count)
            if wanted.contains(item.role) { s += 0.3 }
            if item.role == "AXStaticText" && !wanted.isEmpty { s -= 0.2 }
            if item.role == "AXCell" { s -= 0.05 }
            if phrase.contains(item.name) { s += 0.15 }
            if content.count > 1 && have.joined(separator: " ").contains(content.joined(separator: " ")) { s += 0.3 }
            if item.label.isEmpty && item.role == "AXButton" { s -= 0.1 }
            scored.append((s, i))
        }
        return scored.sorted { $0.score > $1.score }
    }

    static func find(_ phrase: String, in items: [Item], roles: Set<String>? = nil) -> (index: Int, sure: Bool)? {
        let scored = ranked(phrase, in: items, roles: roles)
        guard let best = scored.first else { return nil }
        let second = scored.count > 1 ? scored[1].score : 0
        return (best.index, best.score >= 1.2 || (best.score - second) / best.score >= 0.25)
    }

    func target(_ args: [String: Any], _ fail: (String) -> Void) -> (AXUIElement, Int)? {
        if let n = args["element"] as? Int {
            guard n >= 1, n <= elements.count else {
                fail("No element with that number. Call mac_ui again for fresh numbers.")
                return nil
            }
            return (elements[n - 1], n)
        }
        guard let name = (args["name"] as? String)?.trimmingCharacters(in: .whitespaces), !name.isEmpty else {
            fail("Say which element: its number from mac_ui, or its name.")
            return nil
        }
        guard let app = workingApp() else { fail("No app is in front."); return nil }
        if let reason = appRefusal(app.processIdentifier) { fail(reason); return nil }
        targetPid = app.processIdentifier
        items = readItems(app).items
        elements = items.map(\.element)
        let fillable: Set<String> = ["AXTextField", "AXTextArea", "AXSearchField", "AXComboBox", "AXPopUpButton",
                                     "AXMenuButton", "AXSecureTextField"]
        let roles: Set<String>? = args["text"] != nil ? fillable
            : (Self.find(name, in: items, roles: Self.listedRoles.subtracting(["AXStaticText"])) != nil
               ? Self.listedRoles.subtracting(["AXStaticText"]) : nil)
        guard let hit = Self.find(name, in: items, roles: roles) else {
            fail("Nothing called \"\(name)\" is on the screen in \(app.localizedName ?? "this app"). Call mac_ui to see what is there.")
            return nil
        }
        let ranked = Self.ranked(name, in: items, roles: roles)
        let near = ranked.prefix(3).map { Self.describe(items[$0.index], number: $0.index + 1) }
        guard hit.sure else {
            fail("More than one control could be \"\(name)\": " + near.joined(separator: "; ") + ". Press one by its number.")
            return nil
        }
        if Self.weighty(name) {
            let exact = items[hit.index].name.lowercased().trimmingCharacters(in: .whitespaces)
                == name.lowercased().trimmingCharacters(in: .whitespaces)
            let best = ranked.first?.score ?? 0, second = ranked.count > 1 ? ranked[1].score : 0
            if !exact && !(best > 0 && second / best <= 0.5) {
                fail("\"\(name)\" sends, pays or removes something, and no control has exactly those words. "
                     + "Closest: " + near.joined(separator: "; ") + ". Press one by its number if it is right.")
                return nil
            }
        }
        return (elements[hit.index], hit.index + 1)
    }

    static func weighty(_ phrase: String) -> Bool {
        let words = Set(phrase.lowercased().split { !$0.isLetter }.map(String.init))
        let risky: Set<String> = ["send", "delete", "remove", "buy", "pay", "purchase", "submit", "confirm",
                                  "unsubscribe", "erase", "trash", "transfer", "checkout", "order"]
        return !words.isDisjoint(with: risky) || phrase.lowercased().contains("sign out")
            || phrase.lowercased().contains("log out")
    }

    static func switchRefusal(_ e: AXUIElement) -> String? {
        var pid: pid_t = 0
        AXUIElementGetPid(e, &pid)
        guard NSRunningApplication(processIdentifier: pid)?.bundleIdentifier == "com.apple.systempreferences" else { return nil }
        var cur: AXUIElement? = e
        var isSwitch = false
        var window: AXUIElement?
        for _ in 0..<12 {
            guard let c = cur else { break }
            let role = attr(c, kAXRoleAttribute) as? String ?? ""
            if role == "AXCheckBox" || role == "AXSwitch" { isSwitch = true }
            if role == "AXButton", cur.map({ CFEqual($0, e) }) == true {
                let words = ((attr(c, kAXTitleAttribute) as? String) ?? (attr(c, kAXDescriptionAttribute) as? String) ?? "")
                    .lowercased()
                if ["add", "remove", "+", "-", "−"].contains(words) { isSwitch = true }
            }
            if (attr(c, kAXIdentifierAttribute as String) as? String)?.hasSuffix("_Toggle") == true { isSwitch = true }
            if role == "AXWindow" { window = c; break }
            cur = attr(c, kAXParentAttribute).map { $0 as! AXUIElement }
        }
        guard isSwitch else { return nil }
        let title = window.flatMap { attr($0, kAXTitleAttribute) as? String } ?? ""
        let privacy: Set<String> = ["Privacy & Security", "Accessibility", "Screen & System Audio Recording",
                                    "Full Disk Access", "Files & Folders", "Automation", "Location Services",
                                    "Contacts", "Calendars", "Reminders", "Photos", "Camera", "Microphone",
                                    "Input Monitoring", "Developer Tools", "App Management", "Bluetooth",
                                    "Local Network", "Remote Desktop", "Speech Recognition", "Media & Apple Music",
                                    "Passkeys Access for Web Browsers", "HomeKit", "Focus", "Motion & Fitness"]
        if privacy.contains(title) || (attr(e, kAXIdentifierAttribute as String) as? String)?.hasSuffix("_Toggle") == true {
            return "Privacy & Security settings are never changed for bots, including adding or removing apps. "
                + "\(title.isEmpty ? "That setting" : title) is something the person changes in System Settings."
        }
        return nil
    }

    func pressRefusal(_ e: AXUIElement) -> String? {
        var pid: pid_t = 0
        AXUIElementGetPid(e, &pid)
        if let reason = appRefusal(pid) { return reason }
        if let app = NSRunningApplication(processIdentifier: pid), let id = app.bundleIdentifier, Self.shells.contains(id) {
            return "\(app.localizedName ?? "That app") can run commands, so bots never press things in it. Use mac_run."
        }
        return Self.switchRefusal(e)
    }

    // MARK: Pressing and filling

    func press(_ args: [String: Any], _ ok: (Any) -> Void, _ fail: (String) -> Void) {
        if needAccessibility(fail) { return }
        if let reason = appRefusal(targetPid) { return fail(reason) }
        guard let (e, n) = target(args, fail) else { return }
        if let reason = pressRefusal(e) { return fail(reason) }
        let role = Self.attr(e, kAXRoleAttribute) as? String ?? ""
        if Self.actions(e).contains("AXScrollToVisible") { AXUIElementPerformAction(e, "AXScrollToVisible" as CFString) }
        if let center = centerOf(e) { moveCursor(to: center, click: true) }
        if let text = args["text"] as? String {
            if let reason = typingRefusal() { return fail(reason) }
            let sub = Self.attr(e, kAXSubroleAttribute) as? String
            if role == "AXSecureTextField" || sub == "AXSecureTextField" {
                return fail("That is a password field. Bots never type passwords; use the saved-logins flow.")
            }
            if role == "AXPopUpButton" || role == "AXMenuButton" { return choose(e, n, text, ok, fail) }
            guard ["AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"].contains(role) else {
                return fail("Element \(n) is \(role.dropFirst(2).lowercased()), not a field. Give the field's number.")
            }
            return fill(e, n, text, ok, fail)
        }
        if (args["action"] as? String) == "menu" {
            return AXUIElementPerformAction(e, kAXShowMenuAction as CFString) == .success
                ? ok("Opened the menu of element \(n).") : fail("Element \(n) has no menu.")
        }
        let before = Self.attr(e, kAXValueAttribute) as? NSNumber
        if let row = Self.rowOf(e), role == "AXRow" || role == "AXCell" || role == "AXStaticText" || !Self.actions(e).contains("AXPress") {
            if Self.select(row) { return ok("Selected element \(n).") }
        }
        if AXUIElementPerformAction(e, kAXPressAction as CFString) == .success {
            if let before, let after = Self.attr(e, kAXValueAttribute) as? NSNumber, after != before {
                return ok("Pressed element \(n); it is now \(after.intValue == 0 ? "off" : "on").")
            }
            return ok("Pressed element \(n).")
        }
        if AXUIElementSetAttributeValue(e, kAXFocusedAttribute as CFString, kCFBooleanTrue) == .success {
            return ok("Focused element \(n); it has no press action.")
        }
        fail("Element \(n) could not be pressed. Try clicking its position instead.")
    }

    static func rowOf(_ e: AXUIElement) -> AXUIElement? {
        var cur: AXUIElement? = e
        for _ in 0..<4 {
            guard let c = cur else { return nil }
            if (attr(c, kAXRoleAttribute) as? String) == "AXRow" { return c }
            cur = attr(c, kAXParentAttribute).map { $0 as! AXUIElement }
        }
        return nil
    }

    /// Selects a row the way a person would. Setting a list's selection through accessibility marks the row but many
    /// apps never act on it (Oct 10: System Settings' sidebar stayed on the old pane, for cua-driver too). So the row
    /// next to it is selected first and an arrow key moves onto the row, which the app handles as its own selection
    /// change. Falls back to the row's press action, then to marking it selected. True when it reads back selected.
    static func select(_ row: AXUIElement) -> Bool {
        if let parent = attr(row, kAXParentAttribute), ["AXOutline", "AXTable", "AXList"].contains(attr(parent as! AXUIElement, kAXRoleAttribute) as? String ?? "") {
            let list = parent as! AXUIElement
            let rows = attr(list, kAXRowsAttribute) as? [AXUIElement] ?? []
            var pid: pid_t = 0
            AXUIElementGetPid(list, &pid)
            if let i = rows.firstIndex(where: { CFEqual($0, row) }), rows.count > 1, pid != 0 {
                let (neighbor, arrow): (AXUIElement, CGKeyCode) = i > 0 ? (rows[i - 1], 125) : (rows[i + 1], 126)
                AXUIElementSetAttributeValue(list, kAXFocusedAttribute as CFString, kCFBooleanTrue)
                if AXUIElementSetAttributeValue(list, kAXSelectedRowsAttribute as CFString, [neighbor] as CFArray) == .success {
                    Thread.sleep(forTimeInterval: 0.15)
                    postKey(arrow, to: pid)
                    Thread.sleep(forTimeInterval: 0.2)
                    if (attr(row, kAXSelectedAttribute) as? Bool) == true { return true }
                }
            }
            if AXUIElementSetAttributeValue(list, kAXSelectedRowsAttribute as CFString, [row] as CFArray) == .success,
               (attr(row, kAXSelectedAttribute) as? Bool) == true { return true }
        }
        if AXUIElementPerformAction(row, kAXPressAction as CFString) == .success { return true }
        return AXUIElementSetAttributeValue(row, kAXSelectedAttribute as CFString, kCFBooleanTrue) == .success
            && (attr(row, kAXSelectedAttribute) as? Bool) == true
    }

    /// Fills a field so the app notices: the text goes in through accessibility, then its last character comes as a
    /// real key press, which is what search boxes and web pages listen for (Oct 10: System Settings' search ignored a
    /// value set alone, and a pop-up reported "Filled" while nothing changed).
    func fill(_ e: AXUIElement, _ n: Int, _ text: String, _ ok: (Any) -> Void, _ fail: (String) -> Void) {
        AXUIElementSetAttributeValue(e, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        guard let last = text.last else {
            return AXUIElementSetAttributeValue(e, kAXValueAttribute as CFString, "" as CFString) == .success
                ? ok("Cleared element \(n).") : fail("That element does not take typed text.")
        }
        let head = String(text.dropLast())
        guard AXUIElementSetAttributeValue(e, kAXValueAttribute as CFString, head as CFString) == .success else {
            return fail("That element does not take typed text. Click into it and use mac_type instead.")
        }
        var pid: pid_t = 0
        AXUIElementGetPid(e, &pid)
        Self.postText(String(last), to: pid != 0 ? pid : targetPid)
        Thread.sleep(forTimeInterval: 0.08)
        var now = Self.attr(e, kAXValueAttribute) as? String ?? ""
        if now != text {
            AXUIElementSetAttributeValue(e, kAXValueAttribute as CFString, text as CFString)
            now = Self.attr(e, kAXValueAttribute) as? String ?? text
        }
        ok(now == text ? "Filled element \(n)." : "Filled element \(n); it now reads \"\(Self.clean(now, 80))\".")
    }

    static func postText(_ s: String, to pid: pid_t) {
        var chars = Array(s.utf16)
        let d = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true)
        let u = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false)
        d?.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: &chars)
        u?.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: &chars)
        d?.postToPid(pid)
        u?.postToPid(pid)
    }

    func choose(_ e: AXUIElement, _ n: Int, _ option: String, _ ok: (Any) -> Void, _ fail: (String) -> Void) {
        let want = option.lowercased().trimmingCharacters(in: .whitespaces)
        var pid: pid_t = 0
        AXUIElementGetPid(e, &pid)
        let appRoot = AXUIElementCreateApplication(pid)
        func menuItems() -> [AXUIElement] {
            var out: [AXUIElement] = []
            func walk(_ x: AXUIElement, _ d: Int, inMenu: Bool) {
                guard d < 8 else { return }
                let role = Self.attr(x, kAXRoleAttribute) as? String ?? ""
                if role == "AXMenuBar" { return }
                if role == "AXMenuItem" && inMenu { out.append(x) }
                for c in (Self.attr(x, kAXChildrenAttribute) as? [AXUIElement] ?? []) {
                    walk(c, d + 1, inMenu: inMenu || role == "AXMenu")
                }
            }
            walk(e, 0, inMenu: true)
            // A web page's pop-up opens its menu apart from the pop-up itself, as the app's own open menu.
            if out.isEmpty { for c in (Self.attr(appRoot, kAXChildrenAttribute) as? [AXUIElement] ?? []) { walk(c, 0, inMenu: false) } }
            return out
        }
        func title(_ x: AXUIElement) -> String { (Self.attr(x, kAXTitleAttribute) as? String ?? "").trimmingCharacters(in: .whitespaces) }
        AXUIElementPerformAction(e, kAXPressAction as CFString)
        var options: [AXUIElement] = []
        for _ in 0..<10 {
            options = menuItems()
            if !options.isEmpty { break }
            Thread.sleep(forTimeInterval: 0.05)
        }
        let pick = options.first { title($0).lowercased() == want } ?? options.first { title($0).lowercased().hasPrefix(want) }
            ?? options.first { title($0).lowercased().contains(want) }
        guard let pick else {
            AXUIElementPerformAction(e, kAXCancelAction as CFString)
            Self.postKey(53, to: pid != 0 ? pid : targetPid)   // Escape, in case the menu ignores cancel
            let names = options.map(title).filter { !$0.isEmpty }.prefix(20).joined(separator: ", ")
            return fail(names.isEmpty ? "Element \(n) did not open a list of options."
                                      : "Element \(n) has no option called \"\(option)\". Its options: \(names).")
        }
        let chosen = title(pick)
        AXUIElementPerformAction(pick, kAXPressAction as CFString)
        var now = ""
        for _ in 0..<10 {   // a web page updates the pop-up a moment later
            Thread.sleep(forTimeInterval: 0.05)
            now = Self.attr(e, kAXValueAttribute) as? String ?? Self.attr(e, kAXTitleAttribute) as? String ?? ""
            if now == chosen { break }
        }
        ok(now == chosen || now.isEmpty ? "Chose \"\(chosen)\" in element \(n)."
                                       : "Pressed \"\(chosen)\" in element \(n); it shows \"\(Self.clean(now, 80))\".")
    }

    static func postKey(_ code: CGKeyCode, to pid: pid_t) {
        CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true)?.postToPid(pid)
        CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false)?.postToPid(pid)
    }

    // MARK: Scrolling

    /// Scrolls the list or page under a point. The scroll bar moves through accessibility first, which works in the
    /// background where many apps ignore wheel events sent to them (Oct 10: System Settings' sidebar did not move).
    func scroll(_ args: [String: Any], _ ok: (Any) -> Void, _ fail: (String) -> Void) {
        if needAccessibility(fail) { return }
        let dy = Int32(args["dy"] as? Int ?? -5)
        guard targetPid != 0 || args["x"] != nil else {
            return fail("No app is chosen to scroll. Open it with mac_open or click into it first.")
        }
        var point: CGPoint? = nil
        if let x = args["x"] as? Double, let y = args["y"] as? Double {
            point = CGPoint(x: x, y: y)
            moveCursor(to: point!, click: false)
        }
        if let p = point, let area = Self.scrollArea(at: p) {
            var pid: pid_t = 0
            AXUIElementGetPid(area, &pid)
            if let reason = appRefusal(pid) { return fail(reason) }
            if let bar = Self.attr(area, kAXVerticalScrollBarAttribute), let v = Self.attr(bar as! AXUIElement, kAXValueAttribute) as? Double {
                let height = Self.frame(area)?.height ?? 600
                let content = max(height, Self.contentHeight(area) ?? height * 3)
                let step = Double(-dy) * 40 / max(1, content - height)
                let new = min(1, max(0, v + step))
                if AXUIElementSetAttributeValue(bar as! AXUIElement, kAXValueAttribute as CFString, NSNumber(value: new)) == .success,
                   let after = Self.attr(bar as! AXUIElement, kAXValueAttribute) as? Double, abs(after - v) > 0.0001 {
                    targetPid = pid
                    return ok("Scrolled \(dy < 0 ? "down" : "up") \(abs(dy)) lines.")
                }
            }
        }
        if let reason = appRefusal(targetPid) { return fail(reason) }
        let e = CGEvent(scrollWheelEvent2Source: nil, units: .line, wheelCount: 1, wheel1: dy, wheel2: 0, wheel3: 0)
        if let p = point {
            e?.location = p
            if let window = Self.windowNumber(pid: targetPid, at: p) {
                e?.setIntegerValueField(.mouseEventWindowUnderMousePointer, value: Int64(window))
                e?.setIntegerValueField(.mouseEventWindowUnderMousePointerThatCanHandleThisEvent, value: Int64(window))
            }
        }
        e?.postToPid(targetPid)
        ok("Scrolled \(dy < 0 ? "down" : "up") \(abs(dy)) lines. If nothing moved, this app ignores scrolling from Zero; "
           + "press a control lower down by its number instead.")
    }

    static func scrollArea(at p: CGPoint) -> AXUIElement? {
        var hit: AXUIElement?
        guard AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), Float(p.x), Float(p.y), &hit) == .success else { return nil }
        var cur = hit
        for _ in 0..<12 {
            guard let c = cur else { return nil }
            if (attr(c, kAXRoleAttribute) as? String) == "AXScrollArea" { return c }
            cur = attr(c, kAXParentAttribute).map { $0 as! AXUIElement }
        }
        return nil
    }

    static func contentHeight(_ area: AXUIElement) -> CGFloat? {
        (attr(area, kAXContentsAttribute) as? [AXUIElement] ?? attr(area, kAXChildrenAttribute) as? [AXUIElement] ?? [])
            .compactMap { frame($0)?.height }.max()
    }

    static func windowNumber(pid: pid_t, at p: CGPoint) -> Int? {
        let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
        for w in list where (w[kCGWindowOwnerPID as String] as? pid_t) == pid && (w[kCGWindowLayer as String] as? Int) == 0 {
            guard let b = w[kCGWindowBounds as String] as? [String: CGFloat] else { continue }
            if CGRect(x: b["X"] ?? 0, y: b["Y"] ?? 0, width: b["Width"] ?? 0, height: b["Height"] ?? 0).contains(p) {
                return w[kCGWindowNumber as String] as? Int
            }
        }
        return nil
    }

    // MARK: Several steps in one call (the fast mode)

    func steps(_ args: [String: Any], _ ok: (Any) -> Void, _ fail: (String) -> Void) {
        if needAccessibility(fail) { return }
        let list = (args["steps"] as? [Any] ?? []).compactMap { $0 as? String }.prefix(15)
        guard !list.isEmpty else { return fail("Give steps as a list of short sentences.") }
        var done: [String] = []
        func listing() -> String {
            var out = ""
            uiList([:], { out = "\($0)" }, { out = $0 })
            return out
        }
        for raw in list {
            let step = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            var failure: String? = nil
            var result = ""
            let okStep: (Any) -> Void = { result = "\($0)" }
            let failStep: (String) -> Void = { failure = $0 }
            let lower = step.lowercased()
            if let m = Self.match(step, #"^(?:type|enter|fill in|write|put)\s+["“'](.+?)["”']\s+(?:in|into|on)\s+(?:the\s+)?(.+)$"#) {
                press(["name": m[1], "text": m[0]], okStep, failStep)
            } else if let m = Self.match(step, #"^(?:choose|pick|select)\s+["“']?(.+?)["”']?\s+(?:in|from|for)\s+(?:the\s+)?(.+)$"#) {
                press(["name": m[1], "text": m[0]], okStep, failStep)
            } else if let m = Self.match(step, #"^(?:press|hit)\s+(?:the\s+)?((?:(?:cmd|command|shift|opt|option|alt|ctrl|control)\+)*[a-z0-9,./;'\[\]=`-]+)(?:\s+key)?$"#),
                      Self.keyCodes[(m[0].lowercased().split(separator: "+").last.map(String.init) ?? "")] != nil {
                key(["keys": m[0]], okStep, failStep)
            } else if let m = Self.match(step, #"^(?:type|enter)\s+["“'](.+?)["”']$"#) {
                type(["text": m[0]], okStep, failStep)
            } else {
                let name = lower.hasPrefix("click ") || lower.hasPrefix("press ") || lower.hasPrefix("tap ") || lower.hasPrefix("open ")
                    || lower.hasPrefix("select ") || lower.hasPrefix("turn ") || lower.hasPrefix("tick ") || lower.hasPrefix("check ")
                    ? String(step.split(separator: " ", maxSplits: 1).last ?? "") : step
                press(["name": name], okStep, failStep)
            }
            if let failure {
                let summary = done.isEmpty ? "No steps ran." : "Done: " + done.joined(separator: " ")
                return fail("\(summary) Stopped at \"\(step)\": \(failure)\n\nThe screen now:\n\(listing())")
            }
            done.append(result)
            Thread.sleep(forTimeInterval: 0.3)   // let the app draw the result before the next step reads the screen
        }
        ok("Done: " + done.joined(separator: " ") + "\n\nThe screen now:\n" + listing())
    }

    static func match(_ s: String, _ pattern: String) -> [String]? {
        guard let re = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]),
              let m = re.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) else { return nil }
        return (1..<m.numberOfRanges).compactMap { Range(m.range(at: $0), in: s).map { String(s[$0]) } }
    }
}

extension TaffyMacBridge {
    static func pressAt(_ hit: AXUIElement) -> String? {
        let pressable: Set<String> = ["AXButton", "AXCheckBox", "AXRadioButton", "AXLink", "AXMenuItem", "AXMenuBarItem",
                                      "AXPopUpButton", "AXMenuButton", "AXDisclosureTriangle", "AXTab", "AXSwitch"]
        var cur: AXUIElement? = hit
        for _ in 0..<3 {
            guard let c = cur else { return nil }
            let role = attr(c, kAXRoleAttribute) as? String ?? ""
            if role == "AXRow" { return select(c) ? "row" : nil }
            if pressable.contains(role), actions(c).contains("AXPress") {
                return AXUIElementPerformAction(c, kAXPressAction as CFString) == .success
                    ? role.dropFirst(2).lowercased().replacingOccurrences(of: "checkbox", with: "switch") : nil
            }
            if role == "AXWindow" || role == "AXWebArea" || role == "AXScrollArea" { return nil }
            cur = attr(c, kAXParentAttribute).map { $0 as! AXUIElement }
        }
        if let row = rowOf(hit), select(row) { return "row" }
        return nil
    }
}
