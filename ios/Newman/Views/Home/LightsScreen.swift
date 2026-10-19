import SwiftUI

/// Lights: by room (the room set in Newman, else the device's own area), a switch per room and per
/// light, brightness bars for lights that are on, and All off. A light or switch group is one row; its
/// lights are tucked behind "Show the N lights". Tapping a name opens everything it can do.
struct LightsScreen: View {
    @EnvironmentObject var model: AppModel
    @State private var open: Set<String> = []
    @State private var sheetFor: HomeSheet?

    var body: some View {
        let rooms = LightRooms.rooms(model.allEntities, kinds: model.kinds, screens: model.categoryScreens)
        let on = LightRooms.onCount(rooms, entities: model.entities, kinds: model.kinds)
        ZStack {
            NightGlassBackground(teal: false)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(alignment: .center) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Lights").font(AppFont.sora(28)).foregroundColor(Theme.text)
                                .accessibilityAddTraits(.isHeader)
                            Text(on == 0 ? "All off" : "\(on) on").font(AppFont.manrope(13, .heavy))
                                .foregroundColor(on == 0 ? Theme.muted : Theme.amber)
                        }
                        Spacer()
                        if on > 0 {
                            Button("All off") { Task { await allOff(rooms) } }
                                .buttonStyle(PillButtonStyle(height: 40))
                        }
                    }
                    if rooms.isEmpty {
                        Text("No lights yet.").font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted).padding(.top, 20)
                    }
                    ForEach(rooms) { r in room(r) }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 32)
            }
            .refreshable { await model.reloadAll() }
        }
        .sheet(item: $sheetFor) { s in DeviceSheet(entityId: s.id).environmentObject(model) }
    }

    private func room(_ r: LightRooms.Room) -> some View {
        let anyOn = r.rows.contains { LightRooms.isOn($0.id, entities: model.entities, kinds: model.kinds) }
        return VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(r.name).font(AppFont.manrope(17, .heavy)).foregroundColor(Theme.text)
                        .accessibilityAddTraits(.isHeader)
                    Text(LightRooms.roomLine(r, entities: model.entities, kinds: model.kinds))
                        .font(AppFont.manrope(12, .heavy)).foregroundColor(anyOn ? Theme.amber : Theme.muted)
                }
                Spacer()
                Toggle("", isOn: Binding(get: { anyOn }, set: { v in Task { await setAll(r.rows.map { $0.id }, on: v) } }))
                    .labelsHidden()
                    .toggleStyle(SwitchToggleStyle(tint: Theme.amber))
                    .accessibilityLabel("Every light in \(r.name)")
            }
            .padding(.vertical, 6)
            ForEach(r.rows) { row in
                Divider().overlay(Theme.divider)
                lightRow(row.id, members: row.members)
                if row.isGroup && open.contains(row.id) {
                    ForEach(row.members, id: \.self) { m in
                        Divider().overlay(Theme.divider)
                        lightRow(m, members: []).padding(.leading, 18)
                    }
                }
            }
        }
        .glassCard(padding: 14)
    }

    @ViewBuilder private func lightRow(_ id: String, members: [String]) -> some View {
        if let e = model.entity(id) {
            let kind = model.kind(of: e)
            let lit = DeviceDirectory.isOn(e, kind)
            let level = LightRooms.brightness(e)
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 12) {
                    Button { sheetFor = HomeSheet(id: id) } label: {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(e.name).font(AppFont.manrope(15, .bold)).foregroundColor(Theme.text)
                                .multilineTextAlignment(.leading)
                            if !members.isEmpty {
                                Text("Group · \(members.count) light\(members.count == 1 ? "" : "s")")
                                    .font(AppFont.manrope(12, .semibold)).foregroundColor(Theme.dim)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens everything it can do")
                    Text(!e.isAvailable ? "Offline" : (lit ? (level.map { "\(Int($0.rounded()))%" } ?? "On") : "Off"))
                        .font(AppFont.manrope(13, .heavy)).foregroundColor(lit ? Theme.amber : Theme.muted)
                    Toggle("", isOn: Binding(get: { lit }, set: { v in Task { await setAll([id], on: v) } }))
                        .labelsHidden()
                        .toggleStyle(SwitchToggleStyle(tint: Theme.amber))
                        .disabled(!e.isAvailable)
                        .accessibilityLabel(e.name)
                }
                if lit, let level {
                    GeometryReader { g in
                        ZStack(alignment: .leading) {
                            Capsule().fill(Theme.amber.opacity(0.18))
                            Capsule().fill(Theme.amber).frame(width: max(8, g.size.width * min(1, level / 100)))
                        }
                    }
                    .frame(height: 8)
                    .accessibilityHidden(true)
                }
                if !members.isEmpty {
                    Button(open.contains(id) ? "Hide the \(members.count) lights" : "Show the \(members.count) lights ›") {
                        if open.contains(id) { open.remove(id) } else { open.insert(id) }
                    }
                    .font(AppFont.manrope(12, .heavy))
                    .foregroundColor(Theme.dim)
                    .frame(minHeight: 28)
                }
            }
            .padding(.vertical, 10)
        }
    }

    private func setAll(_ ids: [String], on: Bool) async {
        for id in ids {
            guard let e = model.entity(id), let pair = DeviceDirectory.switchPair(model.kind(of: e)) else { continue }
            if DeviceDirectory.isOn(e, model.kind(of: e)) == on { continue }
            await model.run(id, on ? pair.on : pair.off)
        }
    }

    private func allOff(_ rooms: [LightRooms.Room]) async {
        await setAll(rooms.flatMap { $0.rows.map { $0.id } }, on: false)
    }
}
