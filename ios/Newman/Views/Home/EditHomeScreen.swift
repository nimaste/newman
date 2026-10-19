import SwiftUI

/// Edit Home: which cards show and in what order (drag), and up to four favourites. Saved for this
/// person only (PUT /api/home/me) when Done is tapped.
struct EditHomeScreen: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var cards: [HomeData.Card] = []
    @State private var favourites: [String] = []
    @State private var loaded = false
    @State private var picking = false
    @State private var saving = false

    var body: some View {
        ZStack {
            NightGlassBackground(teal: false)
            List {
                Section {
                    Text("Edit Home").font(AppFont.sora(28)).foregroundColor(Theme.text)
                        .accessibilityAddTraits(.isHeader)
                        .listRowBackground(Color.clear)
                        .listRowInsets(EdgeInsets(top: 4, leading: 4, bottom: 0, trailing: 4))
                        .moveDisabled(true)
                        .deleteDisabled(true)
                }
                Section {
                    ForEach($cards) { $card in
                        Toggle(isOn: $card.on) {
                            HStack(spacing: 8) {
                                Text(card.title).font(AppFont.manrope(15, .heavy)).foregroundColor(card.on ? Theme.text : Theme.muted)
                                if card.new {
                                    Text("NEW").font(AppFont.manrope(11, .heavy)).foregroundColor(Theme.blue)
                                        .padding(.horizontal, 7).frame(height: 20)
                                        .background(Capsule().fill(Theme.blue.opacity(0.16)))
                                }
                            }
                        }
                        .tint(Theme.blue)
                        .frame(minHeight: 44)
                        .listRowBackground(Theme.card)
                        .accessibilityValue((card.on ? "Shown" : "Hidden") + (card.new ? ", new" : ""))
                    }
                    .onMove { cards.move(fromOffsets: $0, toOffset: $1) }
                } header: {
                    header("Cards · drag to reorder")
                }
                Section {
                    ForEach(favourites, id: \.self) { id in
                        favouriteRow(id).listRowBackground(Theme.card)
                    }
                    .onMove { favourites.move(fromOffsets: $0, toOffset: $1) }
                    .onDelete { favourites.remove(atOffsets: $0) }
                    if favourites.count < HomeLogic.maxFavourites {
                        Button { picking = true } label: {
                            Label("Add a favourite", systemImage: "plus")
                                .font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.blue)
                        }
                        .frame(minHeight: 44)
                        .listRowBackground(Theme.card)
                    }
                } header: {
                    header("Favourites · up to \(HomeLogic.maxFavourites)")
                }
            }
            .scrollContentBackground(.hidden)
            .environment(\.editMode, .constant(.active))
        }
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button {
                    saving = true
                    Task {
                        if await model.saveHome(cards: cards, favourites: favourites) { dismiss() }
                        saving = false
                    }
                } label: {
                    if saving { ProgressView().tint(Theme.text) } else { Text("Done").font(AppFont.manrope(16, .heavy)) }
                }
                .disabled(saving || !loaded)
            }
        }
        .sheet(isPresented: $picking) {
            FavouritePicker(chosen: favourites) { id in
                if favourites.count < HomeLogic.maxFavourites, !favourites.contains(id) { favourites.append(id) }
            }
            .environmentObject(model)
        }
        .task {
            guard !loaded else { return }
            if model.home == nil { await model.reloadHome() }
            if let h = model.home {
                cards = h.cards
                favourites = Array(h.favourites.prefix(HomeLogic.maxFavourites))
                loaded = true
            }
        }
    }

    private func header(_ text: String) -> some View {
        Text(text.uppercased()).font(AppFont.manrope(12, .heavy)).kerning(1.2).foregroundColor(Theme.muted)
    }

    private func favouriteRow(_ id: String) -> some View {
        let e = model.entity(id)
        let members = e.map(LightRooms.members) ?? []
        return HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(e?.name ?? id).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                if !members.isEmpty {
                    Text("\(members.count) lights").font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                }
            }
            Spacer(minLength: 8)
            Button {
                favourites.removeAll { $0 == id }
            } label: {
                Image(systemName: "minus").font(.system(size: 15, weight: .bold)).foregroundColor(Theme.coralText)
                    .frame(width: 36, height: 36)
                    .background(Circle().fill(Theme.coral.opacity(0.14)))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Remove \(e?.name ?? id)")
        }
        .frame(minHeight: 44)
    }
}

/// Every device and group with a main action (what a favourite's tap does), searched by name.
struct FavouritePicker: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let chosen: [String]
    let onPick: (String) -> Void
    @State private var query = ""

    var body: some View {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        let list = model.allEntities.filter { e in
            !chosen.contains(e.id) && DeviceLogic.tapAction(e, model.kind(of: e)) != nil
                && (q.isEmpty || e.name.lowercased().contains(q))
        }
        .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
        NavigationStack {
            List(list) { e in
                Button {
                    onPick(e.id)
                    dismiss()
                } label: {
                    HStack(spacing: 12) {
                        Image(systemName: Symbols.name(DeviceLogic.icon(e, model.kind(of: e), kinds: model.kinds)))
                            .font(.system(size: 16, weight: .semibold)).foregroundColor(Theme.muted).frame(width: 26)
                            .accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(e.name).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                            if let room = LightRooms.room(e) {
                                Text(room).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                            }
                        }
                    }
                    .frame(minHeight: 44)
                }
                .listRowBackground(Theme.card)
            }
            .scrollContentBackground(.hidden)
            .background(Theme.background)
            .searchable(text: $query, prompt: "Search devices")
            .navigationTitle("Add a favourite")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            }
        }
        .preferredColorScheme(.dark)
    }
}
