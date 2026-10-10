import SwiftUI

/// A device as a big card (a "devices" tile in the big-tiles look): tap runs its main action
/// (on/off, lock/unlock…), a long press opens everything it can do. Wide cards (a whole
/// Lights page) also get a brightness bar when the kind has a percent command.
struct DeviceCard: View {
    @EnvironmentObject var model: AppModel
    let entityId: String
    var wide: Bool
    var scale: Double = 1
    @State private var sheet = false

    var body: some View {
        if let e = model.entity(entityId) {
            let kind = model.kind(of: e)
            let action = DeviceLogic.tapAction(e, kind)
            let lit = DeviceLogic.isLit(e, kind)
            let vitals = DeviceLogic.vitals(e)
            let battery = vitals.battery
            let problem = vitals.problem
            let warn = DeviceLogic.tone(e, kind, problem: problem) == .warn
            let percent = kind?.commandList.first { $0.resolvedValueType == "percent" }
            let dimmable = percent != nil && e.attr("dimmable")?.boolValue != false
            let fg = lit ? Theme.amberInk : Theme.text
            let state = DeviceLogic.stateText(e, kind) + (battery.map { " · \($0)%" } ?? "")

            VStack(alignment: .leading, spacing: 10) {
                Group {
                    if wide {
                        HStack(spacing: 12) {
                            icon(kind, fg: fg, lit: lit, size: 44)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(e.name).font(AppFont.manrope(16 * scale, .heavy)).lineLimit(1)
                                Text([e.area, state].compactMap { $0 }.joined(separator: " · "))
                                    .font(AppFont.manrope(12 * scale, .bold)).opacity(0.8).lineLimit(1)
                                if let problem { Text(problem).font(AppFont.manrope(12 * scale, .bold)).foregroundColor(Theme.coralText) }
                            }
                            Spacer(minLength: 0)
                        }
                    } else {
                        VStack(alignment: .leading, spacing: 8) {
                            Image(systemName: Symbols.name(DeviceLogic.kindIcon(kind))).font(.system(size: 22, weight: .medium))
                            Spacer(minLength: 0)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(e.name).font(AppFont.manrope(14 * scale, .heavy)).lineLimit(2)
                                Text(problem ?? state).font(AppFont.manrope(12 * scale, .bold)).opacity(0.8).lineLimit(2)
                            }
                        }
                        .frame(maxWidth: .infinity, minHeight: 68, alignment: .leading)
                    }
                }
                .foregroundColor(fg)
                .contentShape(Rectangle())
                // Tap = the main action; a long press opens the sheet instead (never both).
                .onTapGesture {
                    guard !model.busy.contains(e.id) else { return }
                    if let action, e.isAvailable { Task { await model.run(e.id, action) } } else { sheet = true }
                }
                .onLongPressGesture(minimumDuration: 0.45) { sheet = true }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(e.name)
                .accessibilityValue(model.busy.contains(e.id) ? "Working" : [problem ?? state, wide ? e.area : nil].compactMap { $0 }.joined(separator: ", "))
                .accessibilityAddTraits(.isButton)
                .accessibilityHint(e.isAvailable ? (action?.label ?? "Opens its controls") : "Opens its controls")
                .accessibilityAction { // VoiceOver's double-tap, same as a tap
                    guard !model.busy.contains(e.id) else { return }
                    if let action, e.isAvailable { Task { await model.run(e.id, action) } } else { sheet = true }
                }
                .accessibilityAction(named: "More") { sheet = true }

                if wide, dimmable, let percent {
                    LevelBar(entity: e, command: percent, lit: lit)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .background(RoundedRectangle(cornerRadius: 20, style: .continuous).fill(lit ? Theme.amber : Theme.card))
            .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous)
                .stroke(warn ? Theme.coral.opacity(0.5) : (lit ? Color.clear : Theme.cardBorder), lineWidth: 1))
            .opacity(e.isAvailable ? 1 : 0.55)
            .sheet(isPresented: $sheet) { DeviceSheet(entityId: e.id).environmentObject(model) }
        }
    }

    private func icon(_ kind: Kind?, fg: Color, lit: Bool, size: CGFloat) -> some View {
        Image(systemName: Symbols.name(DeviceLogic.kindIcon(kind)))
            .font(.system(size: size * 0.45, weight: .medium))
            .frame(width: size, height: size)
            .background(Circle().fill(lit ? Color.black.opacity(0.12) : Color.white.opacity(0.08)))
    }
}

/// The Lights board's brightness bar: drag anywhere on it, sent when you let go.
struct LevelBar: View {
    @EnvironmentObject var model: AppModel
    let entity: Entity
    let command: Command
    let lit: Bool
    @State private var value: Double?
    @State private var dragging = false

    var body: some View {
        let lo = command.min ?? 0, hi = command.max ?? 100
        let current = ControlCommandValue.current(entity, command)
        let shown = value ?? current
        let frac = lit || dragging ? max(0, min(1, (shown - lo) / max(1, hi - lo))) : 0
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 12).fill(lit ? Color.black.opacity(0.14) : Color.white.opacity(0.08))
                RoundedRectangle(cornerRadius: 12).fill(lit ? Color.white.opacity(0.55) : Theme.amber.opacity(0.6))
                    .frame(width: geo.size.width * frac)
                Text(dragging || lit ? "\(Int(shown.rounded()))%" : command.label)
                    .font(AppFont.manrope(12, .heavy)).opacity(0.75).padding(.leading, 12)
                    .lineLimit(1).minimumScaleFactor(0.7)
            }
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 0)
                .onChanged { g in
                    dragging = true
                    value = CommandControl.snapped(lo + (hi - lo) * max(0, min(1, g.location.x / max(1, geo.size.width))), command)
                }
                .onEnded { _ in
                    dragging = false
                    if let v = value { Task { await model.run(entity.id, command, value: .number(v)); value = nil } }
                })
        }
        .frame(height: 40)
        .accessibilityElement()
        .accessibilityLabel("\(entity.name) \(command.label.lowercased())")
        .accessibilityValue("\(Int(shown.rounded())) percent")
        .accessibilityAdjustableAction { dir in
            let step = (command.step ?? 1) * 10
            let v = CommandControl.snapped(shown + (dir == .increment ? step : -step), command)
            Task { await model.run(entity.id, command, value: .number(v)) }
        }
    }
}

enum ControlCommandValue {
    static func current(_ e: Entity, _ cmd: Command) -> Double { CommandControl.startValue(e, cmd) }
}

/// A device as a list row (the Security board's locks): name, state, and the one action that
/// makes sense now; tapping the row opens everything else.
struct DeviceRow: View {
    @EnvironmentObject var model: AppModel
    let entityId: String
    var showArea = false
    var scale: Double = 1
    @State private var sheet = false

    var body: some View {
        if let e = model.entity(entityId) {
            let kind = model.kind(of: e)
            let action = DeviceLogic.nextAction(e, kind)
            let vitals = DeviceLogic.vitals(e)
            let battery = vitals.battery
            let problem = vitals.problem
            let tone = DeviceLogic.tone(e, kind, problem: problem)
            let toneColor = tone == .warn ? Theme.coralText : (tone == .calm ? Theme.teal : Theme.muted)
            let extras = DeviceLogic.extraActions(e, kind, main: action)
            HStack(spacing: 12) {
                Image(systemName: Symbols.name(DeviceLogic.kindIcon(kind)))
                    .font(.system(size: 18, weight: .medium))
                    .foregroundColor(tone == .warn ? Theme.coralText : (tone == .calm ? Theme.teal : Theme.text))
                    .frame(width: 40, height: 40)
                    .background(Circle().fill(tone == .warn ? Theme.coral.opacity(0.18) : (tone == .calm ? Theme.teal.opacity(0.16) : Color.white.opacity(0.08))))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(e.name).font(AppFont.manrope(15 * scale, .heavy)).foregroundColor(Theme.text).lineLimit(1)
                    Text([DeviceLogic.stateText(e, kind), battery.map { "\($0)%" }, problem, showArea ? e.area : nil]
                        .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                        .font(AppFont.manrope(13 * scale, .bold)).foregroundColor(toneColor).lineLimit(2)
                }
                // Name and state as one element; activating it opens everything the device can do
                // (the row's tap), the action buttons stay separate.
                .accessibilityElement(children: .combine)
                .accessibilityAddTraits(.isButton)
                .accessibilityHint("Opens its controls")
                .accessibilityAction { sheet = true }
                Spacer(minLength: 4)
                if let action {
                    Button {
                        Task { await model.run(e.id, action) }
                    } label: {
                        HStack(spacing: 6) {
                            if DeviceLogic.needsAuthentication(action, biometricsEnabled: model.biometricsEnabled) {
                                Image(systemName: "faceid").font(.system(size: 14, weight: .semibold)).accessibilityHidden(true)
                            }
                            Text(action.label)
                        }
                        .frame(minWidth: 72)
                    }
                    .buttonStyle(PillButtonStyle())
                    .disabled(!e.isAvailable || model.busy.contains(e.id))
                    .accessibilityLabel("\(action.label) \(e.name)")
                    .accessibilityHint(DeviceLogic.needsAuthentication(action, biometricsEnabled: model.biometricsEnabled)
                                       ? "Asks for \(DeviceAuth.methodName) first" : "")
                }
                if !extras.isEmpty {
                    Button { sheet = true } label: { Image(systemName: "ellipsis").frame(width: 20) }
                        .buttonStyle(PillButtonStyle())
                        .accessibilityLabel("More for \(e.name)")
                }
            }
            .frame(minHeight: 66)
            .contentShape(Rectangle())
            .onTapGesture { sheet = true }
            .sheet(isPresented: $sheet) { DeviceSheet(entityId: e.id).environmentObject(model) }
        }
    }
}
