import SwiftUI
import UIKit

/// Everything the phone shows, kept current by the live connection: meta, devices, the
/// dashboard, activity. Commands go through here (optimistic, rolled back on error).
@MainActor
final class AppModel: ObservableObject {
    enum Phase { case onboarding, loading, ready }

    @Published var phase: Phase = .onboarding
    @Published var meta: Meta?
    @Published var kinds: [String: Kind] = [:]
    @Published var entities: [String: Entity] = [:]
    @Published var dashboard: Dashboard?
    /// GET /api/phone-layout; nil on hubs without phone layouts (the dashboard flows instead).
    @Published var phoneLayout: PhoneGridLayout?
    @Published var authState: AuthState?
    @Published var user: User?
    @Published var events: [ActivityEvent] = []
    @Published var cameraAlerts: [CameraAlert] = []
    @Published var liveStatus: LiveConnection.Status = .connecting
    @Published var loadError: String?
    @Published var toast: Toast?
    @Published var busy: Set<String> = []
    @Published var personId: String?
    @Published var openCamera: CameraTarget?
    @Published var selectedTab = "home"
    /// GET /api/home: the Home tab's cards, status, favourites, the Cameras tab's big picture and Climate's rooms.
    @Published var home: HomeData?
    /// More › Alerts scrolls to and marks this entry (a tapped notification's alert).
    @Published var highlightedAlert: String?
    /// The Home tab's screens (Climate, Lights, Security…), pushed in order.
    @Published var homePath: [HomeRoute] = []
    /// The Cameras tab's screens (one camera).
    @Published var camerasPath: [CamerasRoute] = []
    var homeReload: Task<Void, Never>?
    @Published var unseenAlerts = 0
    /// The More tab's screens (Alerts, Devices, Automations), pushed in order.
    @Published var morePath: [MoreRoute] = []
    /// GET /api/automations for More › Automations; nil until first loaded.
    @Published var automations: [AutomationSummary]?
    /// A newman://connect link (or QR code) waiting for the Server screen.
    @Published var incomingLink: HubCandidate?
    /// The Server screen over the signed-in app (opened by a link).
    @Published var serverSheetOpen = false
    /// A device's sheet opened from a Lock Screen widget (newman://device?id=…).
    @Published var openDevice: DeviceLink?

    struct DeviceLink: Identifiable, Equatable {
        var id: String
    }

    // People (GET /api/people, live "person" messages) and Today (GET /api/today).
    enum PeopleStatus { case loading, ready, notSetUp, unreachable }
    @Published var people: [Person] = []
    @Published var peopleStatus: PeopleStatus = .loading
    @Published var places: [Place] = []
    /// Tap-to-refresh per person id ("Updating…", timeouts, "Updated recently").
    @Published var refreshes: [String: PersonRefresh.State] = [:]
    var refreshTicker: Task<Void, Never>?
    /// nil = not loaded, or an older hub without Today (the card stays hidden).
    @Published var today: TodayBriefing?
    /// Items done on this phone lately (kept with Undo).
    @Published var todayDone: [String: TodayView.Done] = [:]
    var todayLoadedAt: Double = 0
    var todayReload: Task<Void, Never>?

    /// Face ID before unlocking, opening or disarming (on by default; a switch in Me).
    @Published var biometricsEnabled: Bool = UserDefaults.standard.object(forKey: "biometricsForSensitive") as? Bool ?? true {
        didSet { UserDefaults.standard.set(biometricsEnabled, forKey: "biometricsForSensitive") }
    }
    private var alertsSeenAt: Double {
        get { UserDefaults.standard.double(forKey: "alertsSeenAt") }
        set { UserDefaults.standard.set(newValue, forKey: "alertsSeenAt") }
    }

    struct Toast: Identifiable, Equatable {
        let id = UUID()
        var text: String
        var isError: Bool
    }

    struct CameraTarget: Identifiable, Equatable {
        var id: String { entityId + (alert?.id ?? "") }
        var entityId: String
        var cameraIds: [String]
        var alert: CameraAlert?
    }

    let live = LiveConnection()
    private var dashboardReload: Task<Void, Never>?

    init() {
        live.onMessage = { [weak self] m in self?.handle(m) }
        live.onReconnected = { [weak self] in Task { await self?.reloadAll() } }
        live.onStatus = { [weak self] s in self?.liveStatus = s }
        NotificationCenter.default.addObserver(forName: .newmanSignedOut, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.sessionEnded() }
        }
        NotificationCenter.default.addObserver(forName: .newmanServerChanged, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.serverChanged() }
        }
        phase = ConnectionStore.shared.token == nil || ConnectionStore.shared.serverURL == nil ? .onboarding : .loading
    }

    // MARK: lifecycle

    func start() async {
        guard phase != .onboarding else { return }
        await reloadAll()
        live.start()
        // Widgets: the hub's widget push token (kept by the widget extension), the device names Siri knows.
        await WidgetHub.registerPushTokenIfNeeded()
        await LiveActivities.shared.start() // live status tokens, and what the hub shows right now
        NewmanShortcuts.updateAppShortcutParameters()
        WidgetRefresh.reloadAll()
        await PushManager.shared.refreshCategories()
        PushManager.shared.requestAndRegister()
        await LocationSharing.shared.refresh()
        await LocationSharing.shared.reportNow()
    }

    func signedIn(user: User?) async {
        self.user = user
        phase = .loading
        await start()
    }

    func reloadAll() async {
        do {
            async let m = APIClient.shared.meta()
            async let e = APIClient.shared.entities()
            async let d = APIClient.shared.currentDashboard()
            async let a = APIClient.shared.authState()
            async let l = APIClient.shared.phoneLayout()
            let (meta, ents, dash, auth) = try await (m, e, d, a)
            let layout = await l
            self.meta = meta
            kinds = meta.kindsById
            entities = Dictionary(ents.map { ($0.id, $0) }, uniquingKeysWith: { _, b in b })
            dashboard = dash
            phoneLayout = PhoneGrid.next(phoneLayout, layout)
            authState = auth
            if let u = auth.user { user = u }
            loadError = nil
            phase = .ready
            await reloadHome()
        } catch let e as APIError {
            loadError = e.message
            if e.signInRequired { sessionEnded() }
        } catch {
            loadError = error.localizedDescription
        }
        await reloadActivity()
    }

    func reloadActivity() async {
        if let ev = try? await APIClient.shared.events(limit: 100) {
            events = ev
            recountUnseen()
        }
        if let al = try? await APIClient.shared.alerts(limit: 30) { cameraAlerts = al }
    }

    func reloadAuthState() async {
        if let a = try? await APIClient.shared.authState() {
            authState = a
            if let u = a.user { user = u }
        }
    }

    func foreground() {
        Task {
            // Home or away since last time? (HubRoute: the hub's home address when it answers.)
            let moved = await HubRoute.shared.check()
            guard phase == .ready else { return }
            if moved {
                live.stop()
                live.start()
            } else {
                live.kick()
            }
            await reloadAll()
            await reloadToday()
            await LiveActivities.shared.start()
            await LocationSharing.shared.refresh()
            await LocationSharing.shared.reportNow()
            await PushManager.shared.refreshCategories() // button sets of automations made since
        }
    }

    func signOut() async {
        await LocationSharing.shared.signingOut() // the hub forgets this phone's location push token
        await LiveActivities.shared.signingOut()
        await APIClient.shared.logout()
        sessionEnded()
    }

    /// The hub's address in use changed (network change, or a new one saved on the Server
    /// screen): reconnect and reload from there.
    func serverChanged() {
        guard phase != .onboarding else { return }
        live.stop()
        live.start()
        Task { await reloadAll() }
    }

    /// A different hub is replacing this one on the Server screen: sign out of this one first.
    func leaveHub() async {
        guard phase != .onboarding else { return }
        await signOut()
    }

    /// newman://connect?... - opens the Server screen with that hub filled in.
    func open(_ url: URL) {
        if url.scheme?.lowercased() == "newman", url.host?.lowercased() == "device" {
            // From a Lock Screen widget: that device's sheet.
            let id = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "id" }?.value
            // Still loading: the sheet opens with the main screen (RootView).
            if let id, !id.isEmpty, phase != .onboarding { openDevice = DeviceLink(id: id) }
            return
        }
        guard let link = ConnectLink.parse(url.absoluteString) else { return }
        incomingLink = link
        if phase != .onboarding { serverSheetOpen = true }
    }

    /// Back to sign-in; the server address and certificate stay.
    func sessionEnded() {
        live.stop()
        LocationSharing.shared.signedOut()
        ConnectionStore.shared.token = nil
        dashboard = nil
        phoneLayout = nil
        entities = [:]
        events = []
        cameraAlerts = []
        people = []
        peopleStatus = .loading
        refreshes = [:]
        today = nil
        todayDone = [:]
        automations = nil
        morePath = []
        home = nil
        homePath = []
        camerasPath = []
        selectedTab = "home"
        user = nil
        phase = .onboarding
    }

    // MARK: live

    private func handle(_ m: LiveMessage) {
        switch m {
        case .entity, .entityRemoved:
            entities = EntityUpdate.apply(entities, m)
            todaySoon()
        case .person(let p):
            people = PeopleList.upsert(people, p)
            personArrived(p)
        case .activity(let entry):
            if !events.contains(where: { $0.id == entry.id }) { events.insert(entry, at: 0) }
            recountUnseen()
            if entry.kind == "alert" { Task { if let al = try? await APIClient.shared.alerts(limit: 30) { cameraAlerts = al } } }
        case .dashboard:
            dashboardReload?.cancel()
            dashboardReload = Task {
                try? await Task.sleep(nanoseconds: 300_000_000)
                if Task.isCancelled { return }
                async let l = APIClient.shared.phoneLayout()
                if let d = try? await APIClient.shared.currentDashboard() { dashboard = d }
                let fetched = await l
                phoneLayout = PhoneGrid.next(phoneLayout, fetched)
            }
        case .settings:
            Task { await reloadAuthState() }
            homeSoon()
        case .unknown(let type):
            // {"type": "home"}: something Home shows changed (the hub says so; read it again).
            // {"type": "settings-house"}: the house settings changed (the Cameras tab's big picture…).
            if type == "home" || type == "settings-house" { homeSoon() }
        case .hello, .event, .pong:
            break
        }
    }

    // MARK: alerts badge

    func markAlertsSeen() {
        alertsSeenAt = Date().timeIntervalSince1970
        unseenAlerts = 0
    }

    /// Alerts on screen: More › Alerts is the top of the More tab, and that tab is showing.
    var alertsShowing: Bool { selectedTab == "more" && morePath.last == .alerts }

    /// A tapped notification: More › Alerts (the camera alert itself opens over it, openAlert).
    func showAlerts() {
        selectedTab = "more"
        morePath = [.alerts]
        markAlertsSeen()
    }

    private func recountUnseen() {
        if alertsSeenAt == 0 { alertsSeenAt = Date().timeIntervalSince1970 }
        unseenAlerts = alertsShowing ? 0 : events.filter { $0.ts > alertsSeenAt }.count
    }

    // MARK: lookups

    /// A device with this dashboard's "Show as" name.
    func entity(_ id: String) -> Entity? {
        guard var e = entities[id] else { return nil }
        if let n = dashboard?.names?[id], !n.trimmingCharacters(in: .whitespaces).isEmpty { e.name = n }
        return e
    }

    func kind(of e: Entity?) -> Kind? { e.flatMap { kinds[$0.kind] } }

    var allEntities: [Entity] { entities.keys.sorted().compactMap { entity($0) } }

    var pages: [DashboardPage] { dashboard?.pages ?? [] }

    func url(_ path: String?) -> URL? {
        guard let path, let base = ConnectionStore.shared.serverURL else { return nil }
        return ServerAddress.url(base, path)
    }

    /// A tapped camera notification: that alert full screen on its camera, its video playing
    /// (CameraView). Stays on the Alerts tab when the hub doesn't list it (any more).
    func openAlert(clip: String, reviewId: String?) async {
        for attempt in 0..<2 {
            if attempt > 0 { try? await Task.sleep(nanoseconds: 2_000_000_000) } // e.g. still finding the hub's address
            guard let list = try? await APIClient.shared.alerts(limit: 60) else { continue }
            if let a = AlertClip.match(list, clip: clip, reviewId: reviewId), let eid = a.entityId {
                openCamera = CameraTarget(entityId: eid, cameraIds: [eid], alert: a)
            }
            return
        }
    }

    func stillURL(_ e: Entity, height: Int) -> URL? {
        guard let u = url(e.still) else { return nil }
        return ServerAddress.with(u, "h", String(height))
    }

    // MARK: commands

    /// Sends a command: Face ID first when it's sensitive, the device shown in its new state
    /// at once, put back with the hub's error text if it refuses.
    func run(_ entityId: String, _ cmd: Command, value: JSONValue? = nil) async {
        guard let e = entities[entityId] else { return }
        if DeviceLogic.needsAuthentication(cmd, biometricsEnabled: biometricsEnabled) {
            let name = entity(entityId)?.name ?? e.name
            switch await DeviceAuth.authenticate(reason: "\(cmd.label) \(name)") {
            case .ok: break
            case .cancelled: return
            case .failed(let msg):
                show(msg, error: true)
                return
            }
        }
        let before = e
        entities[entityId] = DeviceLogic.optimistic(e, kinds[e.kind], cmd, value: value)
        busy.insert(entityId)
        defer { busy.remove(entityId) }
        do {
            try await APIClient.shared.command(entityId: entityId, command: cmd, value: value)
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        } catch {
            // Only roll back if nothing newer arrived over the live connection meanwhile.
            if entities[entityId]?.updatedAt == before.updatedAt { entities[entityId] = before }
            show((error as? APIError)?.message ?? error.localizedDescription, error: true)
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        }
    }

    func show(_ text: String, error: Bool = false) {
        toast = Toast(text: text, isError: error)
        let id = toast?.id
        Task {
            try? await Task.sleep(nanoseconds: 3_500_000_000)
            if toast?.id == id { toast = nil }
        }
    }
}
