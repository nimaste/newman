import ActivityKit
import Foundation

/// Live status on the Lock Screen and in the Dynamic Island (Live Activities, drawn by the widget
/// extension: LiveStatusActivity). The hub starts, updates and ends them by APNs (push type
/// liveactivity); this side gives it the tokens for that (POST /api/live/tokens):
///   - the push-to-start token (iOS 17.2+), so the hub can start one without the app running;
///   - each running activity's own push token, for its updates and end (DELETE once it's over).
/// On start and back in the foreground it also matches the hub (GET /api/live/current): starts
/// what the hub couldn't start by push (no push-to-start token yet) and ends what the hub no
/// longer shows.
@MainActor
final class LiveActivities {
    static let shared = LiveActivities()

    private var watching = false
    /// Activities whose tokens and life cycle are being followed (ActivityKit ids).
    private var observed: Set<String> = []
    /// Activity id -> what was last sent for its push token (token, hub, session).
    private var sentUpdate: [String: String] = [:]

    private enum K {
        static let pushToStart = "live.pushToStartToken"
        static let pushToStartStamp = "live.pushToStartStamp"
    }

    private static var sandbox: Bool {
        #if DEBUG
        return true
        #else
        return false
        #endif
    }

    /// Signed in (app start) and back in the foreground: follow the tokens, then match the hub.
    /// iOS 26+ only: the widget extension that draws them is iOS 26, so an older iPhone gives the
    /// hub no tokens (and the hub shows it no live status).
    func start() async {
        guard #available(iOS 26.0, *) else { return }
        watch()
        for a in Activity<NewmanLiveAttributes>.activities { observe(a) }
        await registerPushToStartIfNeeded()
        await reconcile()
    }

    /// Signing out: this phone shows no live status of that account any more.
    func signingOut() async {
        UserDefaults.standard.removeObject(forKey: K.pushToStartStamp)
        sentUpdate = [:]
        for a in Activity<NewmanLiveAttributes>.activities {
            await a.end(nil, dismissalPolicy: .immediate)
        }
    }

    // MARK: following ActivityKit

    private func watch() {
        guard !watching else { return }
        watching = true
        if #available(iOS 17.2, *) {
            Task {
                for await token in Activity<NewmanLiveAttributes>.pushToStartTokenUpdates {
                    await self.pushToStartChanged(token)
                }
            }
        }
        // Activities the hub started by push (and ones started here) show up here.
        Task {
            for await activity in Activity<NewmanLiveAttributes>.activityUpdates {
                self.observe(activity)
            }
        }
    }

    private func observe(_ activity: Activity<NewmanLiveAttributes>) {
        let id = activity.id
        guard !observed.contains(id) else { return }
        observed.insert(id)
        let key = activity.attributes.key
        let tokens = activity.pushTokenUpdates
        let states = activity.activityStateUpdates
        if let token = activity.pushToken {
            Task { await self.sendUpdateToken(token, key: key, activityId: id) }
        }
        Task {
            for await token in tokens {
                await self.sendUpdateToken(token, key: key, activityId: id)
            }
        }
        Task {
            for await state in states {
                if state == .ended || state == .dismissed {
                    await self.finished(key: key, activityId: id)
                    break
                }
            }
        }
    }

    // MARK: tokens (POST / DELETE /api/live/tokens)

    private func pushToStartChanged(_ token: Data) async {
        UserDefaults.standard.set(LiveStatusLogic.hexToken(token), forKey: K.pushToStart)
        await registerPushToStartIfNeeded()
    }

    /// Sends the push-to-start token when it, the hub address or the session changed.
    private func registerPushToStartIfNeeded() async {
        let store = ConnectionStore.shared
        guard let token = UserDefaults.standard.string(forKey: K.pushToStart), !token.isEmpty,
              let session = store.token, let server = store.serverURL?.absoluteString else { return }
        let stamp = WidgetLogic.registrationStamp(token: token, server: server, session: session)
        guard UserDefaults.standard.string(forKey: K.pushToStartStamp) != stamp else { return }
        let body = LiveStatusLogic.pushToStartBody(token: token, deviceId: store.deviceId, sandbox: Self.sandbox)
        if (try? await WidgetHub.checked("POST", "api/live/tokens", body: JSONBody.encode(body))) != nil {
            UserDefaults.standard.set(stamp, forKey: K.pushToStartStamp)
        }
    }

    private func sendUpdateToken(_ token: Data, key: String, activityId: String) async {
        let store = ConnectionStore.shared
        guard let session = store.token, let server = store.serverURL?.absoluteString else { return }
        let hex = LiveStatusLogic.hexToken(token)
        let stamp = WidgetLogic.registrationStamp(token: hex, server: server, session: session)
        guard sentUpdate[activityId] != stamp else { return }
        let body = LiveStatusLogic.updateBody(activityKey: key, token: hex, deviceId: store.deviceId, sandbox: Self.sandbox)
        if (try? await WidgetHub.checked("POST", "api/live/tokens", body: JSONBody.encode(body))) != nil {
            sentUpdate[activityId] = stamp
        }
    }

    /// Ended (by the hub, the 8-hour limit, or swiped away): the hub forgets its token.
    private func finished(key: String, activityId: String) async {
        sentUpdate[activityId] = nil
        guard WidgetHub.isSignedIn else { return }
        let body = LiveStatusLogic.endBody(activityKey: key, deviceId: ConnectionStore.shared.deviceId)
        _ = try? await WidgetHub.checked("DELETE", "api/live/tokens", body: JSONBody.encode(body))
    }

    // MARK: matching the hub (GET /api/live/current)

    private func reconcile() async {
        guard WidgetHub.isSignedIn,
              let data = try? await WidgetHub.checked("GET", LiveStatusLogic.currentPath(deviceId: ConnectionStore.shared.deviceId)),
              let current = LiveStatusLogic.parseCurrent(data) else { return } // unreachable / older hub: leave them
        let running = Activity<NewmanLiveAttributes>.activities.filter {
            $0.activityState == .active || $0.activityState == .stale
        }
        let plan = LiveStatusLogic.plan(local: running.map { LiveStatusLocal(id: $0.id, key: $0.attributes.key) },
                                        current: current.map(\.key))
        for a in running where plan.end.contains(a.id) {
            await a.end(nil, dismissalPolicy: .immediate)
        }
        let canStart = ActivityAuthorizationInfo().areActivitiesEnabled
        for item in current {
            if let id = plan.update[item.key], let a = running.first(where: { $0.id == id }) {
                if a.content.state != item.state {
                    await a.update(ActivityContent(state: item.state, staleDate: nil))
                }
            } else if canStart && plan.start.contains(item.key) {
                do {
                    let a = try Activity<NewmanLiveAttributes>.request(
                        attributes: NewmanLiveAttributes(key: item.key, title: item.title),
                        content: ActivityContent(state: item.state, staleDate: nil),
                        pushType: .token)
                    observe(a)
                } catch {
                    // Live Activities turned off, too many running, or not in the foreground.
                }
            }
        }
    }
}
