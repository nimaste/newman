import Foundation

/// People (GET /api/people, tap-to-refresh POST /api/people/{id}/refresh, the signed-in person's
/// colour and photo) and Today (GET /api/today and the task actions). Older hubs answer 404: the
/// People tab says so calmly and the Today card stays hidden.
extension AppModel {
    // MARK: people

    func reloadPeople() async {
        do {
            people = try await APIClient.shared.people()
            peopleStatus = .ready
            people.forEach { personArrived($0) }
        } catch let e as APIError where e.status == 404 {
            people = []
            peopleStatus = .notSetUp
        } catch {
            if people.isEmpty { peopleStatus = .unreachable }
        }
    }

    func reloadPlaces() async {
        if let p = try? await APIClient.shared.places() { places = p }
    }

    func person(_ id: String) -> Person? { people.first { $0.id == id } }

    /// The signed-in person (their colour and photo on the Me screen).
    var mePerson: Person? { (user?.personId ?? personId).flatMap { person($0) } }

    // MARK: tap-to-refresh

    func refreshState(_ id: String) -> PersonRefresh.State { refreshes[id] ?? .idle }

    /// Asks the hub for ONE fresh fix of this person (it allows one per 2 minutes per person).
    func refreshPerson(_ id: String) {
        let now = Date().timeIntervalSince1970
        let before = refreshState(id)
        let started = PersonRefresh.start(before, person: person(id), now: now)
        guard started != before else { return } // already updating
        refreshes[id] = started
        startRefreshTicker()
        Task {
            var status = 0
            var next: Double?
            if let reply = try? await APIClient.shared.refreshPerson(id) {
                status = reply.0
                next = PersonRefresh.nextAllowedAt(reply.1)
            }
            refreshes[id] = PersonRefresh.replied(refreshState(id), status: status, nextAllowedAt: next,
                                                  now: Date().timeIntervalSince1970)
        }
    }

    /// A "person" update (or a reload) ends "Updating…" once it carries a newer fix.
    func personArrived(_ p: Person) {
        guard let s = refreshes[p.id] else { return }
        let next = PersonRefresh.personChanged(s, p)
        if next != s { refreshes[p.id] = next == .idle ? nil : next }
    }

    /// Once a second while any refresh shows something: timeouts and countdowns.
    private func startRefreshTicker() {
        guard refreshTicker == nil else { return }
        refreshTicker = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                guard let self else { return }
                let now = Date().timeIntervalSince1970
                var next: [String: PersonRefresh.State] = [:]
                for (id, s) in self.refreshes {
                    let t = PersonRefresh.tick(s, now: now)
                    if t != .idle { next[id] = t }
                }
                // Republish every second while a countdown shows (its seconds change).
                self.refreshes = next
                if next.isEmpty {
                    self.refreshTicker = nil
                    return
                }
            }
        }
    }

    // MARK: my colour and photo

    /// PUT /api/me/profile; the live "person" message brings the new colour (reloaded here too).
    func setMyColor(_ color: String) async {
        do {
            try await APIClient.shared.setMyColor(color)
            await reloadPeople()
        } catch {
            show((error as? APIError)?.message ?? error.localizedDescription, error: true)
        }
    }

    /// Uploads a JPEG (already made square and small enough) as the signed-in person's photo.
    func setMyPhoto(_ jpeg: Data) async -> Bool {
        do {
            try await APIClient.shared.uploadMyPhoto(jpeg)
            await reloadPeople()
            return true
        } catch {
            show((error as? APIError)?.message ?? error.localizedDescription, error: true)
            return false
        }
    }

    func removeMyPhoto() async {
        do {
            try await APIClient.shared.deleteMyPhoto()
            await reloadPeople()
        } catch {
            show((error as? APIError)?.message ?? error.localizedDescription, error: true)
        }
    }

    // MARK: today

    func reloadToday() async {
        todayLoadedAt = Date().timeIntervalSince1970
        do {
            today = try await APIClient.shared.today()
        } catch let e as APIError where e.status == 404 {
            today = nil
        } catch {
            // Keep what we had.
        }
    }

    /// A device changed: reload Today a little later (at most every 15 s).
    func todaySoon() {
        guard today != nil, todayReload == nil else { return }
        let wait = max(2, 15 - (Date().timeIntervalSince1970 - todayLoadedAt))
        todayReload = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
            guard let self else { return }
            self.todayReload = nil
            await self.reloadToday()
        }
    }

    /// Done (a GCloud task) or dismiss (an appliance's need). Returns false and shows the hub's words on failure.
    @discardableResult
    func todayDo(_ item: TodayItem) async -> Bool {
        await todayAct {
            if item.action == "dismiss" {
                try await APIClient.shared.todayDismiss(item.id)
                self.todayDone[item.id] = TodayView.Done(item: item, taskId: nil, at: Date().timeIntervalSince1970)
            } else {
                try await APIClient.shared.taskDone(item.id)
                self.todayDone[item.id] = TodayView.Done(item: item, taskId: item.id, at: Date().timeIntervalSince1970)
            }
        }
    }

    @discardableResult
    func todayUndo(_ item: TodayItem) async -> Bool {
        await todayAct {
            if let id = self.todayDone[item.id]?.taskId { try await APIClient.shared.taskUndo(id) }
            self.todayDone[item.id] = nil
        }
    }

    /// Remind tomorrow.
    @discardableResult
    func todaySnooze(_ item: TodayItem) async -> Bool {
        let ok = await todayAct { try await APIClient.shared.taskSnooze(item.id) }
        if ok { show("We'll remind you tomorrow") }
        return ok
    }

    private func todayAct(_ block: () async throws -> Void) async -> Bool {
        do {
            try await block()
            await reloadToday()
            return true
        } catch {
            show((error as? APIError)?.message ?? error.localizedDescription, error: true)
            return false
        }
    }

    // MARK: tabs

    /// The tabs: from the hub's phone layout when it has one, else flowed from the dashboard.
    var tabPlan: PhoneTabs.Plan { phoneLayout.map(PhoneGrid.plan) ?? PhoneTabs.plan(pages) }

    /// The page a compact thermostat tile opens (Climate), when that page has no tab.
    func pageForDial(_ entityId: String?) -> DashboardPage? { PhoneTabs.pageForDial(tabPlan, entityId: entityId) }
}
