import SwiftUI

/// The Alerts tab (PhoneAlerts board): the hub's activity (GET /api/events) by day, camera
/// alerts (GET /api/alerts) folded in, filter chips from the kinds of entries actually there.
struct AlertsScreen: View {
    @EnvironmentObject var model: AppModel
    @State private var filter = "all"

    struct Item: Identifiable, Hashable {
        var id: String
        var ts: Double
        var kind: String
        var title: String
        var body: String
        var entityId: String?
        var thumb: String?
        var alert: CameraAlert?
    }

    private var items: [Item] {
        var out = model.events.map { ev in
            Item(id: "e:" + ev.id, ts: ev.ts, kind: ev.kind, title: ev.title, body: ev.body ?? "", entityId: ev.entityId,
                 thumb: ev.thumbPath ?? ev.snapshotPath,
                 alert: ev.reviewId.flatMap { rid in model.cameraAlerts.first { $0.id == rid } })
        }
        let known = Set(model.events.compactMap { $0.reviewId })
        for a in model.cameraAlerts where !known.contains(a.id) {
            out.append(Item(id: "a:" + a.id, ts: a.start ?? 0, kind: "alert", title: a.title ?? "Alert",
                            body: (a.zones ?? []).joined(separator: ", "), entityId: a.entityId, thumb: a.thumbUrl ?? a.snapshotUrl, alert: a))
        }
        return out.sorted { $0.ts > $1.ts }
    }

    private var kindsPresent: [String] {
        var seen: [String] = []
        for i in items where !seen.contains(i.kind) { seen.append(i.kind) }
        return seen
    }

    var body: some View {
        let all = items
        let shown = filter == "all" ? all : all.filter { $0.kind == filter }
        let grouped = Dictionary(grouping: shown) { HubDates.dayKey(Date(timeIntervalSince1970: $0.ts)) }
        let days = grouped.keys.sorted(by: >)
        return ZStack {
            NightGlassBackground(teal: false)
            VStack(alignment: .leading, spacing: 10) {
                Text("Alerts").font(AppFont.sora(28)).foregroundColor(Theme.text).padding(.horizontal, 18).padding(.top, 12)
                    .accessibilityAddTraits(.isHeader)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        chip("all", "All")
                        ForEach(kindsPresent, id: \.self) { k in chip(k, DeviceLogic.humanize(k)) }
                    }
                    .padding(.horizontal, 16)
                }
                ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 8) {
                        if shown.isEmpty {
                            Text("Nothing here yet.").font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted).padding(.top, 30)
                        }
                        ForEach(days, id: \.self) { day in
                            let list = grouped[day] ?? []
                            Text(HubDates.dayLabel(Date(timeIntervalSince1970: list.first?.ts ?? 0)).uppercased())
                                .font(AppFont.manrope(12, .heavy)).kerning(1).foregroundColor(Theme.muted).padding(.top, 6)
                                .accessibilityLabel(HubDates.dayLabel(Date(timeIntervalSince1970: list.first?.ts ?? 0)))
                                .accessibilityAddTraits(.isHeader)
                            ForEach(list) { item in row(item) }
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.bottom, 20)
                }
                .refreshable { await model.reloadActivity() }
                .onAppear { scrollToHighlight(proxy) }
                .onChange(of: model.highlightedAlert) { _, _ in scrollToHighlight(proxy) }
                }
            }
        }
        .onAppear { model.markAlertsSeen() }
        .onDisappear { model.highlightedAlert = nil }
    }

    /// A tapped notification's entry: shown (all kinds) and scrolled to.
    private func scrollToHighlight(_ proxy: ScrollViewProxy) {
        guard let id = model.highlightedAlert else { return }
        filter = "all"
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
            withAnimation(.easeOut(duration: 0.25)) { proxy.scrollTo(id, anchor: .center) }
        }
    }

    private func chip(_ id: String, _ label: String) -> some View {
        Button(label) { filter = id }
            .buttonStyle(PillButtonStyle(selected: filter == id, height: 36))
            .accessibilityHint("Shows only these")
    }

    private func row(_ item: Item) -> some View {
        HStack(alignment: .top, spacing: 12) {
            if let thumb = item.thumb {
                Button {
                    if let eid = item.entityId {
                        model.openCamera = AppModel.CameraTarget(entityId: eid, cameraIds: [eid], alert: item.alert)
                    }
                } label: {
                    ZStack {
                        RemoteImage(url: model.url(thumb)) {
                            LinearGradient(colors: [Color(hex: 0x2B3758), Color(hex: 0x161E38)], startPoint: .topLeading, endPoint: .bottomTrailing)
                        }
                        Image(systemName: "play.fill").font(.system(size: 14)).foregroundColor(Color(hex: 0xC9D4F5))
                    }
                    .frame(width: 84, height: 62)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Snapshot: \(item.title)")
                .accessibilityHint("Shows it on the camera")
            } else {
                Image(systemName: icon(for: item))
                    .font(.system(size: 17, weight: .medium))
                    .frame(width: 40, height: 40)
                    .background(Circle().fill(Color.white.opacity(0.08)))
                    .foregroundColor(Theme.text)
                    .accessibilityHidden(true)
            }
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline) {
                    Text(item.title).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                    Spacer(minLength: 8)
                    Text(HubDates.time(Date(timeIntervalSince1970: item.ts))).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                }
                if !item.body.isEmpty {
                    Text(item.body).font(AppFont.manrope(13, .semibold)).foregroundColor(Color(hex: 0xC9D2EE))
                }
            }
            // Title, time and details read as one entry.
            .accessibilityElement(children: .combine)
            .accessibilitySortPriority(1) // read before the snapshot button
        }
        .accessibilityElement(children: .contain)
        .glassCard(radius: 18, padding: 12)
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous)
            .stroke(Theme.blue, lineWidth: item.id == model.highlightedAlert ? 2 : 0))
        .id(item.id)
    }

    /// The device's own icon when the entry is about one; otherwise a bell.
    private func icon(for item: Item) -> String {
        if let id = item.entityId, let e = model.entities[id] { return Symbols.name(DeviceLogic.kindIcon(model.kinds[e.kind])) }
        return "bell"
    }
}
