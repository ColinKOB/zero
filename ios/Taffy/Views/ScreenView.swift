import SwiftUI
import WebKit

struct ScreenView: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    let botID: String
    var takeOver = false
    var site: URL? = nil
    var reason: String? = nil
    var requestID: Int? = nil

    @State private var status: ScreenStatus?
    @State private var viewerURL: URL?
    @State private var busy = false
    @State private var error: String?

    struct ScreenStatus: Decodable {
        let running: Bool
        let holder: String
        let you_have_control: Bool
        let reason: String?
    }

    @State private var reconnects = 0
    @State private var scrollHint: String?
    @State private var web = WebHandle()
    @State private var typed = ""
    @State private var secret = false
    @State private var zoomed = false
    @State private var moving = false
    @State private var pageGestures = false
    @FocusState private var typing: Bool
    @Environment(\.verticalSizeClass) private var verticalSize
    private static let onPhone = !ProcessInfo.processInfo.isMacCatalystApp
    private var landscape: Bool { Self.onPhone && verticalSize == .compact }
    private var typingBar: Bool { status?.you_have_control == true && Self.onPhone }

    private var bot: Bot? { state.bot(botID) }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if let error, viewerURL == nil {
                message(error, button: "Try Again") { Task { self.error = nil; reconnects = 0; await refresh(connect: true) } }
            } else if let viewerURL {
                ScreenWeb(url: viewerURL, onState: viewerState, handle: web, panScrolls: !moving && !pageGestures,
                          phone: Self.onPhone)
                    .ignoresSafeArea(.container, edges: typingBar ? [] : .bottom)
            } else if demoScreen {
                #if DEBUG
                DemoScreenArt().ignoresSafeArea(.container, edges: typingBar ? [] : .bottom)
                #endif
            } else if status?.running == false {
                message("\((bot?.name ?? "This Zip").possessive) computer is asleep.", button: busy ? "Starting" : "Wake It Up") {
                    Task { await start() }
                }
            } else {
                VStack(spacing: 14) {
                    BotAvatar(bot: bot, size: 56, working: true)
                    Text("Connecting to \((bot?.name ?? "the Zip").possessive) computer").font(Theme.secondary).foregroundStyle(.white.opacity(0.7))
                }
            }
            if let scrollHint, error == nil {
                Text(scrollHint).font(Theme.caption).foregroundStyle(.white)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(.black.opacity(0.75), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .padding(.bottom, 30).frame(maxHeight: .infinity, alignment: .bottom)
                    .allowsHitTesting(false)
            }
            if let error, viewerURL != nil {
                Text(error).font(Theme.caption).foregroundStyle(.white)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(Theme.danger, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .padding(.bottom, 30).frame(maxHeight: .infinity, alignment: .bottom)
            }
        }
        .safeAreaInset(edge: .top) {
            VStack(spacing: 0) {
                HStack(spacing: 10) {
                    darkCircle("chevron.left", "Back") { Task { await leave() } }
                    if landscape, status?.you_have_control == true {
                        takeOverBanner
                    } else {
                        NamePill(bot: bot, dark: true)
                        Spacer()
                    }
                    if status?.running == true {
                        let mine = status?.you_have_control == true
                        CircleButton(dark: true, active: mine, action: { Task { await toggleControl() } }) {
                            Image(systemName: mine ? "hand.raised.fill" : "hand.point.up.left")
                        }
                        .disabled(busy)
                        .accessibilityLabel(mine ? "Hand Back to \(bot?.name ?? "the Zip")" : "Take Over")
                    }
                    Menu {
                        if status?.running == true {
                            Button { Task { await toggleControl() } } label: {
                                Label(status?.you_have_control == true ? "Hand Back" : "Take Over",
                                      systemImage: status?.you_have_control == true ? "arrow.uturn.backward" : "hand.point.up.left")
                            }
                            Button { Task { viewerURL = nil; await openViewer() } } label: { Label("Reconnect", systemImage: "arrow.clockwise") }
                        } else {
                            Button { Task { await start() } } label: { Label("Wake It Up", systemImage: "macwindow") }
                        }
                    } label: {
                        Image(systemName: "ellipsis")
                            .font(.system(size: 18, weight: .medium)).foregroundStyle(.white)
                            .frame(width: 44, height: 44)
                            .background(Color.white.opacity(0.08), in: Circle())
                            .overlay(Circle().stroke(Color.white.opacity(0.15), lineWidth: 0.5))
                    }
                }
                .padding(.horizontal, 20).padding(.top, 4)
                .padding(.bottom, status?.you_have_control == true && !landscape ? 0 : 8)
                if status?.you_have_control == true, !landscape {
                    takeOverBanner.padding(.horizontal, 20).padding(.vertical, 8)
                }
            }
            .background(Color.black.ignoresSafeArea())
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if typingBar { keyboardBar }
        }
        .onChange(of: status?.you_have_control) { _, mine in
            if mine != true { zoomed = false; moving = false; typing = false }
        }
        .onChange(of: typing) { _, on in web.run("window.taffyFocus && taffyFocus(\(on))") }
        .task { await begin() }
        .onAppear { if Self.onPhone { OrientationLock.allowLandscape(true) } }
        .onDisappear {
            if Self.onPhone { OrientationLock.allowLandscape(false) }
            if status?.you_have_control == true {
                let client = state.client, id = botID
                Task { _ = try? await Self.post(client, "/api/bots/\(id)/screen/release") }
            }
        }
    }

    private var takeOverBanner: some View {
        HStack(spacing: 12) {
            Text(reason ?? "You're in control of \((bot?.name ?? "the Zip").possessive) computer.")
                .font(Theme.text(Self.onPhone ? 15 : 16, .medium)).foregroundStyle(Color(hex: "#8F5309"))
                .lineLimit(landscape ? 1 : 2).frame(maxWidth: .infinity, alignment: .leading)
            Button { Task { await finishAndContinue() } } label: {
                Text(busy ? "Handing Back" : "I'm Done, Continue")
                    .font(Theme.text(Self.onPhone ? 15 : 16, .semibold)).foregroundStyle(.white)
                    .lineLimit(1).fixedSize()
                    .padding(.horizontal, 16).frame(minHeight: 40)
                    .background(Color(hex: "#111111"), in: Capsule())
            }
            .buttonStyle(PressStyle())
            .disabled(busy)
        }
        .padding(.leading, 18).padding(.trailing, 8).padding(.vertical, 6)
        .background(Color(hex: "#F1E6D3"), in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .accessibilityElement(children: .contain)
    }

    private var demoScreen: Bool {
        #if DEBUG
        return state.fixtureMode && status?.running == true
        #else
        return false
        #endif
    }

    private func begin() async {
        #if DEBUG
        if state.fixtureMode {
            status = ScreenStatus(running: true, holder: "human",
                                  you_have_control: takeOver || CommandLine.arguments.contains("-screenControl"), reason: nil)
            let a = CommandLine.arguments
            if let i = a.firstIndex(of: "-screenViewer"), i + 1 < a.count { viewerURL = URL(string: a[i + 1]) }
            return
        }
        #endif
        guard takeOver else { await refresh(connect: true); return }
        do {
            if let site {
                _ = try await state.client.startSignIn(bot: botID, url: site.absoluteString)
            } else {
                if (try? JSONDecoder().decode(ScreenStatus.self, from: try await call("/api/bots/\(botID)/screen", method: "GET")))?.running != true {
                    _ = try await call("/api/bots/\(botID)/screen/start")
                }
                _ = try await call("/api/bots/\(botID)/screen/control")
            }
        } catch {
            self.error = ScreenTrouble.report(error, screen: "take_over", bot: botID, state: state)
            return
        }
        await refresh(connect: true)
    }

    private func finishAndContinue() async {
        busy = true
        defer { busy = false }
        do {
            if let requestID {
                try await state.client.finishSignIn(bot: botID, taskID: nil, site: site?.host(), declined: false,
                                                    requestID: requestID)
            } else {
                _ = try await call("/api/bots/\(botID)/screen/release")
            }
        } catch {
            self.error = ScreenTrouble.report(error, screen: "take_over_done", bot: botID, state: state)
            return
        }
        status = nil
        state.computers[botID] = nil
        await state.refreshBoard()
        dismiss()
    }

    private var keyboardBar: some View {
        VStack(spacing: 8) {
            if secret {
                Text("Tap the password box on the screen first. The password is typed there, and Return is up to you.")
                    .font(Theme.caption).foregroundStyle(.white.opacity(0.8)).frame(maxWidth: .infinity, alignment: .leading)
            }
            let rows = landscape ? AnyLayout(HStackLayout(spacing: 8)) : AnyLayout(VStackLayout(spacing: 8))
            rows {
                HStack(spacing: 8) { field }
                HStack(spacing: 8) { keys }
            }
        }
        .padding(.horizontal, 12).padding(.top, 10).padding(.bottom, 8)
        .frame(maxWidth: .infinity)
        .background {
            Color(hex: "#141414").ignoresSafeArea(edges: .bottom)
                .overlay(alignment: .top) { Color.white.opacity(0.12).frame(height: 0.5) }
        }
    }

    private var field: some View {
        Group {
            Group {
                if secret {
                    SecureField("Password", text: $typed).textContentType(.password)
                } else {
                    TextField("Type into the selected field", text: $typed)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                }
            }
            .focused($typing)
            .submitLabel(secret ? .done : .send)
            .onSubmit { sendTyped(pressReturn: !secret) }
            .font(Theme.text(16)).foregroundStyle(.white)
            .padding(.horizontal, 12).frame(height: 40)
            .background(Color.white.opacity(0.12), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            barButton(secret ? "eye.slash" : "key", secret ? "Typing a password" : "Type a password") { secret.toggle(); typed = "" }
        }
    }

    @ViewBuilder private var keys: some View {
        barKey("Type", "keyboard") { sendTyped(pressReturn: false) }
        barKey("Return", "return") { web.run("taffyKey('Return')") }
        barKey("Tab", "arrow.right.to.line") { web.run("taffyKey('Tab')") }
        barKey("Delete", "delete.left") { web.run("taffyKey('BackSpace')") }
        barButton(zoomed ? "minus.magnifyingglass" : "plus.magnifyingglass", zoomed ? "Fit Screen" : "Zoom In") {
            zoomed.toggle(); moving = false
            web.run("taffyZoom(\(zoomed))")
        }
        if zoomed, !pageGestures {
            barButton(moving ? "hand.draw.fill" : "hand.draw", moving ? "Moving: drag to look around" : "Move Around") {
                moving.toggle(); web.run("taffyMove(\(moving))")
            }
        }
    }

    private func sendTyped(pressReturn: Bool) {
        let text = typed
        typed = ""
        guard !text.isEmpty else { if pressReturn { web.run("taffyKey('Return')") }; return }
        web.run("taffyType(\(Self.jsString(text)))" + (pressReturn ? "; taffyKey('Return')" : ""))
    }

    private static func jsString(_ s: String) -> String {
        let data = (try? JSONSerialization.data(withJSONObject: [s])) ?? Data("[\"\"]".utf8)
        return String(decoding: data, as: UTF8.self) + "[0]"
    }

    private func barKey(_ title: String, _ symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: symbol).labelStyle(.iconOnly)
                .font(.system(size: 15, weight: .medium)).foregroundStyle(.white)
                .frame(maxWidth: landscape ? 48 : .infinity, minHeight: 36)
                .background(Color.white.opacity(0.12), in: RoundedRectangle(cornerRadius: 9, style: .continuous))
        }
        .accessibilityLabel(title)
    }

    private func barButton(_ symbol: String, _ label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 15, weight: .medium)).foregroundStyle(.white)
                .frame(width: 40, height: 36)
                .background(Color.white.opacity(0.12), in: RoundedRectangle(cornerRadius: 9, style: .continuous))
        }
        .accessibilityLabel(label)
    }

    private func message(_ text: String, button: String, action: @escaping () -> Void) -> some View {
        VStack(spacing: 18) {
            BotAvatar(bot: bot, size: 64)
            Text(text).font(Theme.headline).foregroundStyle(.white).multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            Button(action: action) {
                Text(button).font(Theme.text(17, .medium))
                    .padding(.horizontal, 24).frame(minHeight: 48)
                    .foregroundStyle(.black)
                    .background(.white, in: Capsule())
            }
            .buttonStyle(PressStyle())
            .disabled(busy)
        }
    }

    private func viewerState(_ s: String) {
        switch s {
        case "phone-gestures": pageGestures = true; moving = false; return
        case "zoomed": zoomed = true; return
        case "fitted": zoomed = false; return
        default: break
        }
        if s == "connected" {
            reconnects = 0; error = nil
            #if DEBUG
            debugHooks()
            #endif
            return
        }
        if s == "scroll-needs-control" {
            guard scrollHint == nil else { return }
            scrollHint = "Take over to scroll \((bot?.name ?? "the Zip").possessive) screen."
            Task {
                try? await Task.sleep(for: .seconds(2.5))
                scrollHint = nil
            }
            return
        }
        guard s == "disconnected" else { return }
        reconnects += 1
        guard reconnects <= 3 else {
            viewerURL = nil
            error = ScreenTrouble.report("The connection to \((bot?.name ?? "the Zip").possessive) computer dropped.",
                                         detail: "viewer disconnected 3 times", screen: "screen", bot: botID, state: state)
            return
        }
        Task {
            try? await Task.sleep(for: .seconds(Double(reconnects)))
            await refresh(connect: true)
        }
    }

    #if DEBUG
    private func debugHooks() {
        let a = CommandLine.arguments
        Task {
            if a.contains("-screenLandscape") {
                OrientationLock.allowLandscape(true)
                (UIApplication.shared.connectedScenes.first as? UIWindowScene)?
                    .requestGeometryUpdate(.iOS(interfaceOrientations: .landscapeRight))
                try? await Task.sleep(for: .seconds(1.5))
            }
            if let i = a.firstIndex(of: "-screenTapAt"), i + 1 < a.count {
                let xy = a[i + 1].split(separator: ",").compactMap { Double($0) }
                try? await Task.sleep(for: .seconds(1))
                if xy.count == 2 { web.run("window.taffyTapAt && taffyTapAt(\(xy[0]), \(xy[1]))") }
            }
            if a.contains("-screenTyping") { try? await Task.sleep(for: .seconds(1)); typing = true }
        }
    }
    #endif

    static func post(_ client: HubClient, _ path: String) async throws -> Data {
        guard let url = URL(string: client.server + path) else { throw URLError(.badURL) }
        var r = URLRequest(url: url)
        r.httpMethod = "POST"
        if let t = client.token { r.setValue("Bearer \(t)", forHTTPHeaderField: "Authorization") }
        let (data, resp) = try await HubSession.shared.data(for: r)
        if let code = (resp as? HTTPURLResponse)?.statusCode, code >= 400 { throw HubClient.HubError.http(code, "") }
        return data
    }

    private func darkCircle(_ symbol: String, _ label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 18, weight: .medium)).foregroundStyle(.white)
                .frame(width: 44, height: 44)
                .background(Color.white.opacity(0.08), in: Circle())
                .overlay(Circle().stroke(Color.white.opacity(0.15), lineWidth: 0.5))
        }
        .accessibilityLabel(label)
    }

    private func call(_ path: String, method: String = "POST") async throws -> Data {
        guard let url = URL(string: state.client.server + path) else { throw URLError(.badURL) }
        var r = URLRequest(url: url)
        r.httpMethod = method
        r.timeoutInterval = 120
        if let t = state.client.token { r.setValue("Bearer \(t)", forHTTPHeaderField: "Authorization") }
        let (data, resp) = try await HubSession.shared.data(for: r)
        if let code = (resp as? HTTPURLResponse)?.statusCode, code >= 400 {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            throw HubClient.HubError.http(code, detail ?? "")
        }
        return data
    }

    private func refresh(connect: Bool) async {
        do {
            status = try JSONDecoder().decode(ScreenStatus.self, from: try await call("/api/bots/\(botID)/screen", method: "GET"))
            if connect, status?.running == true { await openViewer() }
        } catch { self.error = ScreenTrouble.report(error, screen: "screen_status", bot: botID, state: state) }
    }

    private func openViewer() async {
        struct Ticket: Decodable { let url: String }
        let control = status?.you_have_control == true
        do {
            let t = try JSONDecoder().decode(Ticket.self,
                from: try await call("/api/bots/\(botID)/screen/ticket?control=\(control)"))
            viewerURL = URL(string: state.client.server + t.url)
        } catch { self.error = ScreenTrouble.report(error, screen: "screen_viewer", bot: botID, state: state) }
    }

    private func start() async {
        busy = true
        defer { busy = false }
        do {
            _ = try await call("/api/bots/\(botID)/screen/start")
            await refresh(connect: true)
        } catch { self.error = ScreenTrouble.report(error, screen: "screen_wake", bot: botID, state: state) }
    }

    private func toggleControl() async {
        busy = true
        defer { busy = false }
        let taking = status?.you_have_control != true
        do {
            _ = try await call("/api/bots/\(botID)/screen/\(taking ? "control" : "release")")
            await refresh(connect: false)
            viewerURL = nil
            await openViewer()
        } catch { self.error = ScreenTrouble.report(error, screen: "screen_control", bot: botID, state: state) }
    }

    private func leave() async {
        if status?.you_have_control == true {
            do {
                _ = try await call("/api/bots/\(botID)/screen/release")
                status = nil
            } catch {
                _ = ScreenTrouble.report(error, screen: "screen_leave", bot: botID, state: state)
                self.error = "\(bot?.name ?? "The Zip") could not get its computer back yet. It gets it back by itself in two minutes."
                try? await Task.sleep(for: .seconds(2))
            }
        }
        dismiss()
    }
}

@MainActor
final class WebHandle {
    weak var view: WKWebView?
    func run(_ js: String) { view?.evaluateJavaScript(js) }
}

struct ScreenWeb: UIViewRepresentable {
    let url: URL
    var onState: (String) -> Void = { _ in }
    var handle: WebHandle? = nil
    var panScrolls = true
    var phone = false

    final class Coordinator: NSObject, WKScriptMessageHandler {
        var onState: (String) -> Void
        weak var pan: UIPanGestureRecognizer?
        init(onState: @escaping (String) -> Void) { self.onState = onState }
        func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
            if let s = m.body as? String { onState(s) }
        }

        @objc func scrolled(_ g: UIPanGestureRecognizer) {
            guard let v = g.view as? WKWebView, g.state == .changed else { return }
            let t = g.translation(in: v)
            g.setTranslation(.zero, in: v)
            let p = g.location(in: v)
            v.evaluateJavaScript("window.taffyScroll && taffyScroll(\(-t.x), \(-t.y), \(p.x), \(p.y))")
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(onState: onState) }

    static func dismantleUIView(_ v: WKWebView, coordinator: Coordinator) {
        v.configuration.userContentController.removeScriptMessageHandler(forName: "screen")
    }

    func makeUIView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.userContentController.add(context.coordinator, name: "screen")
        if phone {
            cfg.userContentController.addUserScript(WKUserScript(source: "window.taffyPhone = true;",
                                                                 injectionTime: .atDocumentStart, forMainFrameOnly: true))
        }
        cfg.allowsInlineMediaPlayback = true
        let v = WKWebView(frame: .zero, configuration: cfg)
        v.isOpaque = false
        v.backgroundColor = .clear
        v.scrollView.isScrollEnabled = false
        // The keyboard and the typing bar already shrink the view; the page must not be shifted again.
        if phone { v.scrollView.contentInsetAdjustmentBehavior = .never }
        let pan = UIPanGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.scrolled))
        if ProcessInfo.processInfo.isMacCatalystApp {
            pan.allowedScrollTypesMask = .all
            pan.allowedTouchTypes = []
        } else {
            pan.maximumNumberOfTouches = 2
            pan.allowedScrollTypesMask = .all
        }
        v.addGestureRecognizer(pan)
        context.coordinator.pan = pan
        handle?.view = v
        v.load(URLRequest(url: url))
        return v
    }

    func updateUIView(_ v: WKWebView, context: Context) {
        context.coordinator.onState = onState
        context.coordinator.pan?.isEnabled = panScrolls
        handle?.view = v
        if v.url != url { v.load(URLRequest(url: url)) }
    }
}

@MainActor
enum OrientationLock {
    static var mask: UIInterfaceOrientationMask = .portrait

    static var supported: UIInterfaceOrientationMask {
        UIDevice.current.userInterfaceIdiom == .phone ? mask : .all
    }

    static func allowLandscape(_ on: Bool) {
        guard UIDevice.current.userInterfaceIdiom == .phone else { return }
        mask = on ? .allButUpsideDown : .portrait
        for scene in UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }) {
            for window in scene.windows {
                var vc = window.rootViewController
                while let c = vc { c.setNeedsUpdateOfSupportedInterfaceOrientations(); vc = c.presentedViewController }
            }
            if !on { scene.requestGeometryUpdate(.iOS(interfaceOrientations: .portrait)) }
        }
    }
}
