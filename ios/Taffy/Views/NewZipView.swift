import SwiftUI

/// A card in the New Zip gallery (hub /api/templates, from zip-templates.json on the server).
struct ZipTemplate: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let pitch: String
    let look: ZipLook
    let needs: [String]
    /// The accounts this template's Zip uses (gmail, calendar, drive, trello). Older hubs leave it out.
    var services: [String]? = nil
    let routine: String?
    let added: Bool
    /// Whether this home can run it (the hub checks its services against the home's connectors, mailboxes and
    /// calendars). Older hubs leave it out, and then every template is offered.
    var available: Bool? = nil

    /// Uses Gmail, Google Calendar or Google Drive, so onboarding offers Connect Google after picking it.
    var usesGoogle: Bool { !(services ?? []).filter(AccountGroup.google.serviceIDs.contains).isEmpty }
}

/// New Zip: pick a ready-made Zip or describe your own. Either way the lead Zip builds it in its chat, so it
/// can ask for what the new Zip needs while you are there.
struct NewZipView: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    /// Called with the message to send the lead Zip, or nil to start a description of your own.
    let onPick: (String?) -> Void

    @State private var templates: [ZipTemplate] = []
    @State private var loading = true
    @State private var error: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Text("Pick a Zip to start from. \(lead) builds it with you and asks for anything it needs.")
                        .font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                        .padding(.horizontal, 20)
                    Button { finish(nil) } label: { ownCard }.buttonStyle(.plain).padding(.horizontal, 16)
                    if loading {
                        ProgressView().frame(maxWidth: .infinity).padding(.top, 30)
                    } else if let error {
                        Text(error).font(Theme.secondary).foregroundStyle(Theme.inkSoft).padding(20)
                    }
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 300), spacing: 12)], spacing: 12) {
                        ForEach(templates) { t in
                            Button { finish("Please make me a new \(t.name) Zip from the \(t.id) template.") } label: { card(t) }
                                .buttonStyle(.plain)
                                .accessibilityLabel("\(t.name). \(t.pitch)\(t.added ? " Already on your team." : "")")
                        }
                    }
                    .padding(.horizontal, 16)
                }
                .padding(.vertical, 12)
            }
            .background(Theme.canvas)
            .safeAreaInset(edge: .top, spacing: 0) { SheetBar(title: "New Zip", onCancel: { dismiss() }) }
            .toolbar(.hidden, for: .navigationBar)
            .task { await load() }
        }
    }

    private var lead: String { state.bot("default")?.name ?? "Zero" }

    private var ownCard: some View {
        HStack(spacing: 14) {
            Image(systemName: "pencil.and.scribble").font(.system(size: 22, weight: .medium))
                .foregroundStyle(Theme.ink).frame(width: 64, height: 64)
                .background(Circle().fill(Theme.bubble))
            VStack(alignment: .leading, spacing: 4) {
                Text("Describe Your Own").font(Theme.text(17, .semibold)).foregroundStyle(Theme.ink)
                Text("Tell \(lead) what you need, and it designs the Zip with you.")
                    .font(Theme.secondary).foregroundStyle(Theme.inkSoft)
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 20, style: .continuous).fill(Theme.bubble.opacity(0.6)))
    }

    private func card(_ t: ZipTemplate) -> some View {
        HStack(alignment: .top, spacing: 14) {
            AgentStage(look: .forZip(id: t.id, look: t.look, color: t.look.color), size: 64)
                .frame(width: 64, height: 64)
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 8) {
                    Text(t.name).font(Theme.text(17, .semibold)).foregroundStyle(Theme.ink)
                    if t.added {
                        Text("On Your Team").font(Theme.text(11, .semibold)).foregroundStyle(Theme.inkSoft)
                            .padding(.horizontal, 7).padding(.vertical, 2)
                            .background(Capsule().fill(Theme.ink.opacity(0.08)))
                    }
                }
                Text(t.pitch).font(Theme.secondary).foregroundStyle(Theme.ink.opacity(0.85))
                    .fixedSize(horizontal: false, vertical: true)
                if let routine = t.routine {
                    Label(routine, systemImage: "clock").font(Theme.text(12)).foregroundStyle(Theme.inkSoft)
                }
                if !t.needs.isEmpty {
                    Text("Asks you for " + Self.list(t.needs.map(\.lowercasedFirst)) + ".")
                        .font(Theme.text(12)).foregroundStyle(Theme.inkSoft)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 20, style: .continuous).fill(Theme.bubble.opacity(0.6)))
        .opacity(t.added ? 0.7 : 1)
    }

    private static func list(_ items: [String]) -> String {
        items.count < 2 ? (items.first ?? "") : items.dropLast().joined(separator: ", ") + " and " + items.last!
    }

    private func finish(_ message: String?) {
        dismiss()
        onPick(message)
    }

    private func load() async {
        defer { loading = false }
        if state.fixtureMode {
            templates = ZipTemplate.samples
            return
        }
        do { templates = try await state.client.templates().filter { $0.available != false } } catch { self.error = HubClient.describe(error) }
    }
}

private extension String {
    var lowercasedFirst: String { prefix(1).lowercased() + dropFirst() }
}

extension ZipTemplate {
    /// For simulator test builds without a hub.
    static let samples: [ZipTemplate] = [
        ZipTemplate(id: "inbox", name: "Inbox", pitch: "Gets your email to zero: clears the noise, batches the quick reads and drafts replies in your voice.",
                    look: ZipLook(color: "#06B6D4"),
                    needs: ["Which email accounts to watch", "Senders that are always important"], services: ["gmail"], routine: "Weekdays at 7:30 a.m.", added: false),
        ZipTemplate(id: "calendar", name: "Calendar", pitch: "Catches conflicts before they bite, turns emailed dates into events and finds times that work.",
                    look: ZipLook(color: "#6366F1"),
                    needs: ["Which calendars are yours and which are shared"], services: ["calendar", "gmail"], routine: "Every evening at 6", added: false),
        ZipTemplate(id: "cleanup", name: "Cleanup", pitch: "Finds what is eating your disk space, sorted by how safe it is to remove, and never deletes on its own.",
                    look: ZipLook(color: "#64748B"),
                    needs: ["Your okay each time it looks at your Mac"], services: [], routine: "Mondays at 10 a.m.", added: false),
        ZipTemplate(id: "errands", name: "Errands", pitch: "Does the clicking: shopping research, carts, returns and appointments. You place the order.",
                    look: ZipLook(color: "#EF4444"),
                    needs: ["Sign-ins for the stores you use"], services: ["calendar"], routine: nil, added: true),
    ]
}
