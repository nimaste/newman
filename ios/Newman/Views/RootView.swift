import SwiftUI

struct RootView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            NightGlassBackground()
            switch model.phase {
            case .onboarding:
                OnboardingView()
            case .loading:
                LoadingView()
            case .ready:
                MainTabs()
                    .background {
                        // A device opened from a Lock Screen widget (a separate host: one sheet per view).
                        Color.clear.sheet(item: $model.openDevice) { link in
                            DeviceSheet(entityId: link.id).environmentObject(model)
                        }
                    }
            }
        }
        .overlay(alignment: .bottom) {
            if let t = model.toast {
                Text(t.text)
                    .font(AppFont.manrope(14, .heavy))
                    .multilineTextAlignment(.center)
                    .foregroundColor(t.isError ? Theme.text : Color(hex: 0x04211C))
                    .padding(.horizontal, 16)
                    .frame(minHeight: 48)
                    .frame(maxWidth: .infinity)
                    .background(RoundedRectangle(cornerRadius: 14).fill(t.isError ? Theme.coral.opacity(0.95) : Theme.teal.opacity(0.95)))
                    .padding(.horizontal, 16)
                    .padding(.bottom, 98)
                    .transition(reduceMotion ? .opacity : .move(edge: .bottom).combined(with: .opacity))
                    .onTapGesture { model.toast = nil }
                    .accessibilityAddTraits(.isStaticText)
                    .accessibilityHint("Tap to dismiss")
            }
        }
        .animation(.easeOut(duration: 0.2), value: model.toast)
        .onChange(of: model.toast) { _, t in
            // VoiceOver reads the toast (it appears away from the focus).
            if let t { AccessibilityNotification.Announcement(t.text).post() }
        }
        .fullScreenCover(item: $model.openCamera) { target in
            CameraView(target: target).environmentObject(model)
        }
        .sheet(isPresented: $model.serverSheetOpen) {
            ServerSheet(link: $model.incomingLink, leavingHub: { await model.leaveHub() },
                        onSaved: { model.serverChanged() }) { model.serverSheetOpen = false }
        }
    }
}

struct LoadingView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(spacing: 16) {
            if let err = model.loadError {
                Image(systemName: "wifi.exclamationmark").font(.system(size: 40)).foregroundColor(Theme.muted)
                    .accessibilityHidden(true)
                Text(err).font(AppFont.manrope(15, .bold)).foregroundColor(Theme.text).multilineTextAlignment(.center)
                Button("Try again") { Task { await model.reloadAll() } }.buttonStyle(PillButtonStyle())
                Button("Sign out") { Task { await model.signOut() } }.buttonStyle(PillButtonStyle())
            } else {
                ProgressView().tint(Theme.text)
                Text("Connecting to Newman…").font(AppFont.manrope(15, .bold)).foregroundColor(Theme.muted)
            }
        }
        .padding(32)
    }
}

/// Five tabs (PhoneTabs): the dashboard's first three pages (icon and name from each page), People
/// and Alerts. A Today page becomes the Today card on the first tab; other pages (Climate) open
/// from their thermostat tile or a link at the bottom of the first tab.
struct MainTabs: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        let plan = model.tabPlan
        TabView(selection: $model.selectedTab) {
            ForEach(Array(plan.tabs.enumerated()), id: \.element.id) { index, page in
                PageScreen(page: page, isFirst: index == 0)
                    .tabChrome()
                    .tabItem { Label(page.name, systemImage: Symbols.name(page.icon)) }
                    .tag("page:\(index)")
            }
            PeopleScreen()
                .tabChrome()
                .tabItem { Label("People", systemImage: "person.2") }
                .tag("people")
            AlertsScreen()
                .tabChrome()
                .tabItem { Label("Alerts", systemImage: "bell") }
                .badge(model.unseenAlerts)
                .tag("alerts")
        }
        .tint(Theme.text)
        .onChange(of: model.selectedTab) { _, tab in
            if tab == "alerts" { model.markAlertsSeen() }
        }
    }
}

extension View {
    /// The boards' tab bar: navy glass with a hairline on top.
    func tabChrome() -> some View {
        self
            .toolbarBackground(.visible, for: .tabBar)
            .toolbarBackground(Color(hex: 0x0B1026, alpha: 0.86), for: .tabBar)
            .toolbarColorScheme(.dark, for: .tabBar)
    }
}
