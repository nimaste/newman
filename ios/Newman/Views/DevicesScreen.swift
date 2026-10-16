import SwiftUI

/// More › Devices: every device the hub has, whatever tab it's on (or none), by category in the
/// hub's order (DeviceDirectory). Search by name, or show only those needing attention or on now.
/// Each row has its quick control (a switch, or the one action that makes sense now); tapping it
/// opens everything the device can do.
struct DevicesScreen: View {
    @EnvironmentObject var model: AppModel
    @State private var query = ""
    @State private var filter: DeviceDirectory.Filter = .all
    @State private var expanded: Set<String> = []

    /// Rows a section shows before "Show all".
    private let firstRows = 5

    var body: some View {
        let all = model.allEntities
        let shown = DeviceDirectory.matching(all, kinds: model.kinds, query: query, filter: filter)
        let sections = DeviceDirectory.sections(shown, kinds: model.kinds, categories: model.meta?.categories ?? [])
        let attention = all.filter(DeviceDirectory.needsAttention).count
        let on = all.filter { DeviceDirectory.isOn($0, model.kinds[$0.kind]) }.count
        let searching = !query.trimmingCharacters(in: .whitespaces).isEmpty
        return ZStack {
            NightGlassBackground(teal: false)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    Text("Devices").font(AppFont.sora(28)).foregroundColor(Theme.text)
                        .accessibilityAddTraits(.isHeader)
                    search(count: all.count)
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            chip(.all, "All \(all.count)")
                            if attention > 0 { chip(.attention, "Needs attention \(attention)") }
                            if on > 0 { chip(.on, "On now \(on)") }
                        }
                    }
                    if sections.isEmpty {
                        Text(searching ? "No device called \"\(query)\"." : "Nothing here.")
                            .font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted).padding(.top, 20)
                    }
                    ForEach(sections) { s in
                        section(s, all: searching || filter != .all || expanded.contains(s.id))
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 32)
            }
            .scrollDismissesKeyboard(.interactively)
            .refreshable { await model.reloadAll() }
        }
    }

    private func search(count: Int) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass").font(.system(size: 16, weight: .semibold)).foregroundColor(Theme.dim)
                .accessibilityHidden(true)
            TextField("", text: $query, prompt: Text("Search \(count) devices").foregroundColor(Theme.faint))
                .font(AppFont.manrope(15, .bold))
                .foregroundColor(Theme.text)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .accessibilityLabel("Search devices")
            if !query.isEmpty {
                Button { query = "" } label: {
                    Image(systemName: "xmark.circle.fill").foregroundColor(Theme.dim)
                }
                .accessibilityLabel("Clear the search")
            }
        }
        .padding(.horizontal, 14)
        .frame(minHeight: 46)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Theme.card))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(Theme.cardBorder))
    }

    private func chip(_ f: DeviceDirectory.Filter, _ label: String) -> some View {
        Button(label) { filter = f }
            .buttonStyle(PillButtonStyle(selected: filter == f, height: 36))
            .accessibilityHint("Shows only these")
    }

    private func section(_ s: DeviceDirectory.Section, all: Bool) -> some View {
        let ids = all ? s.ids : Array(s.ids.prefix(firstRows))
        return VStack(alignment: .leading, spacing: 0) {
            CardHeader(text: "\(s.title) · \(s.ids.count)") {
                if s.ids.count > firstRows && filter == .all && query.trimmingCharacters(in: .whitespaces).isEmpty {
                    Button(expanded.contains(s.id) ? "Show fewer" : "Show all") {
                        if expanded.contains(s.id) { expanded.remove(s.id) } else { expanded.insert(s.id) }
                    }
                    .font(AppFont.manrope(13, .heavy))
                    .foregroundColor(Theme.blue)
                    .accessibilityHint("\(s.ids.count) \(s.title.lowercased())")
                }
            }
            .padding(.bottom, 4)
            ForEach(Array(ids.enumerated()), id: \.element) { i, id in
                DirectoryRow(entityId: id)
                if i < ids.count - 1 { Divider().overlay(Theme.divider) }
            }
        }
        .glassCard(padding: 14)
    }
}

/// One device in the directory: its icon, name and state (or its problems), and its quick control.
struct DirectoryRow: View {
    @EnvironmentObject var model: AppModel
    let entityId: String
    @State private var sheet = false

    var body: some View {
        if let e = model.entity(entityId) {
            let kind = model.kind(of: e)
            let bad = DeviceDirectory.needsAttention(e)
            let lit = DeviceDirectory.isOn(e, kind)
            let tone = DeviceLogic.tone(e, kind, problem: DeviceLogic.vitals(e).problem)
            let status = DeviceDirectory.statusLine(e, kind)
            let tint: Color = bad || tone == .warn ? Theme.coralText : (lit ? Theme.amber : (tone == .calm ? Theme.teal : Theme.text))
            HStack(spacing: 12) {
                Image(systemName: bad ? "exclamationmark.triangle" : Symbols.name(DeviceLogic.kindIcon(kind)))
                    .font(.system(size: 17, weight: .medium))
                    .foregroundColor(lit ? Theme.amberInk : tint)
                    .frame(width: 38, height: 38)
                    .background(Circle().fill(lit ? Theme.amber : tint.opacity(tint == Theme.text ? 0.08 : 0.16)))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(e.name).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text).lineLimit(2)
                    if !status.isEmpty {
                        Text(status).font(AppFont.manrope(13, .bold))
                            .foregroundColor(bad || tone == .warn ? Theme.coralText : (lit ? Theme.amber : (tone == .calm ? Theme.teal : Theme.muted)))
                            .lineLimit(2)
                    }
                }
                .accessibilityElement(children: .combine)
                .accessibilityAddTraits(.isButton)
                .accessibilityHint("Opens its controls")
                .accessibilityAction { sheet = true }
                Spacer(minLength: 4)
                control(e, kind)
            }
            .frame(minHeight: 60)
            .contentShape(Rectangle())
            .onTapGesture { sheet = true }
            .opacity(e.isAvailable ? 1 : 0.7)
            .sheet(isPresented: $sheet) { DeviceSheet(entityId: e.id).environmentObject(model) }
        }
    }

    @ViewBuilder private func control(_ e: Entity, _ kind: Kind?) -> some View {
        let busy = model.busy.contains(e.id)
        if let pair = DeviceDirectory.switchPair(kind) {
            Toggle(isOn: Binding(
                get: { DeviceLogic.isActive(e, kind) },
                set: { on in Task { await model.run(e.id, on ? pair.on : pair.off) } }
            )) { EmptyView() }
            .labelsHidden()
            .toggleStyle(SwitchToggleStyle(tint: Theme.amber))
            .disabled(!e.isAvailable || busy)
            .accessibilityLabel(e.name)
        } else if let action = DeviceLogic.nextAction(e, kind) {
            Button {
                Task { await model.run(e.id, action) }
            } label: {
                HStack(spacing: 6) {
                    if busy {
                        ProgressView().tint(Theme.text)
                    } else if DeviceLogic.needsAuthentication(action, biometricsEnabled: model.biometricsEnabled) {
                        Image(systemName: "faceid").font(.system(size: 13, weight: .semibold)).accessibilityHidden(true)
                    }
                    Text(action.label).lineLimit(1)
                }
            }
            .buttonStyle(PillButtonStyle(height: 36))
            .disabled(!e.isAvailable || busy)
            .accessibilityLabel("\(action.label) \(e.name)")
            .accessibilityHint(DeviceLogic.needsAuthentication(action, biometricsEnabled: model.biometricsEnabled)
                               ? "Asks for \(DeviceAuth.methodName) first" : "")
        }
    }
}
