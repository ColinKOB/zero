import SwiftUI

struct KnownFact: Decodable, Identifiable, Equatable {
    let id: Int
    let text: String
    let subject: String
    let kind: String
    let heading: String
    let source: String
    let `private`: Bool
    let observed_at: Double
    let edited: Bool
}

extension HubClient {
    struct FactsPage: Decodable {
        let facts: [KnownFact]
        let headings: [String]
    }

    func knownFacts() async throws -> FactsPage {
        try await send(try request("/api/facts"), as: FactsPage.self)
    }

    func editFact(_ id: Int, text: String) async throws {
        struct Body: Encodable { let text: String }
        struct Resp: Decodable { let ok: Bool }
        _ = try await send(try request("/api/facts/\(id)", method: "PUT", body: Body(text: text)), as: Resp.self)
    }

    func addFact(_ text: String, kind: String) async throws {
        struct Body: Encodable { let text: String; let kind: String }
        struct Resp: Decodable { let ok: Bool }
        _ = try await send(try request("/api/facts", method: "POST", body: Body(text: text, kind: kind)), as: Resp.self)
    }

    func deleteFact(_ id: Int) async throws {
        struct Resp: Decodable { let ok: Bool }
        _ = try await send(try request("/api/facts/\(id)", method: "DELETE"), as: Resp.self)
    }
}

struct KnownFactsView: View {
    @Environment(AppState.self) private var state
    @State private var facts: [KnownFact] = []
    @State private var headings: [String] = []
    @State private var loaded = false
    @State private var error: String?
    @State private var editing: FactEdit?
    @State private var search = ""

    struct FactEdit: Identifiable {
        let id = UUID()
        let fact: KnownFact?
    }

    private var shown: [KnownFact] {
        let q = search.trimmingCharacters(in: .whitespaces).lowercased()
        return q.isEmpty ? facts : facts.filter { $0.text.lowercased().contains(q) || $0.subject.lowercased().contains(q) }
    }

    var body: some View {
        List {
            if let error {
                Section { Label(error, systemImage: "exclamationmark.circle.fill").foregroundStyle(Theme.danger) }
            }
            if loaded && facts.isEmpty {
                Section {
                    Text("Nothing yet. When you tell a Zip something worth remembering, like the coffee you buy or a plan you made, it shows up here.")
                        .font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                }
            }
            ForEach(headings, id: \.self) { heading in
                let rows = shown.filter { $0.heading == heading }
                if !rows.isEmpty {
                    Section(heading) {
                        ForEach(rows) { row($0) }
                            .onDelete { idx in remove(idx.map { rows[$0] }) }
                    }
                }
            }
            Section {
                Button { editing = FactEdit(fact: nil) } label: {
                    Label("Add Something", systemImage: "plus.circle")
                        .font(Theme.secondary.weight(.medium)).foregroundStyle(Theme.ink)
                }
                .disabled(state.offline)
            } footer: {
                Text("Your Zips share these so you never have to say them twice. Change the wording or delete anything, and they stop using it right away.")
            }
        }
        .scrollContentBackground(.hidden)
        .background(Theme.canvas)
        .navigationTitle("What I Know About You")
        .searchable(text: $search, prompt: "Search")
        .refreshable { await load() }
        .task { await load() }
        .sheet(item: $editing) { edit in
            FactEditorSheet(fact: edit.fact) { text, kind in
                await save(edit.fact, text: text, kind: kind)
            }
        }
    }

    private func row(_ f: KnownFact) -> some View {
        Button { editing = FactEdit(fact: f) } label: {
            VStack(alignment: .leading, spacing: 4) {
                Text(f.text).font(Theme.body).foregroundStyle(Theme.ink)
                    .multilineTextAlignment(.leading)
                HStack(spacing: 6) {
                    if f.private {
                        Image(systemName: "lock.fill").font(.system(size: 10)).foregroundStyle(Theme.inkSoft)
                            .accessibilityLabel("Only the Zip that saved it uses this")
                    }
                    Text(f.edited ? "\(f.source). You changed it." : f.source)
                        .font(Theme.secondary).foregroundStyle(Theme.inkSoft)
                }
            }
            .padding(.vertical, 2)
        }
        .swipeActions {
            Button(role: .destructive) { remove([f]) } label: { Label("Delete", systemImage: "trash") }
        }
    }

    private func remove(_ items: [KnownFact]) {
        Task {
            for f in items {
                do {
                    try await state.client.deleteFact(f.id)
                    facts.removeAll { $0.id == f.id }
                    error = nil
                } catch { self.error = HubClient.describe(error) }
            }
        }
    }

    private func save(_ fact: KnownFact?, text: String, kind: String) async {
        do {
            if let fact { try await state.client.editFact(fact.id, text: text) }
            else { try await state.client.addFact(text, kind: kind) }
            error = nil
            await load()
        } catch { self.error = HubClient.describe(error) }
    }

    private func load() async {
        do {
            let page = try await state.client.knownFacts()
            facts = page.facts
            headings = page.headings
            error = nil
        } catch { self.error = HubClient.describe(error) }
        loaded = true
    }
}

struct FactEditorSheet: View {
    let fact: KnownFact?
    let onSave: (String, String) async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var kind = "preference"
    @State private var saving = false

    private static let kinds: [(String, String)] = [("preference", "Preference"), ("place", "Place"),
                                                     ("purchase", "Purchase"), ("supply", "Supply"),
                                                     ("person", "Person"), ("plan", "Plan"),
                                                     ("decision", "Decision"), ("other", "Other")]

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("For example, I buy Lavazza Super Crema at Whole Foods.", text: $text, axis: .vertical)
                        .lineLimit(2...6)
                } footer: {
                    Text(fact == nil ? "One sentence your Zips should remember." : fact!.source)
                }
                if fact == nil {
                    Picker("Kind", selection: $kind) {
                        ForEach(Self.kinds, id: \.0) { Text($0.1).tag($0.0) }
                    }
                }
            }
            .navigationTitle(fact == nil ? "Add Something" : "Change This")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        saving = true
                        Task {
                            await onSave(text.trimmingCharacters(in: .whitespacesAndNewlines), kind)
                            saving = false
                            dismiss()
                        }
                    }
                    .disabled(saving || text.trimmingCharacters(in: .whitespaces).count < 3)
                }
            }
            .onAppear { text = fact?.text ?? "" }
        }
    }
}
