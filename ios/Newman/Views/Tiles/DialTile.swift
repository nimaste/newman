import SwiftUI

/// The round thermostat dial (PhoneOverview / PhoneClimate boards) for any "number" command, with
/// the mode chips of the tile's "state" command. Small tiles show the compact dial and open the
/// big one in a sheet; wide tiles (a Climate page) show it full size, draggable.
struct DialTile: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.tileScale) var scale
    let tile: Tile
    let wide: Bool
    @State private var sheet = false
    @State private var page: DashboardPage?

    var body: some View {
        let e = model.entity(tile.string("entity") ?? "")
        Group {
            if let dial = DialModel(entity: e, kind: model.kind(of: e), tile: tile) {
                if wide {
                    DialControl(dial: dial, size: 300)
                } else if acrossThePhone {
                    DialRow(dial: dial) { openBig(dial) }
                        .sheet(isPresented: $sheet) {
                            DialSheet(entityId: dial.entity.id, tile: tile).environmentObject(model)
                        }
                        .fullScreenCover(item: $page) { p in
                            PageScreen(page: p, isFirst: false, onClose: { page = nil }).environmentObject(model)
                                .fullDynamicType()
                        }
                } else {
                    Button {
                        openBig(dial)
                    } label: { compact(dial) }.buttonStyle(.plain)
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel("\(dial.entity.name), \(dial.command.label.lowercased())")
                        .accessibilityValue([dial.text(dial.current), dial.mode.map { DeviceLogic.stateLabel(dial.kind, $0) },
                                             dial.nowProperty.map { "now " + DeviceLogic.formatProp($0, $0.value) }]
                            .compactMap { $0 }.joined(separator: ", "))
                        .accessibilityAddTraits(.isButton)
                        .accessibilityHint("Opens the thermostat")
                        .accessibilityAction { openBig(dial) }
                        .sheet(isPresented: $sheet) {
                            DialSheet(entityId: dial.entity.id, tile: tile).environmentObject(model)
                        }
                        .fullScreenCover(item: $page) { p in
                            PageScreen(page: p, isFirst: false, onClose: { page = nil }).environmentObject(model)
                                .fullDynamicType()
                        }
                }
            } else {
                Text("This dial's device is missing.").font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted)
                    .glassCard()
            }
        }
    }

    /// Placed across the whole phone but shorter than the big dial (4 rows): the dial beside its controls.
    private var acrossThePhone: Bool {
        model.phoneLayout != nil && PhoneGrid.clamp(tile).w >= PhoneGrid.columns
    }

    /// The thermostat's own page (Climate) when it has no tab; else the big dial.
    private func openBig(_ dial: DialModel) {
        if let p = model.pageForDial(dial.entity.id), !(p.tiles ?? []).contains(where: { $0.id == tile.id }) {
            page = p
        } else {
            sheet = true
        }
    }

    private func compact(_ dial: DialModel) -> some View {
        let color = Color(hex: dial.colorHex)
        return VStack(spacing: 4) {
            HStack {
                Text(dial.entity.name).font(AppFont.manrope(13 * scale, .heavy)).foregroundColor(Theme.text).lineLimit(1)
                    .minimumScaleFactor(0.7)
                Spacer()
                Text(dial.mode.map { DeviceLogic.stateLabel(dial.kind, $0) } ?? "").font(AppFont.manrope(13 * scale, .heavy)).foregroundColor(color)
                    .lineLimit(1).minimumScaleFactor(0.7)
            }
            ZStack {
                DialRing(dial: dial, value: dial.current, lineWidth: 16, knob: 13)
                VStack(spacing: 0) {
                    Text(dial.text(dial.current)).font(AppFont.sora(30 * scale)).foregroundColor(Theme.text)
                        .lineLimit(1).minimumScaleFactor(0.5)
                    if let n = dial.nowProperty {
                        Text("Now " + DeviceLogic.formatProp(n, n.value).replacingOccurrences(of: " °C", with: "°").replacingOccurrences(of: " °F", with: "°"))
                            .font(AppFont.manrope(11 * scale, .bold)).foregroundColor(Theme.muted)
                            .lineLimit(1).minimumScaleFactor(0.6)
                    }
                }
                .padding(.top, 8)
                .padding(.horizontal, 18)
            }
            .frame(width: 130, height: 124)
        }
        .glassCard()
    }
}

/// The thermostat across the phone's width, a few rows high (Overview): its name and what it's doing
/// on top; the dial on the left (tap for the big one); − / + and the modes (the device's own) on the right.
struct DialRow: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.tileScale) var scale
    let dial: DialModel
    let open: () -> Void
    @State private var target: Double?
    @State private var pending: Task<Void, Never>?

    var body: some View {
        let color = Color(hex: dial.colorHex)
        VStack(alignment: .leading, spacing: 10) {
            CardHeader(text: dial.entity.name) {
                if !dial.status.isEmpty {
                    Text(dial.status)
                        .font(AppFont.manrope(12 * scale, .heavy)).foregroundColor(color)
                        .lineLimit(1).minimumScaleFactor(0.7)
                        .padding(.horizontal, 10)
                        .frame(minHeight: 24)
                        .background(Capsule().fill(color.opacity(0.18)))
                }
            }
            GeometryReader { g in
                HStack(spacing: 16) {
                    ring(size: max(84, min(140, g.size.height)))
                    controls
                }
                .frame(width: g.size.width, height: g.size.height)
            }
        }
        .glassCard(padding: 14)
        .onChange(of: dial.current) {
            if pending == nil { target = nil }
        }
    }

    private func ring(size: CGFloat) -> some View {
        let shown = target ?? dial.current
        return Button(action: open) {
            ZStack {
                DialRing(dial: dial, value: shown, lineWidth: size > 110 ? 13 : 10, knob: size > 110 ? 10 : 8, nowValue: dial.nowValue)
                VStack(spacing: 0) {
                    Text(dial.text(shown)).font(AppFont.sora(size * 0.19 * scale)).foregroundColor(Theme.text)
                        .lineLimit(1).minimumScaleFactor(0.5)
                    if let n = dial.nowProperty {
                        Text("Now " + DeviceLogic.formatProp(n, n.value).replacingOccurrences(of: " °C", with: "°").replacingOccurrences(of: " °F", with: "°"))
                            .font(AppFont.manrope(max(9, size * 0.08) * scale, .bold)).foregroundColor(Theme.muted)
                            .lineLimit(1).minimumScaleFactor(0.6)
                    }
                }
                .padding(.horizontal, size * 0.2)
            }
            .frame(width: size, height: size)
            .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(dial.entity.name), \(dial.command.label.lowercased())")
        .accessibilityValue([dial.text(shown), dial.mode.map { DeviceLogic.stateLabel(dial.kind, $0) },
                             dial.nowProperty.map { "now " + DeviceLogic.formatProp($0, $0.value) }]
            .compactMap { $0 }.joined(separator: ", "))
        .accessibilityAddTraits(.isButton)
        .accessibilityHint("Opens the thermostat")
        .accessibilityAdjustableAction { dir in bump(dir == .increment ? dial.step : -dial.step) }
    }

    private var controls: some View {
        VStack(spacing: 10) {
            HStack(spacing: 10) {
                stepButton("minus") { bump(-dial.step) }
                    .accessibilityLabel("Lower \(dial.command.label.lowercased())")
                stepButton("plus") { bump(dial.step) }
                    .accessibilityLabel("Raise \(dial.command.label.lowercased())")
            }
            .disabled(dial.isOff)
            .opacity(dial.isOff ? 0.5 : 1)
            if let modeCmd = dial.modeCommand, !dial.modes.isEmpty {
                HStack(spacing: 4) {
                    ForEach(dial.modes, id: \.id) { m in
                        let on = m.id == dial.mode
                        let c = Color(hex: DialModel.modeColors[m.id] ?? DialModel.neutral)
                        Button {
                            if !on { Task { await model.run(dial.entity.id, modeCmd, value: .string(m.id)) } }
                        } label: {
                            Text(m.label).font(AppFont.manrope(13 * scale, .heavy)).lineLimit(1).minimumScaleFactor(0.6)
                                .frame(maxWidth: .infinity, minHeight: 34)
                                .foregroundColor(on ? Theme.background : Theme.muted)
                                .background(RoundedRectangle(cornerRadius: 10).fill(on ? c : Color.clear))
                        }
                        .buttonStyle(.plain)
                        .accessibilityAddTraits(on ? .isSelected : [])
                    }
                }
                .padding(4)
                .background(RoundedRectangle(cornerRadius: 14).fill(Theme.pill))
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func stepButton(_ symbol: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 20, weight: .semibold))
                .frame(maxWidth: .infinity, minHeight: 44)
                .foregroundColor(Theme.text)
                .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Theme.control))
                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Theme.controlBorder))
        }
        .buttonStyle(.plain)
    }

    private func bump(_ d: Double) {
        guard !dial.isOff, let base = target ?? dial.current else { return }
        let v = dial.clamp(base + d)
        target = v
        pending?.cancel()
        pending = Task {
            try? await Task.sleep(nanoseconds: 600_000_000)
            guard !Task.isCancelled else { return }
            pending = nil
            if v != dial.current { await model.run(dial.entity.id, dial.command, value: .number(v)) }
        }
    }
}

struct DialSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let entityId: String
    let tile: Tile

    var body: some View {
        ZStack {
            NightGlassBackground(teal: false)
            VStack(spacing: 14) {
                HStack {
                    Spacer()
                    Button("Done") { dismiss() }.buttonStyle(PillButtonStyle())
                }
                if let e = model.entity(entityId), let dial = DialModel(entity: e, kind: model.kind(of: e), tile: tile) {
                    DialControl(dial: dial, size: 300)
                }
                Spacer()
            }
            .padding(16)
        }
        .presentationDetents([.large])
        .presentationBackground(Theme.background)
        .fullDynamicType()
    }
}

/// The big dial: name and status, a ring you drag around (sent ~600 ms after you let go),
/// − / + buttons and the mode chips.
struct DialControl: View {
    @EnvironmentObject var model: AppModel
    let dial: DialModel
    var size: CGFloat
    @State private var target: Double?
    @State private var dragging = false
    @State private var pending: Task<Void, Never>?

    var body: some View {
        let color = Color(hex: dial.colorHex)
        let shown = target ?? dial.current
        VStack(spacing: 14) {
            HStack {
                Text(dial.entity.name).font(AppFont.sora(22)).foregroundColor(Theme.text)
                Spacer()
                Text(dial.status).font(AppFont.manrope(13, .heavy)).foregroundColor(color)
            }
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isHeader)
            ZStack {
                DialRing(dial: dial, value: shown, lineWidth: 26, knob: 19, nowValue: dial.nowValue)
                VStack(spacing: 2) {
                    Text(dial.word.uppercased()).font(AppFont.manrope(12, .heavy)).kerning(1.5).foregroundColor(color)
                        .lineLimit(1).minimumScaleFactor(0.6)
                    Text(dial.text(shown)).font(AppFont.sora(64)).foregroundColor(Theme.text).lineLimit(1).minimumScaleFactor(0.5)
                    Text(dial.nowLine(compact: false)).font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted)
                        .lineLimit(1).minimumScaleFactor(0.6)
                }
                .padding(60)
                .allowsHitTesting(false)
                // The ring has a fixed size: its text grows only so far.
                .cappedDynamicType(.xxxLarge)
            }
            .frame(width: size, height: size)
            .contentShape(Circle())
            .gesture(DragGesture(minimumDistance: 0)
                .onChanged { g in
                    guard !dial.isOff else { return }
                    dragging = true
                    pending?.cancel()
                    target = value(at: g.location)
                }
                .onEnded { g in
                    guard !dial.isOff else { return }
                    dragging = false
                    commit(value(at: g.location))
                })
            .accessibilityElement()
            .accessibilityLabel("\(dial.command.label) for \(dial.entity.name)")
            .accessibilityValue([dial.text(shown), dial.nowLine(compact: false)].filter { !$0.isEmpty }.joined(separator: ", "))
            .accessibilityHint(dial.isOff ? "Off. Choose a mode to change it" : "Swipe up or down to adjust")
            .accessibilityAdjustableAction { dir in bump(dir == .increment ? dial.step : -dial.step) }

            HStack(spacing: 40) {
                round("minus") { bump(-dial.step) }
                    .accessibilityLabel("Lower \(dial.command.label.lowercased())")
                round("plus") { bump(dial.step) }
                    .accessibilityLabel("Raise \(dial.command.label.lowercased())")
            }
            .disabled(dial.isOff)

            if let modeCmd = dial.modeCommand, !dial.modes.isEmpty {
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 6), count: min(4, dial.modes.count)), spacing: 6) {
                    ForEach(dial.modes, id: \.id) { m in
                        let on = m.id == dial.mode
                        let c = Color(hex: DialModel.modeColors[m.id] ?? DialModel.neutral)
                        Button {
                            if !on { Task { await model.run(dial.entity.id, modeCmd, value: .string(m.id)) } }
                        } label: {
                            Text(m.label).font(AppFont.manrope(14, .heavy)).lineLimit(1).minimumScaleFactor(0.8)
                                .frame(maxWidth: .infinity, minHeight: 48)
                                .foregroundColor(on ? Theme.background : Theme.text)
                                .background(RoundedRectangle(cornerRadius: 14).fill(on ? c : Color.clear))
                        }
                        .buttonStyle(.plain)
                        .accessibilityAddTraits(on ? .isSelected : [])
                    }
                }
                .padding(5)
                .background(RoundedRectangle(cornerRadius: 18).fill(Theme.pill))
            }
        }
        .glassCard(padding: 14)
        .onChange(of: dial.current) {
            if !dragging && pending == nil { target = nil }
        }
    }

    private func value(at p: CGPoint) -> Double {
        let c = CGPoint(x: size / 2, y: size / 2)
        let deg = atan2(p.y - c.y, p.x - c.x) * 180 / .pi
        return dial.value(atDegrees: deg)
    }

    private func bump(_ d: Double) {
        guard !dial.isOff, let base = target ?? dial.current else { return }
        let v = dial.clamp(base + d)
        target = v
        commit(v)
    }

    private func commit(_ v: Double) {
        target = v
        pending?.cancel()
        pending = Task {
            try? await Task.sleep(nanoseconds: 600_000_000)
            guard !Task.isCancelled else { return }
            pending = nil
            if v != dial.current { await model.run(dial.entity.id, dial.command, value: .number(v)) }
        }
    }

    private func round(_ symbol: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 26, weight: .semibold))
                .frame(width: 64, height: 64)
                .foregroundColor(Theme.text)
                .background(Circle().fill(Color.white.opacity(0.06)))
                .overlay(Circle().stroke(Color.white.opacity(0.16)))
        }
    }
}

/// The ring: a 270° track from 7:30 to 4:30, filled in the mode's colour up to the value, a knob,
/// and a small dot for the current reading.
struct DialRing: View {
    let dial: DialModel
    let value: Double?
    var lineWidth: CGFloat
    var knob: CGFloat
    var nowValue: Double? = nil

    var body: some View {
        GeometryReader { geo in
            let side = min(geo.size.width, geo.size.height)
            let r = side / 2 - lineWidth / 2 - 2
            let center = CGPoint(x: geo.size.width / 2, y: geo.size.height / 2)
            let color = Color(hex: dial.colorHex)
            ZStack {
                arc(center, r, 135, 405).stroke(Color.white.opacity(0.10), style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                if let v = value, !dial.isOff {
                    let end = max(135.1, dial.angle(for: v))
                    arc(center, r, 135, end).stroke(color, style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                    if let n = nowValue {
                        Circle().fill(Theme.text.opacity(0.7)).frame(width: 10, height: 10).position(point(center, r, dial.angle(for: n)))
                    }
                    Circle().fill(Theme.text).frame(width: knob * 2, height: knob * 2).position(point(center, r, end))
                }
            }
        }
    }

    private func point(_ c: CGPoint, _ r: CGFloat, _ deg: Double) -> CGPoint {
        let a = deg * .pi / 180
        return CGPoint(x: c.x + r * CGFloat(cos(a)), y: c.y + r * CGFloat(sin(a)))
    }

    private func arc(_ c: CGPoint, _ r: CGFloat, _ a0: Double, _ a1: Double) -> Path {
        var p = Path()
        p.addArc(center: c, radius: r, startAngle: .degrees(a0), endAngle: .degrees(a1), clockwise: false)
        return p
    }
}
