import SwiftUI

/// Today: CALENDAR (today's events), TO DO (tap to tick one done; "+ Add" makes a GCloud task) and
/// COMING UP (the next events of the coming days and the tasks coming due, with their status), from
/// GET /api/today and the calendars' upcoming events.
struct TodayScreen: View {
    @EnvironmentObject var model: AppModel
    @State private var adding = false
    @State private var sheetFor: HomeSheet?

    var body: some View {
        let briefing = model.today ?? TodayBriefing()
        let calendars = TodayScreenLogic.calendars(model.allEntities)
        ZStack {
            NightGlassBackground(teal: false)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Today").font(AppFont.sora(28)).foregroundColor(Theme.text)
                            .accessibilityAddTraits(.isHeader)
                        Text(Date().formatted(.dateTime.weekday(.wide).month(.wide).day()))
                            .font(AppFont.manrope(13, .heavy)).foregroundColor(Theme.muted)
                    }
                    calendarCard(briefing.events, calendars: calendars)
                    todoCard(briefing.todo)
                    comingUpCard(TodayScreenLogic.comingUp(calendars, now: Date()), tasks: briefing.soon)
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 32)
            }
            .refreshable { await reload() }
        }
        .task { await reload() }
        .sheet(isPresented: $adding) { AddTaskSheet().environmentObject(model) }
        .sheet(item: $sheetFor) { s in DeviceSheet(entityId: s.id).environmentObject(model) }
    }

    private func reload() async {
        await model.reloadToday()
    }

    private func empty(_ text: String) -> some View {
        Text(text).font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted).padding(.vertical, 8)
    }

    private func calendarCard(_ events: [TodayEvent], calendars: [Entity]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            CardHeader(text: "Calendar").padding(.bottom, 4)
            if events.isEmpty { empty(TodayScreenLogic.emptyCalendarLine(calendars)) }
            ForEach(Array(events.enumerated()), id: \.offset) { i, ev in
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    Text(ev.time ?? "").font(AppFont.manrope(13, .heavy))
                        .foregroundColor(ev.now == true ? Theme.teal : Theme.blue)
                        .frame(width: 72, alignment: .leading)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(ev.title ?? "Busy").font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                        let place = [ev.location ?? "", ev.calendar ?? ""].filter { !$0.isEmpty }.joined(separator: " · ")
                        if !place.isEmpty { Text(place).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted) }
                    }
                    Spacer(minLength: 0)
                }
                .padding(.vertical, 9)
                .accessibilityElement(children: .combine)
                if i < events.count - 1 { Divider().overlay(Theme.divider) }
            }
        }
        .glassCard(padding: 14)
    }

    private func todoCard(_ items: [TodayItem]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            CardHeader(text: "To do") {
                Button("+ Add") { adding = true }
                    .font(AppFont.manrope(13, .heavy)).foregroundColor(Theme.blue)
            }
            .padding(.bottom, 4)
            let now = Date().timeIntervalSince1970
            let done = model.todayDone.values
                .filter { d in now - d.at < TodayView.keepDoneSeconds && !items.contains { $0.id == d.item.id } }
                .sorted { $0.at > $1.at }
            if items.isEmpty && done.isEmpty { empty("Nothing to do") }
            ForEach(Array(items.enumerated()), id: \.element.id) { i, item in
                todoRow(item)
                if i < items.count - 1 || !done.isEmpty { Divider().overlay(Theme.divider) }
            }
            ForEach(Array(done.enumerated()), id: \.element.item.id) { i, d in
                doneRow(d.item)
                if i < done.count - 1 { Divider().overlay(Theme.divider) }
            }
        }
        .glassCard(padding: 14)
    }

    private func todoRow(_ item: TodayItem) -> some View {
        let opens = item.action == "open"
        return Button {
            if opens, let e = item.entity { sheetFor = HomeSheet(id: e) } else { Task { await model.todayDo(item) } }
        } label: {
            HStack(spacing: 12) {
                Image(systemName: opens ? "exclamationmark.circle" : "circle")
                    .font(.system(size: 22, weight: .regular))
                    .foregroundColor(item.level == "overdue" || item.level == "device" ? Theme.coralText : Theme.muted)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 1) {
                    Text(item.title).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                        .multilineTextAlignment(.leading)
                    let line = [item.status, item.owner].filter { !$0.isEmpty }.joined(separator: " · ")
                    if !line.isEmpty {
                        Text(line).font(AppFont.manrope(12, .bold))
                            .foregroundColor(item.level == "overdue" ? Theme.coralText : Theme.muted)
                    }
                }
                Spacer(minLength: 0)
            }
            .frame(minHeight: 52)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityHint(opens ? "Opens the device" : (item.action == "dismiss" ? "Dismisses it" : "Marks it done"))
    }

    /// Just ticked done: struck through, with Undo for a while (TodayView.keepDoneSeconds).
    private func doneRow(_ item: TodayItem) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "checkmark.circle.fill").font(.system(size: 22)).foregroundColor(Theme.teal)
                .accessibilityHidden(true)
            Text(item.title).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.muted).strikethrough()
            Spacer(minLength: 8)
            Button("Undo") { Task { await model.todayUndo(item) } }
                .font(AppFont.manrope(13, .heavy)).foregroundColor(Theme.blue)
        }
        .frame(minHeight: 52)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(item.title), done")
        .accessibilityAction(named: "Undo") { Task { await model.todayUndo(item) } }
    }

    private func comingUpCard(_ events: [TodayScreenLogic.Upcoming], tasks: [TodayItem]) -> some View {
        let rows: [(String, String, String)] = events.map { ($0.day, $0.title, $0.calendar) }
            + tasks.map { ("Task", $0.title, $0.status) }
        return VStack(alignment: .leading, spacing: 0) {
            CardHeader(text: "Coming up").padding(.bottom, 4)
            if rows.isEmpty { empty("Nothing coming up") }
            ForEach(Array(rows.enumerated()), id: \.offset) { i, r in
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    Text(r.0).font(AppFont.manrope(12, .heavy)).foregroundColor(Theme.dim)
                        .frame(width: 72, alignment: .leading)
                    Text(r.1).font(AppFont.manrope(14, .heavy)).foregroundColor(Theme.text)
                    Spacer(minLength: 8)
                    Text(r.2).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                        .multilineTextAlignment(.trailing)
                }
                .padding(.vertical, 9)
                .accessibilityElement(children: .combine)
                if i < rows.count - 1 { Divider().overlay(Theme.divider) }
            }
        }
        .glassCard(padding: 14)
    }
}

/// Today's "+ Add": a GCloud task - what needs doing, when it's due, how often it comes back, who it
/// belongs to and which calendar it goes in (the ones Newman may write that take tasks).
struct AddTaskSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var due = Date()
    @State private var every = 1
    @State private var unit = "weeks"
    @State private var owner = ""
    @State private var calendar = ""
    @State private var options: TaskCalendars?
    @State private var saving = false

    private var calendars: [TaskCalendars.Entry] { options?.writable ?? [] }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("What needs doing", text: $name)
                        .font(AppFont.manrope(15, .bold))
                    DatePicker("Due", selection: $due, displayedComponents: .date)
                        .font(AppFont.manrope(15, .bold))
                }
                .listRowBackground(Theme.card)
                Section {
                    Stepper("Comes back every \(every) \(every == 1 ? String(unit.dropLast()) : unit)", value: $every, in: 1...365)
                        .font(AppFont.manrope(15, .bold))
                    Picker("Unit", selection: $unit) {
                        Text("Days").tag("days")
                        Text("Weeks").tag("weeks")
                        Text("Months").tag("months")
                    }
                    .pickerStyle(.segmented)
                } header: {
                    sectionTitle("COMES BACK")
                }
                .listRowBackground(Theme.card)
                Section {
                    Picker("Belongs to", selection: $owner) {
                        Text("Everyone").tag("")
                        ForEach(options?.people ?? []) { p in Text(p.name).tag(p.id) }
                    }
                    .font(AppFont.manrope(15, .bold))
                    Picker("Calendar", selection: $calendar) {
                        ForEach(calendars) { c in Text(c.name).tag(c.id) }
                    }
                    .font(AppFont.manrope(15, .bold))
                } header: {
                    sectionTitle("IN GCLOUD")
                } footer: {
                    if options != nil && calendars.isEmpty {
                        Text("Newman can't add tasks to any GCloud calendar yet.")
                            .font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                    }
                }
                .listRowBackground(Theme.card)
            }
            .scrollContentBackground(.hidden)
            .background(Theme.background)
            .navigationTitle("New task")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add") { add() }
                        .disabled(saving || calendar.isEmpty || name.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .task { await load() }
        }
        .preferredColorScheme(.dark)
    }

    private func sectionTitle(_ text: String) -> some View {
        Text(text).font(AppFont.manrope(12, .heavy)).kerning(1.2).foregroundColor(Theme.muted)
    }

    private func load() async {
        let o = (try? await APIClient.shared.taskCalendars()) ?? TaskCalendars()
        options = o
        if calendar.isEmpty { calendar = o.writable.first?.id ?? "" }
    }

    private func add() {
        saving = true
        Task {
            if await model.addTask(calendar: calendar, title: name.trimmingCharacters(in: .whitespaces), due: due,
                                   every: every, unit: unit, owner: owner) { dismiss() }
            saving = false
        }
    }
}
