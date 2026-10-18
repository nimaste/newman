import SwiftUI

/// Everything about one device: its state, every reading it reports (properties), and every
/// command with the control its valueType asks for (CommandControl).
struct DeviceSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let entityId: String
    @State private var openLight: AppModel.DeviceLink?
    @State private var muteTarget: MuteTarget?

    var body: some View {
        NavigationStack {
            ZStack {
                NightGlassBackground(teal: false)
                if let e = model.entity(entityId) {
                    let kind = model.kind(of: e)
                    ScrollView {
                        VStack(alignment: .leading, spacing: 14) {
                            header(e, kind)
                            problems(e)
                            commands(e, kind)
                            remoteControls(e)
                            readings(e)
                            details(e, kind)
                        }
                        .padding(16)
                    }
                } else {
                    Text("This device is gone.").font(AppFont.manrope(15, .bold)).foregroundColor(Theme.muted)
                }
            }
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() }.foregroundColor(Theme.text) }
            }
            .toolbarBackground(.hidden, for: .navigationBar)
            .sheet(item: $openLight) { link in
                DeviceSheet(entityId: link.id).environmentObject(model)
            }
            .muteDialog($muteTarget)
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .presentationBackground(Theme.background)
        .fullDynamicType()
    }

    private func header(_ e: Entity, _ kind: Kind?) -> some View {
        let tone = DeviceLogic.tone(e, kind, problem: DeviceLogic.vitals(e).problem)
        return HStack(spacing: 14) {
            Image(systemName: Symbols.name(DeviceLogic.kindIcon(kind)))
                .font(.system(size: 24, weight: .medium))
                .frame(width: 52, height: 52)
                .background(Circle().fill(Color.white.opacity(0.08)))
                .foregroundColor(Theme.text)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(e.name).font(AppFont.sora(22)).foregroundColor(Theme.text)
                Text(DeviceLogic.stateText(e, kind, long: true))
                    .font(AppFont.manrope(14, .bold))
                    .foregroundColor(tone == .warn ? Theme.coralText : (tone == .calm ? Theme.teal : Theme.muted))
            }
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isHeader)
        }
    }

    @ViewBuilder private func commands(_ e: Entity, _ kind: Kind?) -> some View {
        let plain = DeviceLogic.plainCommands(kind)
        let valued = (kind?.commandList ?? []).filter { CommandControl.forCommand($0) != .button }
        if !plain.isEmpty {
            LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
                ForEach(plain) { c in
                    Button {
                        Task { await model.run(e.id, c) }
                    } label: {
                        HStack(spacing: 6) {
                            if DeviceLogic.needsAuthentication(c, biometricsEnabled: model.biometricsEnabled) {
                                Image(systemName: "faceid").accessibilityHidden(true)
                            } else {
                                Image(systemName: Symbols.name(c.icon)).accessibilityHidden(true)
                            }
                            Text(c.label).lineLimit(1).minimumScaleFactor(0.8)
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(PillButtonStyle(height: 48))
                    .accessibilityLabel(c.label)
                    .accessibilityHint(DeviceLogic.needsAuthentication(c, biometricsEnabled: model.biometricsEnabled)
                                       ? "Asks for \(DeviceAuth.methodName) first" : "")
                    .disabled(!e.isAvailable || model.busy.contains(e.id) || (c.confirmsTo != nil && c.confirmsTo != "" && c.confirmsTo == e.state))
                }
            }
        }
        ForEach(valued) { c in
            CommandControlView(entity: e, kind: kind, command: c)
                .glassCard(radius: 18, padding: 14)
                .disabled(!e.isAvailable)
        }
    }

    /// A remote's Controls (set on the dashboard): the lights it controls - each opens its own sheet - with
    /// All on / All off, then what each press does.
    @ViewBuilder private func remoteControls(_ e: Entity) -> some View {
        if let controls = e.controls {
            let lights = controls.lights.compactMap { model.entity($0) }
            VStack(alignment: .leading, spacing: 10) {
                CardHeader(text: controls.heading)
                ForEach(Array(lights.enumerated()), id: \.element.id) { i, light in
                    Button {
                        openLight = AppModel.DeviceLink(id: light.id)
                    } label: {
                        HStack(spacing: 12) {
                            Circle()
                                .fill(DeviceLogic.isLit(light, model.kind(of: light)) ? Theme.amber : Theme.faint)
                                .frame(width: 10, height: 10)
                                .accessibilityHidden(true)
                            Text(light.name).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text).lineLimit(2)
                            Spacer(minLength: 8)
                            Text(lightLevel(light))
                                .font(AppFont.manrope(13, .bold))
                                .foregroundColor(DeviceLogic.isLit(light, model.kind(of: light)) ? Theme.amber : Theme.muted)
                        }
                        .frame(minHeight: 44)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityElement(children: .combine)
                    .accessibilityHint("Opens \(light.name)")
                    if i < lights.count - 1 { Divider().overlay(Theme.divider) }
                }
                if !lights.isEmpty {
                    HStack(spacing: 10) {
                        allLights("All on", command: "turn_on", lights)
                        allLights("All off", command: "turn_off", lights)
                    }
                }
            }
            .glassCard(radius: 18, padding: 14)
            if !controls.rows.isEmpty {
                VStack(spacing: 0) {
                    ForEach(Array(controls.rows.enumerated()), id: \.element.id) { i, row in
                        HStack(alignment: .firstTextBaseline, spacing: 12) {
                            Text(row.label).font(AppFont.manrope(14, .heavy)).foregroundColor(Theme.text)
                                .frame(minWidth: 96, alignment: .leading)
                            Text(row.does).font(AppFont.manrope(14, .bold))
                                .foregroundColor(row.custom ? Theme.text : Theme.muted)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .padding(.vertical, 11)
                        .accessibilityElement(children: .combine)
                        if i < controls.rows.count - 1 { Divider().overlay(Theme.divider) }
                    }
                }
                .glassCard(radius: 18, padding: 14)
            }
        }
    }

    /// "60%" for a light that's on at a level, its state otherwise ("On", "Off").
    private func lightLevel(_ light: Entity) -> String {
        let kind = model.kind(of: light)
        if DeviceLogic.isLit(light, kind), let b = light.attr("brightness")?.doubleValue,
           light.attr("brightness")?.stringValue == nil, light.attr("dimmable")?.boolValue != false {
            return "\(Int(b.rounded()))%"
        }
        return DeviceLogic.stateLabel(light, kind)
    }

    /// Turns every one of the remote's lights on or off (each through the usual command path).
    private func allLights(_ title: String, command: String, _ lights: [Entity]) -> some View {
        Button {
            Task {
                for light in lights where light.isAvailable {
                    if let cmd = model.kind(of: light)?.command(command) { await model.run(light.id, cmd) }
                }
            }
        } label: {
            Text(title).frame(maxWidth: .infinity)
        }
        .buttonStyle(PillButtonStyle(height: 44))
        .disabled(!lights.contains { $0.isAvailable })
    }

    @ViewBuilder private func readings(_ e: Entity) -> some View {
        let props = e.properties ?? []
        if !props.isEmpty {
            VStack(spacing: 0) {
                ForEach(Array(props.enumerated()), id: \.element.id) { i, p in
                    HStack {
                        Text(p.label ?? DeviceLogic.humanize(p.id)).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                        Spacer()
                        Text(DeviceLogic.formatProp(p, p.value)).font(AppFont.manrope(15, .bold)).foregroundColor(Theme.muted)
                    }
                    .frame(minHeight: 46)
                    .accessibilityElement(children: .combine)
                    if i < props.count - 1 { Divider().overlay(Theme.divider) }
                }
            }
            .glassCard(radius: 18, padding: 14)
        }
    }

    /// Its problems, each with Mute, then the muted ones (until when, and Unmute).
    @ViewBuilder private func problems(_ e: Entity) -> some View {
        let active = MuteLogic.active(e)
        let muted = MuteLogic.muted(e)
        if !active.isEmpty {
            VStack(spacing: 0) {
                ForEach(Array(active.enumerated()), id: \.offset) { i, p in
                    HStack(spacing: 10) {
                        Image(systemName: "exclamationmark.triangle").font(.system(size: 14, weight: .bold))
                            .foregroundColor(Theme.coralText).accessibilityHidden(true)
                        Text(p.text).font(AppFont.manrope(14, .heavy)).foregroundColor(Theme.coralText)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        if MuteLogic.canMute(p) {
                            Button("Mute") { muteTarget = MuteTarget(entityId: e.id, device: e.name, issue: p.id, text: p.text) }
                                .buttonStyle(PillButtonStyle(height: 36))
                                .accessibilityHint(p.text)
                        }
                    }
                    .frame(minHeight: 44)
                    if i < active.count - 1 { Divider().overlay(Theme.divider) }
                }
            }
            .glassCard(padding: 14, fill: Theme.coral.opacity(0.10), border: Theme.coral.opacity(0.35))
        }
        if !muted.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                CardHeader(text: "Muted").padding(.bottom, 4)
                ForEach(Array(muted.enumerated()), id: \.offset) { i, p in
                    HStack(spacing: 10) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(p.text).font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted)
                            if let m = p.muted {
                                Text(MuteLogic.mutedText(m)).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.dim)
                            }
                        }
                        .accessibilityElement(children: .combine)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        Button("Unmute") { Task { await model.unmute(e.id, issue: p.id) } }
                            .buttonStyle(PillButtonStyle(height: 36))
                            .accessibilityHint(p.text)
                    }
                    .padding(.vertical, 6)
                    if i < muted.count - 1 { Divider().overlay(Theme.divider) }
                }
            }
            .glassCard(padding: 14)
        }
    }

    @ViewBuilder private func details(_ e: Entity, _ kind: Kind?) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text([kind?.name, e.integrationName, e.area].compactMap { $0 }.joined(separator: " · "))
                .font(AppFont.manrope(12, .bold)).foregroundColor(Theme.dim)
        }
    }
}

/// One value command's control, picked by valueType: slider (percent), stepper (number),
/// state chips (state), item chips + Go (choices), text field + Send (text).
struct CommandControlView: View {
    @EnvironmentObject var model: AppModel
    let entity: Entity
    let kind: Kind?
    let command: Command

    @State private var number: Double?
    @State private var text = ""
    @State private var picked: [JSONValue] = []
    @State private var pending: Task<Void, Never>?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(command.label).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                if DeviceLogic.needsAuthentication(command, biometricsEnabled: model.biometricsEnabled) {
                    Image(systemName: "faceid").foregroundColor(Theme.muted)
                        .accessibilityLabel("Asks for \(DeviceAuth.methodName)")
                }
                Spacer()
                Text(currentText).font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted)
            }
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isHeader)
            control
        }
    }

    private var currentText: String {
        switch CommandControl.forCommand(command) {
        case .slider, .stepper:
            let v = number ?? CommandControl.startValue(entity, command)
            return DeviceLogic.valueText(command, kind, .string(JSONValue.format(v)))
        case .statePicker, .choices:
            return DeviceLogic.valueText(command, kind, DeviceLogic.commandCurrent(entity, command), entity: entity)
        default:
            return ""
        }
    }

    @ViewBuilder private var control: some View {
        switch CommandControl.forCommand(command) {
        case .slider(let lo, let hi, let step, _):
            Slider(value: Binding(get: { number ?? CommandControl.startValue(entity, command) }, set: { number = $0 }),
                   in: lo...max(hi, lo + step), step: step, onEditingChanged: { editing in
                if !editing, let v = number { send(.number(CommandControl.snapped(v, command))) }
            })
            .tint(Theme.amber)
            .accessibilityLabel(command.label)
            .accessibilityValue(currentText)
        case .stepper(let lo, let hi, let step, _):
            HStack(spacing: 24) {
                roundButton("minus") { bump(-step, lo, hi) }
                    .accessibilityLabel("Lower \(command.label.lowercased())")
                Text(DeviceLogic.valueText(command, kind, .string(JSONValue.format(number ?? CommandControl.startValue(entity, command)))))
                    .font(AppFont.sora(30)).foregroundColor(Theme.text).frame(minWidth: 110)
                    .lineLimit(1).minimumScaleFactor(0.6)
                    // Also adjustable: VoiceOver swipe up / down on the value.
                    .accessibilityLabel(command.label)
                    .accessibilityValue(currentText)
                    .accessibilityAdjustableAction { dir in bump(dir == .increment ? step : -step, lo, hi) }
                roundButton("plus") { bump(step, lo, hi) }
                    .accessibilityLabel("Raise \(command.label.lowercased())")
            }
            .frame(maxWidth: .infinity)
        case .statePicker:
            FlowChips(items: DeviceLogic.stateChoices(kind).map { (id: $0.id, label: $0.label) }, selected: entity.state.map { [$0] } ?? []) { id in
                if id != entity.state { send(.string(id)) }
            }
        case .choices:
            let items = DeviceLogic.choiceItems(entity, command)
            if items.isEmpty {
                Text("\(entity.name) hasn't listed any yet.").font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted)
            } else {
                FlowChips(items: items.map { (id: $0.id.displayText, label: $0.name) }, selected: picked.map { $0.displayText }) { id in
                    if let item = items.first(where: { $0.id.displayText == id }) {
                        if let i = picked.firstIndex(of: item.id) { picked.remove(at: i) } else { picked.append(item.id) }
                    }
                }
                Button(picked.isEmpty ? "Go" : "Go · \(picked.count)") {
                    let ids = items.map { $0.id }.filter { picked.contains($0) }
                    send(.array(ids))
                    picked = []
                }
                .buttonStyle(PillButtonStyle(prominent: true))
                .disabled(picked.isEmpty)
            }
        case .text:
            HStack {
                TextField(command.defaultValue?.displayText ?? "Value", text: $text)
                    .textInputAutocapitalization(.never)
                    .font(AppFont.manrope(15, .semibold))
                    .foregroundColor(Theme.text)
                    .padding(.horizontal, 12)
                    .frame(minHeight: 44)
                    .accessibilityLabel(command.label)
                    .background(RoundedRectangle(cornerRadius: 12).fill(Theme.control))
                Button("Send") {
                    send(.string(text))
                    text = ""
                }
                .buttonStyle(PillButtonStyle(prominent: true))
            }
        case .button:
            EmptyView()
        }
    }

    private func roundButton(_ symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 22, weight: .semibold))
                .frame(width: 56, height: 56)
                .background(Circle().fill(Color.white.opacity(0.06)))
                .overlay(Circle().stroke(Color.white.opacity(0.16)))
                .foregroundColor(Theme.text)
        }
        .disabled(model.busy.contains(entity.id))
    }

    /// Steppers send ~600 ms after the last tap, like the wall's dial.
    private func bump(_ d: Double, _ lo: Double, _ hi: Double) {
        let v = CommandControl.snapped(min(hi, max(lo, (number ?? CommandControl.startValue(entity, command)) + d)), command)
        number = v
        pending?.cancel()
        pending = Task {
            try? await Task.sleep(nanoseconds: 600_000_000)
            if !Task.isCancelled { send(.number(v)) }
        }
    }

    private func send(_ value: JSONValue) {
        Task {
            await model.run(entity.id, command, value: value)
            number = nil
        }
    }
}

/// Wrapping chips (state choices, a vacuum's rooms).
struct FlowChips: View {
    let items: [(id: String, label: String)]
    let selected: [String]
    let onTap: (String) -> Void

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 96), spacing: 6)], alignment: .leading, spacing: 6) {
            ForEach(items, id: \.id) { item in
                let on = selected.contains(item.id)
                Button { onTap(item.id) } label: {
                    HStack(spacing: 4) {
                        if on { Image(systemName: "checkmark").font(.system(size: 12, weight: .heavy)).accessibilityHidden(true) }
                        Text(item.label).lineLimit(1).minimumScaleFactor(0.8)
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(PillButtonStyle(selected: on, height: 44))
            }
        }
    }
}
