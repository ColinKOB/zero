import SwiftUI

/// The column of outfit boxes on the left: white, a faint gray border, each showing the outfit's pieces. Flat
/// bodies wear no outfits, so on them the column is dimmed and does nothing.
struct OutfitColumn: View {
    @Binding var look: AgentLook

    var body: some View {
        let available = AgentBody.named(look.body).hasOutfits
        ScrollView(.vertical) {
            VStack(spacing: 10) {
                ForEach(AgentOutfit.all) { outfit in
                    let on = available && look.outfit == outfit.id
                    Button {
                        look.outfit = outfit.id
                    } label: {
                        OutfitIcon(outfit: outfit)
                            .frame(width: 64, height: 64)
                            .onboardingCard(selected: on, radius: 14)
                            .opacity(available ? 1 : 0.35)
                    }
                    .buttonStyle(PressStyle(scale: 0.94))
                    .disabled(!available)
                    .accessibilityLabel(outfit.description)
                    .accessibilityHint(available ? "" : "Flat Zips wear no outfits.")
                    .accessibilityAddTraits(on ? .isSelected : [])
                }
            }
            .padding(.vertical, 12)
            .frame(maxWidth: .infinity)
        }
        .scrollIndicators(.hidden)
        .sensoryFeedback(.selection, trigger: look.outfit)
    }
}

/// An outfit's pieces as small icons, stacked as they are worn (glasses are short, so they get less height).
struct OutfitIcon: View {
    let outfit: AgentOutfit

    var body: some View {
        if outfit.items.isEmpty {
            Text("None").font(Theme.text(13, .medium)).foregroundStyle(Theme.inkSoft)
        } else {
            VStack(spacing: 3) {
                ForEach(outfit.items, id: \.self) { item in
                    Image("addon-\(item)")
                        .resizable()
                        .scaledToFit()
                        .frame(maxWidth: 46, maxHeight: item.hasPrefix("glasses") ? 16 : .infinity)
                }
            }
            .padding(8)
        }
    }
}

/// The carousel of bodies under the agent, with a Plush and Flat switch above it.
struct BodyCarousel: View {
    @Binding var selected: String
    @State private var style: Style

    enum Style: String, CaseIterable, Identifiable {
        case plush = "Plush", flat = "Flat"
        var id: String { rawValue }
    }

    init(selected: Binding<String>) {
        _selected = selected
        _style = State(initialValue: AgentBody.named(selected.wrappedValue).isFlat ? .flat : .plush)
    }

    private var bodies: [AgentBody] { style == .flat ? AgentBody.flat : AgentBody.plush }

    var body: some View {
        VStack(spacing: 10) {
            Picker("Style", selection: $style) {
                ForEach(Style.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .frame(width: 200)
            ScrollViewReader { proxy in
                ScrollView(.horizontal) {
                    HStack(spacing: 6) {
                        ForEach(bodies) { b in
                            let on = b.id == selected
                            Button {
                                selected = b.id
                                withAnimation(.spring(duration: 0.4)) { proxy.scrollTo(b.id, anchor: .center) }
                            } label: {
                                VStack(spacing: 4) {
                                    BodyThumb(b, size: 50)
                                        .frame(width: 66, height: 66)
                                        .background(Circle().fill(on ? Color(hex: "#F3F3F3") : .clear))
                                        .overlay(Circle().stroke(on ? Theme.ink : .clear, lineWidth: 2))
                                    Text(b.name)
                                        .font(Theme.text(12, on ? .semibold : .regular))
                                        .foregroundStyle(on ? Theme.ink : Theme.inkSoft)
                                }
                                .frame(width: 72)
                            }
                            .buttonStyle(PressStyle(scale: 0.92))
                            .id(b.id)
                            .accessibilityLabel("\(style.rawValue) \(b.name)")
                            .accessibilityAddTraits(on ? .isSelected : [])
                        }
                    }
                    .padding(.horizontal, 18)
                }
                .scrollIndicators(.hidden)
                .onAppear { proxy.scrollTo(selected, anchor: .center) }
                .onChange(of: style) { _, _ in
                    if AgentBody.named(selected).isFlat != (style == .flat), let first = bodies.first {
                        selected = first.id
                        proxy.scrollTo(first.id, anchor: .center)
                    }
                }
            }
        }
        .sensoryFeedback(.selection, trigger: selected)
    }
}
