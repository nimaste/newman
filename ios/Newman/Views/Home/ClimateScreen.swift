import SwiftUI

/// Climate: the thermostat's big dial (− / +, its modes), its humidity, and the rooms - each one's
/// temperature with a bar on the house's range, coloured by the house's bands (web Settings › Climate).
struct ClimateScreen: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        let climate = model.home?.climate ?? HomeData.Climate()
        ZStack {
            NightGlassBackground(teal: false)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Text("Climate").font(AppFont.sora(28)).foregroundColor(Theme.text)
                        .accessibilityAddTraits(.isHeader)
                    if let id = climate.thermostat, let e = model.entity(id), let dial = ClimateScreen.dial(e, model.kind(of: e)) {
                        VStack(spacing: 12) {
                            DialControl(dial: dial, size: 250)
                            if let p = dial.percentProperty {
                                Text("\(p.label ?? "Humidity") \(DeviceLogic.formatProp(p, p.value))")
                                    .font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                        .glassCard(padding: 16)
                    } else {
                        Text("No thermostat yet.").font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .glassCard(padding: 16)
                    }
                    rooms(climate)
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 32)
            }
            .refreshable { await model.reloadHome() }
        }
    }

    private func rooms(_ c: HomeData.Climate) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            CardHeader(text: "Rooms") {
                Button("Edit") { model.homePath.append(.climateRooms) }
                    .font(AppFont.manrope(13, .heavy))
                    .foregroundColor(Theme.blue)
                    .accessibilityHint("Choose which rooms show")
            }
            .padding(.bottom, 4)
            if c.rooms.isEmpty {
                Text("No rooms chosen yet.").font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted).padding(.vertical, 8)
            }
            ForEach(Array(c.rooms.enumerated()), id: \.element.id) { i, r in
                let value = model.entity(r.entity).flatMap(HomeLogic.temperature) ?? r.value
                HStack(spacing: 12) {
                    Text(r.name).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                        .lineLimit(1).minimumScaleFactor(0.8)
                    Spacer(minLength: 8)
                    if let value {
                        ZStack(alignment: .leading) {
                            Capsule().fill(Theme.control).frame(width: 90, height: 6)
                            Capsule().fill(Color(hex: HomeLogic.bandColor(value, bands: c.bands)))
                                .frame(width: 90 * HomeLogic.barFraction(value, range: c.range), height: 6)
                        }
                        .accessibilityHidden(true)
                    }
                    Text(value.map { JSONValue.format($0, maxDecimals: 1) + "°" } ?? "—")
                        .font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                        .frame(minWidth: 52, alignment: .trailing)
                }
                .frame(minHeight: 44)
                .accessibilityElement(children: .combine)
                if i < c.rooms.count - 1 { Divider().overlay(Theme.divider) }
            }
        }
        .glassCard(padding: 14)
    }

    /// The thermostat's dial: its number command (the set temperature) and its mode command.
    static func dial(_ e: Entity, _ kind: Kind?) -> DialModel? {
        guard let kind, let cmd = kind.commandList.first(where: { $0.resolvedValueType == "number" }) else { return nil }
        return DialModel(entity: e, kind: kind, command: cmd, modeCommand: kind.commandList.first { $0.resolvedValueType == "state" })
    }
}

/// Rooms on Climate: every temperature sensor (any brand), ticked to show, dragged to order, renamed here.
/// Saved for the whole house.
struct ClimateRoomsScreen: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var rows: [Row] = []
    @State private var loaded = false
    @State private var saving = false

    struct Row: Identifiable, Hashable {
        var id: String
        var name: String
        var detail: String
        var on: Bool
    }

    var body: some View {
        ZStack {
            NightGlassBackground(teal: false)
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("Rooms on Climate").font(AppFont.sora(24)).foregroundColor(Theme.text)
                        .accessibilityAddTraits(.isHeader)
                    Spacer()
                    Button {
                        saving = true
                        Task {
                            let chosen = rows.filter { $0.on }.map { HomeData.Room(entity: $0.id, name: $0.name, value: nil) }
                            if await model.saveClimateRooms(chosen) { dismiss() }
                            saving = false
                        }
                    } label: {
                        if saving { ProgressView().tint(Theme.background) } else { Text("Done") }
                    }
                    .buttonStyle(PillButtonStyle(height: 38, prominent: true))
                    .disabled(saving)
                }
                .padding(.horizontal, 16)
                .padding(.top, 8)
                Text("Every temperature sensor, whatever makes it. Tick the ones to show, drag to order, tap a name to rename it here.")
                    .font(AppFont.manrope(13, .semibold)).foregroundColor(Theme.muted)
                    .padding(.horizontal, 16)
                List {
                    ForEach($rows) { $row in
                        HStack(spacing: 12) {
                            Toggle("", isOn: $row.on).labelsHidden().toggleStyle(SwitchToggleStyle(tint: Theme.blue))
                                .accessibilityLabel("Show \(row.name)")
                            VStack(alignment: .leading, spacing: 2) {
                                TextField("Name", text: $row.name)
                                    .font(AppFont.manrope(15, .heavy))
                                    .foregroundColor(row.on ? Theme.text : Theme.muted)
                                Text(row.detail).font(AppFont.manrope(12, .semibold)).foregroundColor(Theme.dim)
                            }
                        }
                        .listRowBackground(Theme.card)
                    }
                    .onMove { rows.move(fromOffsets: $0, toOffset: $1) }
                }
                .scrollContentBackground(.hidden)
                .environment(\.editMode, .constant(.active))
            }
        }
        .onAppear(perform: load)
    }

    private func load() {
        guard !loaded else { return }
        loaded = true
        let chosen = model.home?.climate.rooms ?? []
        let sensors = HomeLogic.temperatureSensors(model.allEntities)
        func detail(_ id: String) -> String {
            guard let e = model.entity(id) else { return "" }
            let t = HomeLogic.temperature(e).map { JSONValue.format($0, maxDecimals: 1) + "°" } ?? ""
            return [e.name, e.integrationName ?? "", t].filter { !$0.isEmpty }.joined(separator: " · ")
        }
        var out = chosen.map { Row(id: $0.entity, name: $0.name, detail: detail($0.entity), on: true) }
        for s in sensors where !out.contains(where: { $0.id == s.id }) {
            out.append(Row(id: s.id, name: s.name, detail: detail(s.id), on: false))
        }
        rows = out
    }
}
