import SwiftUI

/// Security: the alarm's modes, then a section per kind of device the hub shows here (every category whose
/// screen is "security" - SecuritySections), titled with the kind's plural name: kinds with buttons (lock,
/// open…) as rows with their button and battery, the rest as tiles; then today's security activity.
/// Never a list of kinds or headings.
struct SecurityScreen: View {
    @EnvironmentObject var model: AppModel
    @State private var sheetFor: HomeSheet?

    var body: some View {
        let all = model.allEntities
        let mode = HouseStatus.modeDevice(all, kinds: model.kinds)
        let screens = model.categoryScreens
        let secure = SecuritySections.members(all, kinds: model.kinds, screens: screens)
        let sections = SecuritySections.sections(all, kinds: model.kinds, screens: screens)
        let bad = secure.filter { DeviceLogic.notSecure($0, model.kind(of: $0)) }
        ZStack {
            NightGlassBackground(teal: false)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Security").font(AppFont.sora(28)).foregroundColor(Theme.text)
                            .accessibilityAddTraits(.isHeader)
                        Text(model.home?.status.summary.isEmpty == false ? model.home?.status.summary ?? "" :
                                (bad.isEmpty ? "All secure" : bad.map { $0.name }.joined(separator: ", ") + " not secure"))
                            .font(AppFont.manrope(13, .heavy)).foregroundColor(bad.isEmpty ? Theme.teal : Theme.coralText)
                    }
                    if let mode { modeCard(mode) }
                    ForEach(sections) { s in section(s) }
                    SecurityTodayCard(ids: Set(secure.map { $0.id } + (mode.map { [$0.id] } ?? [])))
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 32)
            }
            .refreshable { await model.reloadAll() }
        }
        .sheet(item: $sheetFor) { s in DeviceSheet(entityId: s.id).environmentObject(model) }
    }

    /// One kind's devices: rows with their button, or tiles.
    @ViewBuilder private func section(_ s: SecuritySections.Section) -> some View {
        let devices = s.ids.compactMap { model.entity($0) }
        if s.actionable {
            VStack(alignment: .leading, spacing: 0) {
                CardHeader(text: s.title).padding(.bottom, 4)
                ForEach(Array(devices.enumerated()), id: \.element.id) { i, e in
                    actionRow(e)
                    if i < devices.count - 1 { Divider().overlay(Theme.divider) }
                }
            }
            .glassCard(padding: 14)
        } else {
            VStack(alignment: .leading, spacing: 10) {
                CardHeader(text: s.title)
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 3), spacing: 8) {
                    ForEach(devices) { e in sensorTile(e) }
                }
            }
            .glassCard(padding: 14)
        }
    }

    /// The alarm: one button per mode it can be set to.
    private func modeCard(_ e: Entity) -> some View {
        let kind = model.kind(of: e)
        let cmd = kind?.commandList.first { $0.resolvedValueType == "state" }
        return VStack(alignment: .leading, spacing: 10) {
            CardHeader(text: e.name) {
                Text(e.integrationName ?? "").font(AppFont.manrope(12, .bold)).foregroundColor(Theme.dim)
            }
            if let cmd {
                HStack(spacing: 6) {
                    ForEach(DeviceLogic.stateChoices(kind), id: \.id) { s in
                        Button(s.label) { Task { await model.run(e.id, cmd, value: .string(s.id)) } }
                            .buttonStyle(PillButtonStyle(selected: e.state == s.id, height: 44))
                            .frame(maxWidth: .infinity)
                    }
                }
            } else {
                Text(DeviceLogic.stateText(e, kind)).font(AppFont.manrope(16, .heavy)).foregroundColor(Theme.text)
            }
        }
        .glassCard(padding: 14)
    }

    private func actionRow(_ e: Entity) -> some View {
        let kind = model.kind(of: e)
        let notSecure = DeviceLogic.notSecure(e, kind)
        let battery = DeviceLogic.vitals(e).battery
        let line = [DeviceLogic.stateText(e, kind), battery.map { "battery \($0)%" }].compactMap { $0 }.joined(separator: " · ")
        return HStack(spacing: 12) {
            Button { sheetFor = HomeSheet(id: e.id) } label: {
                VStack(alignment: .leading, spacing: 2) {
                    Text(e.name).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                    Text(line).font(AppFont.manrope(12, .bold)).foregroundColor(notSecure ? Theme.coralText : Theme.teal)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if let next = DeviceLogic.nextAction(e, kind) {
                Button(next.label) { Task { await model.run(e.id, next) } }
                    .buttonStyle(PillButtonStyle(height: 38))
                    .disabled(!e.isAvailable || model.busy.contains(e.id))
            }
        }
        .frame(minHeight: 56)
    }

    private func sensorTile(_ e: Entity) -> some View {
        let kind = model.kind(of: e)
        let notSecure = DeviceLogic.notSecure(e, kind)
        return Button { sheetFor = HomeSheet(id: e.id) } label: {
            VStack(alignment: .leading, spacing: 2) {
                Text(e.name).font(AppFont.manrope(13, .heavy)).foregroundColor(Theme.text).lineLimit(2).minimumScaleFactor(0.8)
                Text(e.isAvailable ? DeviceLogic.stateText(e, kind) : "Offline").font(AppFont.manrope(12, .bold))
                    .foregroundColor(notSecure || !e.isAvailable ? Theme.coralText : Theme.teal)
            }
            .frame(maxWidth: .infinity, minHeight: 52, alignment: .topLeading)
            .padding(10)
            .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Theme.control))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(notSecure ? Theme.coral.opacity(0.5) : Theme.controlBorder))
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
    }
}

/// Security's TODAY: GET /api/alerts?category=security&since=<start of today>, newest first, "<time>  <title>";
/// older hubs: today's activity of these devices and the cameras.
struct SecurityTodayCard: View {
    @EnvironmentObject var model: AppModel
    let ids: Set<String>
    @State private var entries: [SecurityEntry]?

    var body: some View {
        let list = entries ?? fallback
        return VStack(alignment: .leading, spacing: 0) {
            CardHeader(text: "Today") {
                Button("All alerts") { model.showAlerts() }
                    .font(AppFont.manrope(13, .heavy))
                    .foregroundColor(Theme.blue)
            }
            .padding(.bottom, 4)
            if list.isEmpty {
                Text("Nothing yet today.").font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted).padding(.vertical, 8)
            }
            ForEach(Array(list.enumerated()), id: \.element.id) { i, ev in
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    Text(HubDates.time(Date(timeIntervalSince1970: ev.at)))
                        .font(AppFont.manrope(13, .heavy)).foregroundColor(Theme.blue)
                        .frame(width: 72, alignment: .leading)
                    Text(ev.title).font(AppFont.manrope(14, .bold)).foregroundColor(Theme.text)
                    Spacer(minLength: 0)
                }
                .padding(.vertical, 9)
                .accessibilityElement(children: .combine)
                if i < list.count - 1 { Divider().overlay(Theme.divider) }
            }
        }
        .glassCard(padding: 14)
        .task(id: model.events.first?.id) { await load() }
    }

    private var start: Double { Calendar.current.startOfDay(for: Date()).timeIntervalSince1970 }

    private func load() async {
        if let list = try? await APIClient.shared.securityActivity(since: start) { entries = SecurityEntry.sorted(list) }
    }

    private var fallback: [SecurityEntry] {
        let cams = Set(model.allEntities.filter { model.kind(of: $0)?.hasCamera == true }.map { $0.id })
        let list = model.events.filter { ev in
            guard ev.ts >= start, let id = ev.entityId else { return false }
            return ids.contains(id) || cams.contains(id)
        }
        return SecurityEntry.sorted(list.map { SecurityEntry(id: $0.id, at: $0.ts, title: $0.title, entity: $0.entityId) })
    }
}
