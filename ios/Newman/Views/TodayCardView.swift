import SwiftUI

/// The Today card (the wall's Today page, phone-sized): To do with Done / Remind tomorrow / Open
/// right there, today's events and heads-up. Calm: a few rows, the rest behind "N more".
/// `showWhenEmpty`: the dashboard has a Today page, so say "Nothing needs doing today" rather than
/// hiding.
struct TodayCardView: View {
    @EnvironmentObject var model: AppModel
    let showWhenEmpty: Bool
    @State private var expanded = false
    @State private var busy: String?
    @State private var sheetFor: SheetTarget?

    private struct SheetTarget: Identifiable { var id: String }

    var body: some View {
        content
            .task {
                // Fresh on show, then every minute while it's on screen.
                while !Task.isCancelled {
                    await model.reloadToday()
                    try? await Task.sleep(nanoseconds: 60_000_000_000)
                }
            }
            .sheet(item: $sheetFor) { t in DeviceSheet(entityId: t.id).environmentObject(model) }
    }

    @ViewBuilder private var content: some View {
        if let b = model.today {
            let card = TodayView.card(b, done: model.todayDone, now: Date().timeIntervalSince1970, rows: expanded ? 99 : TodayView.rows)
            if card.empty {
                if showWhenEmpty {
                    Label("Nothing needs doing today.", systemImage: "checkmark")
                        .font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .glassCard(padding: 14)
                }
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    if !card.rows.isEmpty {
                        caps("To do")
                        ForEach(Array(card.rows.enumerated()), id: \.element.id) { i, r in
                            row(r)
                            if i < card.rows.count - 1 { Divider().overlay(Theme.divider) }
                        }
                        if card.more > 0 {
                            Button("\(card.more) more") { expanded = true }
                                .font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted).padding(.vertical, 10)
                        } else if expanded && card.rows.count > TodayView.rows {
                            Button("Show less") { expanded = false }
                                .font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted).padding(.vertical, 10)
                        }
                    }
                    if !card.events.isEmpty {
                        caps("On today").padding(.top, card.rows.isEmpty ? 0 : 10)
                        ForEach(Array(card.events.enumerated()), id: \.offset) { _, ev in
                            HStack(spacing: 10) {
                                Text(ev.now == true ? "Now" : (ev.time ?? ""))
                                    .font(AppFont.manrope(13, .bold)).foregroundColor(ev.now == true ? Theme.teal : Theme.muted)
                                    .frame(width: 64, alignment: .leading).lineLimit(1).minimumScaleFactor(0.6)
                                RoundedRectangle(cornerRadius: 2).fill(Color(hexString: ev.colour ?? "") ?? Theme.teal).frame(width: 3, height: 30)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(ev.title ?? "Busy").font(AppFont.manrope(14, .heavy)).foregroundColor(Theme.text).lineLimit(1)
                                    if let loc = ev.location, !loc.isEmpty {
                                        Text(loc).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted).lineLimit(1)
                                    }
                                }
                                Spacer(minLength: 0)
                            }
                            .padding(.vertical, 5)
                            .accessibilityElement(children: .combine)
                        }
                    }
                    if !card.headsUp.isEmpty {
                        VStack(alignment: .leading, spacing: 6) {
                            ForEach(card.headsUp) { h in
                                HStack(spacing: 10) {
                                    Image(systemName: headsSymbol(h.icon)).font(.system(size: 15, weight: .bold)).foregroundColor(headsColor(h.icon))
                                        .accessibilityHidden(true)
                                    Text(h.text).font(AppFont.manrope(14, .semibold)).foregroundColor(Theme.text).lineLimit(2)
                                }
                            }
                        }
                        .padding(.top, 8)
                    }
                }
                .glassCard(padding: 14)
            }
        }
    }

    private func caps(_ s: String) -> some View {
        Text(s.uppercased()).font(AppFont.manrope(12, .heavy)).kerning(1).foregroundColor(Theme.muted).padding(.bottom, 4)
            .accessibilityLabel(s) // not spelled out letter by letter
            .accessibilityAddTraits(.isHeader)
    }

    private func row(_ r: TodayView.Row) -> some View {
        let done = r.tone == .done
        return HStack(spacing: 10) {
            RoundedRectangle(cornerRadius: 2).fill(toneColor(r.tone)).frame(width: 4, height: 34)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(r.title).font(AppFont.manrope(14, .heavy)).foregroundColor(done ? Theme.muted : Theme.text)
                    .strikethrough(done).lineLimit(2)
                if !r.status.isEmpty {
                    Text(r.status).font(AppFont.manrope(12, .bold)).foregroundColor(r.tone == .later ? Theme.muted : toneColor(r.tone)).lineLimit(2)
                }
            }
            .accessibilityElement(children: .combine)
            .accessibilityValue(done ? "Done" : "")
            Spacer(minLength: 4)
            ForEach(r.buttons, id: \.self) { b in
                button(b, r).accessibilityHint(r.title) // which to-do the button acts on
            }
        }
        .padding(.vertical, 8)
    }

    @ViewBuilder private func button(_ b: TodayView.Button, _ r: TodayView.Row) -> some View {
        let isBusy = busy == r.id
        switch b {
        case .done:
            Button("Done") { act(r.id) { await model.todayDo(r.item) } }
                .buttonStyle(PillButtonStyle(height: 36, prominent: true)).disabled(isBusy)
        case .dismiss:
            Button("Done") { act(r.id) { await model.todayDo(r.item) } }
                .buttonStyle(PillButtonStyle(height: 36)).disabled(isBusy)
        case .undo:
            Button("Undo") { act(r.id) { await model.todayUndo(r.item) } }
                .buttonStyle(PillButtonStyle(height: 36)).disabled(isBusy)
        case .open:
            Button("Open") { if let e = r.entity { sheetFor = SheetTarget(id: e) } }
                .buttonStyle(PillButtonStyle(height: 36))
        case .snooze:
            Button { act(r.id) { await model.todaySnooze(r.item) } } label: {
                Image(systemName: "clock").font(.system(size: 15, weight: .semibold)).foregroundColor(Theme.muted)
                    .frame(width: 36, height: 36)
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.controlBorder))
            }
            .buttonStyle(.plain)
            .disabled(isBusy)
            .accessibilityLabel("Remind me tomorrow")
        }
    }
    private func act(_ id: String, _ work: @escaping () async -> Bool) {
        busy = id
        Task {
            _ = await work()
            busy = nil
        }
    }

    private func toneColor(_ t: TodayView.Tone) -> Color {
        switch t {
        case .urgent: return Theme.coral
        case .due: return Theme.amber
        case .done: return Theme.teal
        case .later: return Theme.text.opacity(0.35)
        }
    }

    private func headsSymbol(_ icon: String?) -> String {
        switch icon {
        case "warning": return "exclamationmark.triangle"
        case "rain": return "cloud.rain"
        case "calendar": return "calendar"
        default: return "clock"
        }
    }

    private func headsColor(_ icon: String?) -> Color {
        switch icon {
        case "warning": return Theme.coral
        case "rain": return Theme.blue
        default: return Theme.amber
        }
    }
}
