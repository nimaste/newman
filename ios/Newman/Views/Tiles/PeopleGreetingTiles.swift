import SwiftUI

/// Where everyone is - the People tab's rows (initial, name, "Home" / a place / how far, when),
/// fitted to the tile (PeopleTileLogic): fewer rows and "+N more" (opens the People tab) when they
/// don't all fit, one line when it's too short for rows. Tap someone sharing to see them on the map
/// (the tile's map look) - the tap also asks their phone for one fresh location. config.people picks who's shown. Same data as the People tab
/// (model.people, kept current by live "person" messages); only loaded here when nothing has yet.
struct PeopleTileView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.tileScale) var scale
    let tile: Tile
    /// The tile's cell (placed on a phone layout); nil on a flowed page (everyone shown).
    var size: CGSize? = nil
    @State private var following: Person?
    @State private var now = Date().timeIntervalSince1970

    private let pad: CGFloat = 14

    var body: some View {
        let list = PeopleTileLogic.select(model.people, pick: tile.strings("people"))
        let iw = size.map { Double(max(0, $0.width - 2 * pad)) } ?? 320
        let ih = size.map { Double(max(0, $0.height - 2 * pad)) } ?? Double.infinity
        let lay = PeopleTileLogic.layout(count: list.count, width: iw, height: ih)
        return content(list, lay, ih)
            .frame(maxWidth: .infinity, alignment: .topLeading)
            .glassCard(padding: pad)
            .task {
                if model.peopleStatus == .loading { await model.reloadPeople() }
            }
            .task {
                // Keep "updated 4 min ago" true.
                while !Task.isCancelled {
                    try? await Task.sleep(nanoseconds: 30_000_000_000)
                    now = Date().timeIntervalSince1970
                }
            }
            .fullScreenCover(item: $following) { p in
                PersonMapView(personId: p.id, light: tile.string("mapLook") != "dark").environmentObject(model)
            }
    }

    private var message: String? {
        if model.peopleStatus == .notSetUp { return "Location sharing isn't set up on your hub yet." }
        if !PeopleTileLogic.select(model.people, pick: tile.strings("people")).isEmpty { return nil }
        switch model.peopleStatus {
        case .loading: return "Loading…"
        case .unreachable: return "Can't reach Newman right now."
        default: return "Nobody to show."
        }
    }

    @ViewBuilder
    private func content(_ list: [Person], _ lay: PeopleTileLogic.Layout, _ ih: Double) -> some View {
        if let msg = message {
            let titled = ih >= PeopleTileLogic.head + PeopleTileLogic.headGap + TileFit.line
            let room = titled ? ih - PeopleTileLogic.head - PeopleTileLogic.headGap : ih
            VStack(alignment: .leading, spacing: CGFloat(PeopleTileLogic.headGap)) {
                if titled { openTab { head(nil) } }
                Text(msg).font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted)
                    .lineLimit(room == .infinity ? 3 : max(1, Int(room / TileFit.line)))
            }
        } else if lay.look == .oneLine {
            openTab {
                VStack(alignment: .leading, spacing: 0) {
                    head(nil)
                    Text(PeopleTileLogic.oneLine(list)).font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted).lineLimit(1)
                }
            }
        } else {
            VStack(alignment: .leading, spacing: 0) {
                if lay.head {
                    openTab { head(lay.moreInHead ? "+\(lay.more) more" : nil) }
                        .padding(.bottom, CGFloat(PeopleTileLogic.headGap))
                }
                VStack(alignment: .leading, spacing: CGFloat(PeopleTileLogic.gap)) {
                    ForEach(Array(list.prefix(lay.shown))) { p in
                        row(p, avatar: lay.avatars)
                    }
                }
                if lay.moreBelow {
                    openTab {
                        Text("+\(lay.more) more").font(AppFont.manrope(13, .heavy)).foregroundColor(Theme.muted).lineLimit(1)
                            .frame(maxWidth: .infinity, minHeight: CGFloat(TileFit.line), maxHeight: CGFloat(TileFit.line), alignment: .leading)
                    }
                }
            }
        }
    }

    /// Opens the People tab.
    private func openTab<Content: View>(@ViewBuilder _ label: () -> Content) -> some View {
        Button { model.selectedTab = "people" } label: { label().contentShape(Rectangle()) }
            .buttonStyle(.plain)
            .accessibilityHint("Opens the People tab")
    }

    private func head(_ note: String?) -> some View {
        HStack(spacing: 6) {
            Text("People").font(AppFont.manrope(15 * scale, .heavy)).foregroundColor(Theme.text).lineLimit(1)
            Spacer(minLength: 4)
            if let note { Text(note).font(AppFont.manrope(13, .heavy)).foregroundColor(Theme.muted).lineLimit(1) }
        }
        .frame(height: CGFloat(PeopleTileLogic.head))
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }

    /// One person, as on the People tab but one row high; only someone with a location opens the map.
    private func row(_ p: Person, avatar: Bool) -> some View {
        let r = PeopleText.row(p, now: now)
        let i = model.people.firstIndex { $0.id == p.id } ?? 0
        return Button {
            if r.located {
                model.refreshPerson(p.id)
                following = p
            }
        } label: {
            HStack(spacing: 10) {
                if avatar {
                    PersonAvatar(person: p, index: i, size: 36, dimmed: !p.sharing)
                }
                VStack(alignment: .leading, spacing: 1) {
                    Text(r.name).font(AppFont.manrope(15 * scale, .heavy)).foregroundColor(p.sharing ? Theme.text : Theme.muted).lineLimit(1)
                        .minimumScaleFactor(0.7)
                    Text([r.whereText, r.updated].compactMap { $0 }.joined(separator: " · "))
                        .font(AppFont.manrope(12 * scale, .bold))
                        .foregroundColor(r.home ? Theme.teal : (p.sharing ? Theme.text.opacity(0.85) : Theme.muted))
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                }
                Spacer(minLength: 0)
            }
            .frame(height: CGFloat(PeopleTileLogic.row))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!r.located)
        .accessibilityLabel(r.name)
        .accessibilityValue([r.whereText, r.updated, p.sharing ? nil : "not sharing"].compactMap { $0 }.joined(separator: ", "))
        .accessibilityHint(r.located ? "Shows them on a map" : "")
    }
}

/// "Good morning" and today in one line ("3 things to do · 2 events today · rain from 3 PM"), from
/// the Today card's briefing. The greeting is sized to the tile; when the line doesn't fit under it,
/// both go on one line.
struct GreetingTileView: View {
    @EnvironmentObject var model: AppModel
    /// The tile's cell (placed on a phone layout); nil on a flowed page.
    var size: CGSize? = nil
    @State private var now = Date()

    private let pad: CGFloat = 14

    var body: some View {
        let ih = size.map { Double(max(0, $0.height - 2 * pad)) } ?? Double.infinity
        let fit = TodayView.greetingFit(height: ih)
        let hello = TodayView.greeting(hour: Calendar.current.component(.hour, from: now))
        let line = model.today.map { TodayView.greetingLine($0, done: model.todayDone, now: now.timeIntervalSince1970) }
        return VStack(alignment: .leading, spacing: 0) {
            if let line, !fit.twoLines {
                (Text(hello).font(AppFont.sora(CGFloat(fit.size))).foregroundColor(Theme.text)
                    + Text("  ·  " + line).font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted))
                    .lineLimit(1)
            } else {
                Text(hello).font(AppFont.sora(CGFloat(fit.size))).foregroundColor(Theme.text).lineLimit(1)
                if let line { Text(line).font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted).lineLimit(1) }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: size == nil ? nil : .infinity, alignment: .leading)
        .glassCard(padding: pad)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
        .task {
            // Like the Today card: fresh on show, then every minute (device changes reload it too).
            while !Task.isCancelled {
                await model.reloadToday()
                now = Date()
                try? await Task.sleep(nanoseconds: 60_000_000_000)
            }
        }
    }
}
