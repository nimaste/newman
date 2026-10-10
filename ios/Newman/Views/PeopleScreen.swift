import SwiftUI

/// A person's round avatar, everywhere they appear: their photo (fetched through the mTLS session,
/// cached by its ?v= URL, which changes with the picture) or their single initial on their colour.
/// `index` (their place in the household) only picks a colour on older hubs that send none.
struct PersonAvatar: View {
    let person: Person?
    var index: Int = 0
    var size: CGFloat = 46
    var dimmed = false

    var body: some View {
        ZStack {
            Circle().fill(Color(hex: PersonLook.rgb(person ?? Person(id: ""), index: index)))
            Text(PeopleText.initial(person?.name ?? ""))
                .font(AppFont.sora(size * 0.42))
                .foregroundColor(Theme.background)
            if let url = photoURL {
                RemoteImage(url: url, placeholder: { Color.clear })
                    .frame(width: size, height: size)
                    .clipShape(Circle())
            }
        }
        .frame(width: size, height: size)
        .opacity(dimmed ? 0.45 : 1)
        // A picture of the person: their name (rows that show the name too override it).
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(person?.name ?? "")
        .accessibilityAddTraits(.isImage)
    }

    private var photoURL: URL? {
        guard let path = PersonLook.photoPath(person), let base = ConnectionStore.shared.serverURL else { return nil }
        return ServerAddress.url(base, path)
    }
}

/// "Updating…" with a spinner, or why it didn't (tap-to-refresh).
struct RefreshNote: View {
    let state: PersonRefresh.State
    let now: Double

    var body: some View {
        if let text = PersonRefresh.text(state, now: now) {
            HStack(spacing: 6) {
                if state.phase == .updating { ProgressView().scaleEffect(0.7).tint(Theme.muted).frame(width: 14, height: 14) }
                Text(text).font(AppFont.manrope(12, .bold))
                    .foregroundColor(state.phase == .failed ? Theme.coralText : Theme.muted)
                    .lineLimit(2)
            }
            .accessibilityElement(children: .combine)
        }
    }
}

/// The People tab: everyone the hub returns, "Home" / a named place / how far, and when that was.
/// Tapping someone asks their phone for ONE fresh location ("Updating…" until it arrives) and, when
/// they have one, opens them on a map. The list itself re-reads the hub every 30 s while shown (live
/// messages between) - that never wakes anyone's phone.
struct PeopleScreen: View {
    @EnvironmentObject var model: AppModel
    @ObservedObject private var sharing = LocationSharing.shared
    @State private var following: Person?
    @State private var showMe = false
    @State private var now = Date().timeIntervalSince1970

    var body: some View {
        ZStack {
            NightGlassBackground()
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    Text("People").font(AppFont.sora(28)).foregroundColor(Theme.text).padding(.top, 12).padding(.bottom, 4)
                        .accessibilityAddTraits(.isHeader)
                    if needsPermission {
                        Button { showMe = true } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(sharing.settings?.mode == "required" ? "Your household requires location sharing" : "Location sharing is on")
                                    .font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                                Text("Allow location for Newman so your family can see you. Tap to set it up.")
                                    .font(AppFont.manrope(12, .bold)).foregroundColor(Theme.coralText)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .glassCard(padding: 14, fill: Theme.coral.opacity(0.10), border: Theme.coral.opacity(0.35))
                        }
                        .buttonStyle(.plain)
                    }
                    content
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
            }
            .refreshable { await reload() }
        }
        .task {
            while !Task.isCancelled {
                await reload()
                try? await Task.sleep(nanoseconds: UInt64(ReportSchedule.peopleRefreshSeconds) * 1_000_000_000)
            }
        }
        .fullScreenCover(item: $following) { p in
            PersonMapView(personId: p.id).environmentObject(model)
        }
        .sheet(isPresented: $showMe) { MeScreen().environmentObject(model).environmentObject(PushManager.shared) }
    }

    private func reload() async {
        await model.reloadPeople()
        now = Date().timeIntervalSince1970
    }

    private var needsPermission: Bool { sharing.settings?.sharing == true && !sharing.hasWhenInUse }

    @ViewBuilder private var content: some View {
        if model.peopleStatus == .notSetUp {
            message("Location sharing isn't set up on your hub yet")
        } else if model.people.isEmpty && model.peopleStatus == .loading {
            ProgressView().tint(Theme.text).frame(maxWidth: .infinity).padding(.top, 60)
        } else if model.people.isEmpty && model.peopleStatus == .unreachable {
            message("Can't reach Newman right now. Pull to try again.")
        } else if model.people.isEmpty {
            message("No one in your household yet")
        } else {
            ForEach(Array(model.people.enumerated()), id: \.element.id) { i, p in
                let row = PeopleText.row(p, now: now)
                let refresh = model.refreshState(p.id)
                Button {
                    if p.sharing { model.refreshPerson(p.id) }
                    if row.located { following = p }
                } label: {
                    HStack(spacing: 12) {
                        PersonAvatar(person: p, index: i, dimmed: !p.sharing)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.name).font(AppFont.manrope(16, .heavy)).foregroundColor(Theme.text).lineLimit(1)
                            HStack(spacing: 6) {
                                if row.home { Circle().fill(Theme.teal).frame(width: 7, height: 7) }
                                Text(row.whereText).font(AppFont.manrope(13, .bold))
                                    .foregroundColor(row.home ? Theme.teal : (p.sharing ? Theme.text.opacity(0.85) : Theme.muted)).lineLimit(1)
                            }
                            if refresh.phase != .idle {
                                RefreshNote(state: refresh, now: Date().timeIntervalSince1970)
                            } else if let u = row.updated {
                                Text(u).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                            }
                        }
                        Spacer()
                        if row.located { Image(systemName: "chevron.right").font(.system(size: 14, weight: .bold)).foregroundColor(Theme.dim) }
                    }
                    .glassCard(radius: 20, padding: 12)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(row.name)
                .accessibilityValue([row.whereText, refresh.phase != .idle ? PersonRefresh.text(refresh, now: now) : row.updated,
                                     p.sharing ? nil : "not sharing"].compactMap { $0 }.joined(separator: ", "))
                .accessibilityHint(row.located ? "Gets a fresh location and shows them on a map" : (p.sharing ? "Gets a fresh location" : ""))
            }
            if SharingSwitch.state(sharing.settings).visible && sharing.settings?.sharing == false {
                Button { showMe = true } label: { Label("Share my location too", systemImage: "person.crop.circle").frame(maxWidth: .infinity) }
                    .buttonStyle(PillButtonStyle())
            }
        }
    }

    private func message(_ text: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "person.2").font(.system(size: 36)).foregroundColor(Theme.dim).accessibilityHidden(true)
            Text(text).font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted).multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 80)
        .padding(.horizontal, 24)
    }
}
