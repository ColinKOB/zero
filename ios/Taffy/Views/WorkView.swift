import QuickLook
import SwiftUI

/// What the team is doing: what needs Colin first, then work in progress, then what got done.
/// Laid out like the home list: candies on the left, a title and one gray line beside them.
struct WorkView: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    @State private var selectedID: String?
    /// The Settings toggle "Open Threads Beside the Main Chat".
    @AppStorage(ThreadPanel.settingKey) private var beside = true
    /// How wide the side panel is, remembered between launches.
    @AppStorage(ThreadPanel.widthKey) private var panelWidth = ThreadPanel.defaultWidth
    @State private var dragStart: Double?
    /// Done starts folded into a small stack of its newest cards and stays the way Colin last left it.
    @AppStorage("workDoneOpen") private var doneOpen = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Namespace private var doneSpace
    @AccessibilityFocusState private var doneHeaderFocused: Bool

    var body: some View {
        GeometryReader { geo in
            // Decided from the pane's live width, so resizing the window moves a thread between the side panel and a sheet.
            let wide = beside && geo.size.width >= ThreadPanel.minPaneWidth
            let width = ThreadPanel.clamp(panelWidth, pane: geo.size.width)
            HStack(spacing: 0) {
                board
                if wide, let id = selectedID {
                    ThreadPanel.divider(width: width, pane: geo.size.width, dragStart: $dragStart) { panelWidth = $0 }
                    TaskDetailView(taskID: id, onClose: { selectedID = nil })
                        .environment(state)
                        .frame(width: width)
                        .transition(.move(edge: .trailing).combined(with: .opacity))
                }
            }
            .animation(.snappy(duration: 0.25), value: wide && selectedID != nil)
            .sheet(item: Binding(get: { wide ? nil : selectedID.map(TaskRef.init) }, set: { selectedID = $0?.id })) {
                TaskDetailView(taskID: $0.id).environment(state)
            }
        }
        .background(Theme.canvas)
        #if DEBUG
        .task {
            let a = CommandLine.arguments   // for screenshots
            if let i = a.firstIndex(of: "-task"), i + 1 < a.count {
                try? await Task.sleep(for: .milliseconds(800))
                selectedID = a[i + 1]
            }
        }
        #endif
    }

    private var board: some View {
        ScrollViewReader { scroller in
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    if state.offline { OfflineNote().padding(.horizontal, 20) }
                    section("Needs You", tasks: state.board.needs_you, empty: "Nothing is waiting on you.",
                            icon: "checkmark.circle", emphasize: true, requests: state.board.requests ?? [])
                    section("In Progress", tasks: state.board.working,
                            empty: "Nobody is working on anything right now.", icon: "moon.zzz")
                    section("Done", tasks: state.board.done, empty: "Finished work shows up here.", icon: "tray",
                            folds: true)
                        .id("done")
                }
                .padding(.top, 8)
                .padding(.bottom, 24)
            }
            #if DEBUG
            .task {   // -showDone: Done at the top of the screen, for screenshots
                guard CommandLine.arguments.contains("-showDone") else { return }
                try? await Task.sleep(for: .milliseconds(600))
                scroller.scrollTo("done", anchor: .top)
            }
            #endif
        }
        .background(Theme.canvas)
        .safeAreaInset(edge: .top) {
            HStack(spacing: 12) {
                if !ProcessInfo.processInfo.isMacCatalystApp {   // the Mac sidebar is always there
                    CircleButton(action: { dismiss() }) { Image(systemName: "chevron.left") }
                        .accessibilityLabel("Back")
                }
                Text("Work").font(Theme.title).foregroundStyle(Theme.ink)
                Spacer()
            }
            .padding(.horizontal, 20).padding(.top, 4).padding(.bottom, 8)
            .barFade(.top)
        }
        .toolbar(.hidden, for: .navigationBar)
        .refreshable { await state.refreshBoard() }
        .task { await state.refreshBoard() }
    }

    @ViewBuilder
    private func section(_ title: String, tasks: [TaskItem], empty: String, icon: String, emphasize: Bool = false,
                         requests: [SignInRequest] = [], folds: Bool = false) -> some View {
        let count = tasks.count + requests.count
        // Only a section with tasks folds; an empty one keeps its plain note.
        let foldable = folds && !tasks.isEmpty
        VStack(alignment: .leading, spacing: 4) {
            if foldable {
                Button { toggleDone(count: tasks.count) } label: {
                    header(title, count: count, emphasize: emphasize, folds: true)
                }
                .buttonStyle(PressStyle(scale: 0.98))
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(title), \(count == 1 ? "1 task" : "\(count) tasks")")
                .accessibilityValue(doneOpen ? "Expanded" : "Collapsed")
                .accessibilityHint(doneOpen ? "Collapses the list." : "Shows every finished task.")
                .accessibilityAddTraits(.isHeader)
                .accessibilityFocused($doneHeaderFocused)
            } else {
                header(title, count: count, emphasize: emphasize)
            }
            ForEach(requests) { r in
                SignInRequestCard(request: r, inChat: false).padding(.horizontal, 20).padding(.vertical, 4)
            }
            if count == 0 {
                HStack(spacing: 14) {
                    Image(systemName: icon)
                        .font(.system(size: 17, weight: .medium)).foregroundStyle(Theme.inkSoft)
                        .frame(width: 44, height: 44)
                        .background(Theme.field, in: Circle())
                    Text(empty).font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 20).padding(.vertical, 6)
            } else if foldable && !doneOpen {
                DoneStack(tasks: Array(tasks.prefix(DoneStack.depth)), total: tasks.count, space: doneSpace,
                          matched: !reduceMotion) { toggleDone(count: tasks.count) }
                    .padding(.horizontal, 20).padding(.top, 6)
                    .transition(handoff)
            } else if !tasks.isEmpty {
                VStack(spacing: 0) {
                    ForEach(Array(tasks.enumerated()), id: \.element.id) { i, t in
                        Button { selectedID = t.id } label: { TaskRow(task: t, emphasize: emphasize) }
                            .buttonStyle(PressStyle(scale: 0.98))
                            // The stack's cards become the first rows.
                            .modifier(Matched(id: "done-\(t.id)", space: doneSpace,
                                              on: foldable && !reduceMotion && i < DoneStack.depth))
                        if t.id != tasks.last?.id {
                            Rectangle().fill(Theme.rule).frame(height: 0.5).padding(.leading, 78)
                        }
                    }
                }
                .transition(folds ? handoff : .opacity)
            }
        }
    }

    /// Between the stack and the list the leaving one fades out quickly and the arriving one fades in just after,
    /// so a card and its row are never both readable mid-slide. With Reduce Motion it is an even crossfade.
    private var handoff: AnyTransition {
        reduceMotion ? .opacity : .asymmetric(insertion: .opacity.animation(.easeOut(duration: 0.22).delay(0.03)),
                                              removal: .opacity.animation(.easeIn(duration: 0.14)))
    }

    private func header(_ title: String, count: Int, emphasize: Bool, folds: Bool = false) -> some View {
        HStack(spacing: 8) {
            Text(title).font(Theme.headline).foregroundStyle(Theme.ink).accessibilityAddTraits(.isHeader)
            if count > 0 {
                Text("\(count)")
                    .font(Theme.text(12, .semibold)).monospacedDigit()
                    .foregroundStyle(emphasize ? .white : Theme.inkSoft)
                    .padding(.horizontal, 7).frame(minWidth: 22, minHeight: 20)
                    .background(emphasize ? Theme.taffyDeep : Theme.field, in: Capsule())
            }
            if folds {
                // Points right when folded and down when open, like a Finder disclosure triangle.
                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .bold)).foregroundStyle(Theme.inkSoft)
                    .rotationEffect(.degrees(doneOpen ? 90 : 0))
                    .frame(width: 20, height: 20)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 20)
        .padding(.bottom, 4)
        .contentShape(Rectangle())
    }

    /// Opens or folds Done. The cards spring into the rows; with Reduce Motion the two simply crossfade. VoiceOver
    /// lands on the header, which reads the new state.
    private func toggleDone(count: Int) {
        withAnimation(reduceMotion ? .easeInOut(duration: 0.2) : .spring(response: 0.48, dampingFraction: 0.84)) {
            doneOpen.toggle()
        }
        doneHeaderFocused = true
        let news = doneOpen ? "Showing all \(count) done tasks." : "Done is collapsed."
        AccessibilityNotification.Announcement(news).post()
    }
}

/// Done, folded: the newest finished tasks as a small deck of cards. The newest is in front; each older card sits a
/// step down and to the right behind it, with its Zip and title along the strip that shows below the card ahead.
/// Top to bottom it reads in the list's order, so opening it slides each card straight into its row. One button;
/// tapping it opens the full list.
private struct DoneStack: View {
    @Environment(AppState.self) private var state
    static let depth = 3
    /// Newest first, at most `depth`.
    let tasks: [TaskItem]
    let total: Int
    let space: Namespace.ID
    let matched: Bool
    let open: () -> Void

    /// How far each card sits down and to the right of the one in front of it. The step down is the strip that
    /// shows: a 30 pt Zip with 8 pt above and below.
    private let down: CGFloat = 46, right: CGFloat = 16

    var body: some View {
        let last = CGFloat(tasks.count - 1)
        Button(action: open) {
            ZStack(alignment: .topLeading) {
                // Drawn oldest first, so the newest lands in front.
                ForEach(Array(tasks.enumerated().reversed()), id: \.element.id) { i, t in
                    let step = CGFloat(i)   // 0 is the front card, top left
                    card(t, front: i == 0)
                        .modifier(Matched(id: "done-\(t.id)", space: space, on: matched))
                        .padding(.top, step * down)
                        .padding(.leading, step * right)
                        .padding(.trailing, (last - step) * right)
                }
            }
            .frame(maxWidth: 520, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle(scale: 0.98))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Done, \(total == 1 ? "1 task" : "\(total) tasks"). Show all.")
        .accessibilityAddTraits(.isButton)
    }

    /// Every card is sized by the front card's layout, so they match. A card behind shows only its Zip and title,
    /// along its bottom edge.
    private func card(_ task: TaskItem, front: Bool) -> some View {
        let bot = state.bot(task.assignee ?? "default")
        return VStack(alignment: .leading, spacing: 6) {
            heading(task, bot)
            Text(TaskRow.meta(task, bot)).font(Theme.secondary).foregroundStyle(Theme.inkSoft).lineLimit(1)
                .padding(.leading, 40)
        }
        .padding(.horizontal, 14).padding(.top, 12).padding(.bottom, 14)
        .opacity(front ? 1 : 0)
        .overlay(alignment: .bottomLeading) {
            if !front { heading(task, bot).padding(.horizontal, 14).padding(.bottom, 8) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .floating(RoundedRectangle(cornerRadius: 18, style: .continuous))
    }

    private func heading(_ task: TaskItem, _ bot: Bot?) -> some View {
        HStack(spacing: 10) {
            BotAvatar(bot: bot, size: 30)
            Text(task.title).font(Theme.text(16, .semibold)).foregroundStyle(Theme.ink).lineLimit(1)
            Spacer(minLength: 0)
        }
    }
}

private struct TaskRef: Identifiable { let id: String }

/// The side panel a task thread opens in when the window is wide enough: the panel's limits, and the draggable
/// divider between it and the board.
enum ThreadPanel {
    static let settingKey = "threadsBesideChat"
    static let widthKey = "threadPanelWidth"
    static let defaultWidth = 380.0
    /// The pane must be at least this wide (points) for a thread to open beside the board. A Mac window at its
    /// smallest (900) is narrower than this once the sidebar is counted, so it keeps the sheet.
    static let minPaneWidth: CGFloat = 700
    private static let minPanel = 300.0, maxPanel = 640.0, minBoard = 360.0

    static func clamp(_ w: Double, pane: CGFloat) -> CGFloat {
        CGFloat(min(max(w, minPanel), max(minPanel, min(maxPanel, Double(pane) - minBoard))))
    }

    /// A hairline with a wider grab area. Dragging left widens the panel; VoiceOver can adjust it too.
    static func divider(width: CGFloat, pane: CGFloat, dragStart: Binding<Double?>,
                        set: @escaping (Double) -> Void) -> some View {
        Rectangle().fill(Theme.rule).frame(width: 1)
            .overlay {
                Color.clear.frame(width: 14).contentShape(Rectangle())
                    .gesture(DragGesture(minimumDistance: 0, coordinateSpace: .global)
                        .onChanged { g in
                            let start = dragStart.wrappedValue ?? Double(width)
                            dragStart.wrappedValue = start
                            set(Double(clamp(start - g.translation.width, pane: pane)))
                        }
                        .onEnded { _ in dragStart.wrappedValue = nil })
            }
            .accessibilityElement()
            .accessibilityLabel("Resize Thread")
            .accessibilityValue("\(Int(width)) points wide")
            .accessibilityAdjustableAction { dir in
                set(Double(clamp(Double(width) + (dir == .increment ? 40 : -40), pane: pane)))
            }
    }
}

struct TaskRow: View {
    @Environment(AppState.self) private var state
    let task: TaskItem
    var emphasize = false

    var body: some View {
        let bot = state.bot(task.assignee ?? "default")
        HStack(alignment: .center, spacing: 14) {
            BotAvatar(bot: bot, size: 44, working: task.status == "running")
            VStack(alignment: .leading, spacing: 3) {
                Text(task.title)
                    .font(emphasize ? Theme.headline : Theme.body)
                    .foregroundStyle(Theme.ink)
                    .multilineTextAlignment(.leading)
                    .lineLimit(2)
                Text(Self.meta(task, bot)).font(Theme.secondary).foregroundStyle(Theme.inkSoft).lineLimit(2)
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right").font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.rule)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 10)
        .contentShape(Rectangle())
    }

    /// "Builder, working since 8:41 AM" or the question or summary when there is one.
    static func meta(_ task: TaskItem, _ bot: Bot?) -> String {
        let name = bot?.name ?? task.assignee ?? "Zero"
        switch task.status {
        case "blocked": return task.signInURL != nil ? "\(name) needs a login" : (task.block_reason ?? "\(name) is waiting on you")
        case "review": return task.summary ?? "\(name) wants your review"
        case "running": return "\(name) is on it" + (task.created.map { " since \($0.listStamp)" } ?? "")
        case "ready", "todo": return "\(name) will start soon"
        case "done": return task.summary ?? "\(name) finished" + (task.completed.map { " \($0.listStamp)" } ?? "")
        default: return name
        }
    }
}

struct TaskDetailView: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    let taskID: String
    /// Set when the thread is the side panel; the panel has its own header and close button instead of a sheet.
    var onClose: (() -> Void)?
    @State private var preview: URL?
    @State private var fileError: String?

    /// Always the live copy from the board, so an answered task updates or closes this sheet.
    private var live: TaskItem? {
        let b = state.board
        return (b.needs_you + b.working + b.done).first { $0.id == taskID }
    }

    private var close: () -> Void { onClose ?? { dismiss() } }

    var body: some View {
        if onClose != nil { panel } else { sheetBody }
    }

    private var panel: some View {
        Group {
            if let task = live { content(task) } else {
                ContentUnavailableView("This Task Is Gone", systemImage: "tray",
                                       description: Text("It finished or was removed."))
            }
        }
        .background(Theme.canvas)
        .safeAreaInset(edge: .top) {
            HStack(spacing: 12) {
                Text("Task").font(Theme.title).foregroundStyle(Theme.ink)
                Spacer()
                CircleButton(size: 36, action: close) { TablerIcon(name: "TablerX", size: 18) }
                    .accessibilityLabel("Close Thread")
            }
            .padding(.horizontal, 20).padding(.top, 4).padding(.bottom, 8)
            .barFade(.top)
        }
        .quickLookPreview($preview)
        .onChange(of: live == nil) { _, gone in if gone { close() } }
    }

    private var sheetBody: some View {
        NavigationStack {
            Group {
                if let task = live { content(task) } else {
                    ContentUnavailableView("This Task Is Gone", systemImage: "tray",
                                           description: Text("It finished or was removed."))
                }
            }
            .background(Theme.canvas)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { dismiss() } label: { Image(systemName: "xmark") }.accessibilityLabel("Close")
                }
            }
            .quickLookPreview($preview)
        }
        .onChange(of: live == nil) { _, gone in if gone { dismiss() } }
    }

    private func content(_ task: TaskItem) -> some View {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    let bot = state.bot(task.assignee ?? "default")
                    HStack(spacing: 10) {
                        BotAvatar(bot: bot, size: 30, working: task.status == "running")
                        Text(bot?.name ?? "").font(Theme.text(15, .medium)).foregroundStyle(Theme.inkSoft)
                        Spacer()
                        Text(statusLabel(task)).font(Theme.text(13, .medium)).foregroundStyle(Theme.inkSoft)
                            .padding(.horizontal, 10).padding(.vertical, 5).background(Theme.bubble, in: Capsule())
                    }
                    Text(task.title).font(Theme.text(24, .semibold)).foregroundStyle(Theme.ink)
                    if task.needsYou {
                        ApprovalCard(task: task, inChat: false)
                    } else if let s = task.distinctSummary {
                        bubble(s)
                    }
                    if let e = task.error, !e.isEmpty, task.status != "done" { bubble(e, tint: Theme.danger) }
                    if !task.attachments.isEmpty {
                        VStack(spacing: 8) {
                            ForEach(task.attachments) { a in
                                Button { Task { await open(a, of: task) } } label: { FileCard(name: a.filename) }
                                    .buttonStyle(PressStyle(scale: 0.98))
                            }
                            if let fileError { Text(fileError).font(Theme.caption).foregroundStyle(Theme.danger) }
                        }
                    }
                    if let body = task.body, !body.isEmpty {
                        DisclosureGroup("The Brief") {
                            Text(body).font(Theme.text(15)).foregroundStyle(Theme.inkSoft).textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading).padding(.top, 8)
                        }
                        .font(Theme.text(16, .medium)).tint(Theme.ink)
                        .padding(.top, 6)
                    }
                }
                .padding(20)
            }
    }

    private func statusLabel(_ task: TaskItem) -> String {
        switch task.status {
        case "running": return "Working"
        case "ready", "todo": return "Queued"
        case "blocked": return "Needs You"
        case "review": return "Review"
        case "done": return "Done"
        default: return task.status.capitalized
        }
    }

    private func bubble(_ text: String, tint: Color? = nil) -> some View {
        Text(text).font(Theme.text(17)).foregroundStyle(tint ?? Theme.ink).textSelection(.enabled)
            .padding(.horizontal, 16).padding(.vertical, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
    }

    private func open(_ a: Attachment, of task: TaskItem) async {
        fileError = nil
        guard let req = try? state.client.attachmentURLRequest(task: task.id, attachment: a.id),
              let (data, resp) = try? await HubSession.shared.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200 else {
            fileError = "Could not open \(a.filename). Try again in a moment."
            return
        }
        // Only the last path part of the name, so a strange filename cannot write outside the temp folder.
        let name = (a.filename as NSString).lastPathComponent
        let dir = WorkFiles.folder
        let url = dir.appendingPathComponent(name.isEmpty ? "file" : name)
        do { try data.write(to: url); preview = url } catch { fileError = "Could not open \(a.filename)." }
    }
}

/// Grok Bot's file card: an icon tile, the file's name and its type.
struct FileCard: View {
    let name: String
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 18, weight: .semibold)).foregroundStyle(.white)
                .frame(width: 38, height: 44)
                .background(tint, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            VStack(alignment: .leading, spacing: 2) {
                Text(name).font(Theme.text(16, .medium)).foregroundStyle(Theme.ink).lineLimit(1)
                Text(kind).font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
            }
            Spacer(minLength: 0)
        }
        .padding(12)
        .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
    private var ext: String { (name as NSString).pathExtension.lowercased() }
    private var icon: String {
        switch ext {
        case "pdf": return "doc.richtext.fill"
        case "csv", "xlsx": return "tablecells.fill"
        case "png", "jpg", "jpeg", "heic": return "photo.fill"
        default: return "doc.text.fill"
        }
    }
    private var tint: Color {
        switch ext {
        case "pdf": return Color(hex: "#D93036")
        case "csv", "xlsx": return Color(hex: "#10A35A")
        case "png", "jpg", "jpeg", "heic": return Color(hex: "#8B5CF6")
        default: return Color(hex: "#3B82F6")
        }
    }
    private var kind: String { ext.isEmpty ? "File" : ext.uppercased() + " file" }
}


/// Attachments Colin opened, kept only until the app next starts.
enum WorkFiles {
    static let folder: URL = {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("TaffyFiles", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }()

    static func clear() {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("TaffyFiles", isDirectory: true)
        try? FileManager.default.removeItem(at: dir)
    }
}
