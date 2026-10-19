import SwiftUI

/// Which device list the editor's picker is choosing for.
enum AutomationPick: Identifiable, Hashable {
    case trigger(Int)
    case newTrigger(String)
    case action(Int)
    case newAction
    case condition(Int)
    case newCondition

    var id: String { "\(self)" }
}

/// Make or change an automation on the phone: When, What happens, Only if and Don't repeat for, as
/// the web editor saves them. Parts the phone can't edit (types made by another editor, notifications,
/// follow-ups) are shown read-only and saved back exactly as they came. Accounts that can't change
/// automations see it read-only.
struct AutomationEditorScreen: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    /// nil = a new automation.
    let automationId: String?

    @State private var draft: AutomationDraft?
    @State private var original: AutomationDraft?
    @State private var described: AutomationDescription?
    @State private var picking: AutomationPick?
    @State private var saving = false
    @State private var running = false
    @State private var confirmDiscard = false
    @State private var confirmDelete = false

    private var editable: Bool { model.canAutomate }
    private var dirty: Bool { draft != nil && draft != original }

    var body: some View {
        ZStack {
            NightGlassBackground(teal: false)
            if let d = draft {
                content(d)
            } else {
                ProgressView().tint(Theme.text)
            }
        }
        .navigationBarBackButtonHidden(true)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button {
                    if dirty && editable { confirmDiscard = true } else { dismiss() }
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "chevron.left").font(.system(size: 16, weight: .bold))
                        Text("Automations").font(AppFont.manrope(15, .bold))
                    }
                    .foregroundColor(Theme.blue)
                }
                .accessibilityLabel("Back to Automations")
            }
            if editable && draft != nil {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { save() } label: {
                        if saving { ProgressView().tint(Theme.background) } else { Text("Save") }
                    }
                    .buttonStyle(PillButtonStyle(height: 38, prominent: true))
                    .disabled(saving || (!dirty && !(draft?.isNew ?? false)))
                }
            }
        }
        .confirmationDialog("Discard your changes?", isPresented: $confirmDiscard, titleVisibility: .visible) {
            Button("Discard", role: .destructive) { dismiss() }
            Button("Keep editing", role: .cancel) {}
        }
        .confirmationDialog("Delete \(draft?.name ?? "this automation")?", isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                guard let id = draft?.id else { return }
                Task { if await model.deleteAutomation(id) { dismiss() } }
            }
            Button("Cancel", role: .cancel) {}
        }
        .sheet(item: $picking) { target in
            pickerSheet(target).environmentObject(model)
        }
        .task { await load() }
        .task(id: draft) {
            // The hub's own sentence for it, a moment after each change.
            guard let d = draft else { return }
            try? await Task.sleep(nanoseconds: 500_000_000)
            if Task.isCancelled { return }
            if let desc = try? await APIClient.shared.describeAutomation(d.body), !Task.isCancelled { described = desc }
        }
    }

    private func load() async {
        guard draft == nil else { return }
        if let id = automationId {
            if let d = await model.loadAutomation(id) {
                draft = d
                original = d
            }
        } else {
            let d = AutomationDraft.new()
            draft = d
            original = d
        }
    }

    private func save() {
        guard let d = draft else { return }
        saving = true
        Task {
            if let saved = await model.saveAutomation(d) {
                draft = saved
                original = saved
                dismiss()
            }
            saving = false
        }
    }

    /// A binding into the draft (the editor only shows while it's loaded).
    private var binding: Binding<AutomationDraft> {
        Binding(get: { draft ?? AutomationDraft.new() }, set: { draft = $0 })
    }

    // MARK: layout

    private func content(_ d: AutomationDraft) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if !editable {
                    Text("Your account can't change automations.")
                        .font(AppFont.manrope(13, .bold)).foregroundColor(Theme.amber)
                }
                HStack(spacing: 12) {
                    TextField("Name", text: binding.name)
                        .font(AppFont.manrope(20, .heavy))
                        .foregroundColor(Theme.text)
                        .padding(.vertical, 8)
                        .overlay(alignment: .bottom) { Rectangle().fill(Theme.controlBorder).frame(height: 1) }
                        .accessibilityLabel("Name")
                    Toggle(isOn: binding.enabled) { EmptyView() }
                        .labelsHidden()
                        .tint(Theme.blue)
                        .accessibilityLabel(d.enabled ? "On" : "Off")
                }
                .padding(.horizontal, 4)
                .disabled(!editable)
                summary
                Group {
                    whenSection(d)
                    actionsSection(d)
                    conditionsSection(d)
                    cooldownSection(d)
                }
                .disabled(!editable)
                if editable, let id = d.id {
                    HStack(spacing: 10) {
                        Button {
                            running = true
                            Task {
                                await model.runAutomation(id: id, name: d.name)
                                running = false
                            }
                        } label: {
                            HStack(spacing: 8) {
                                if running { ProgressView().tint(Theme.text) }
                                Text("Run now")
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(PillButtonStyle(height: 46))
                        .disabled(running || dirty)
                        .accessibilityHint(dirty ? "Save first" : "Its conditions are still checked")
                        Button { confirmDelete = true } label: {
                            Text("Delete")
                                .font(AppFont.manrope(14, .heavy))
                                .foregroundColor(Theme.coralText)
                                .frame(maxWidth: .infinity, minHeight: 46)
                                .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Theme.coral.opacity(0.12)))
                                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Theme.coral.opacity(0.4)))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 32)
        }
    }

    private var summary: some View {
        let errors = described?.errors ?? []
        return VStack(alignment: .leading, spacing: 6) {
            Text(described?.summary ?? " ")
                .font(AppFont.manrope(13, .semibold)).foregroundColor(Theme.muted)
                .fixedSize(horizontal: false, vertical: true)
            ForEach(errors, id: \.self) { e in
                Text(e).font(AppFont.manrope(13, .bold)).foregroundColor(Theme.coralText)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Theme.control))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(Theme.cardBorder))
    }

    private func sectionTitle(_ text: String) -> some View {
        Text(text).font(AppFont.manrope(12, .heavy)).kerning(1.2).foregroundColor(Theme.muted)
            .accessibilityAddTraits(.isHeader)
    }

    private func addButton(_ text: String) -> some View {
        Text(text).font(AppFont.manrope(13, .bold)).foregroundColor(Theme.blue).frame(minHeight: 32)
    }

    // MARK: When

    private func whenSection(_ d: AutomationDraft) -> some View {
        let items = d.triggerItems
        return VStack(alignment: .leading, spacing: 10) {
            sectionTitle("WHEN")
            ForEach(Array(items.indices), id: \.self) { i in
                if i > 0 { Text("or").font(AppFont.manrope(12, .heavy)).foregroundColor(Theme.dim) }
                TriggerEditor(item: triggerBinding(i), canRemove: items.count > 1,
                              onChange: { picking = .trigger(i) },
                              onRemove: { removeTrigger(i) })
            }
            if editable {
                Menu {
                    Button("At a time") { appendTrigger(TriggerItem.newTime()) }
                    Button("When a device changes") { picking = .newTrigger("state") }
                    Button("When a device does something") { picking = .newTrigger("event") }
                    Button("When a reading goes above or below") { picking = .newTrigger("property") }
                } label: {
                    addButton(items.isEmpty ? "+ Add when it happens" : "+ Or when something else happens")
                }
            }
        }
        .glassCard(padding: 16)
    }

    private func triggerBinding(_ i: Int) -> Binding<TriggerItem> {
        Binding(
            get: {
                let items = draft?.triggerItems ?? []
                return i < items.count ? items[i] : TriggerItem.newTime()
            },
            set: { v in
                guard var d = draft else { return }
                var items = d.triggerItems
                guard i < items.count else { return }
                items[i] = v
                d.triggerItems = items
                draft = d
            })
    }

    private func appendTrigger(_ t: TriggerItem) {
        guard var d = draft else { return }
        var items = d.triggerItems
        items.append(t)
        d.triggerItems = items
        draft = d
    }

    private func removeTrigger(_ i: Int) {
        guard var d = draft else { return }
        var items = d.triggerItems
        guard i < items.count else { return }
        items.remove(at: i)
        d.triggerItems = items
        draft = d
    }

    // MARK: What happens

    private func actionsSection(_ d: AutomationDraft) -> some View {
        let items = d.actions
        return VStack(alignment: .leading, spacing: 10) {
            sectionTitle("WHAT HAPPENS")
            if items.isEmpty {
                Text("Nothing yet").font(AppFont.manrope(14, .semibold)).foregroundColor(Theme.muted)
            }
            ForEach(Array(items.indices), id: \.self) { i in
                ActionEditor(item: actionBinding(i),
                             onChange: { picking = .action(i) },
                             onRemove: { removeAction(i) })
            }
            if editable {
                Button { picking = .newAction } label: {
                    addButton(items.isEmpty ? "+ Do something" : "+ Do something else too")
                }
                .buttonStyle(.plain)
            }
        }
        .glassCard(padding: 16)
    }

    private func actionBinding(_ i: Int) -> Binding<ActionItem> {
        Binding(
            get: {
                let items = draft?.actions ?? []
                return i < items.count ? items[i] : ActionItem(raw: [:])
            },
            set: { v in
                guard var d = draft else { return }
                var items = d.actions
                guard i < items.count else { return }
                items[i] = v
                d.actions = items
                draft = d
            })
    }

    private func removeAction(_ i: Int) {
        guard var d = draft else { return }
        var items = d.actions
        guard i < items.count else { return }
        items.remove(at: i)
        d.actions = items
        draft = d
    }

    // MARK: Only if

    private func conditionsSection(_ d: AutomationDraft) -> some View {
        let rows = d.conditionDevices
        let others = d.otherConditions
        return VStack(alignment: .leading, spacing: 8) {
            sectionTitle("ONLY IF")
            if rows.isEmpty && others.isEmpty {
                Text("Always — no conditions").font(AppFont.manrope(14, .semibold)).foregroundColor(Theme.muted)
            }
            ForEach(Array(rows.indices), id: \.self) { i in
                ConditionEditor(row: conditionBinding(i),
                                onChange: { picking = .condition(i) },
                                onRemove: { removeCondition(i) })
            }
            ForEach(others, id: \.self) { o in
                HStack {
                    Text(o).font(AppFont.manrope(14, .bold)).foregroundColor(Theme.text)
                    Spacer()
                    Text("Edit on the web").font(AppFont.manrope(12, .bold)).foregroundColor(Theme.dim)
                }
                .frame(minHeight: 36)
            }
            if editable {
                Button { picking = .newCondition } label: {
                    addButton("+ Only if… (someone's home, a door is closed…)")
                }
                .buttonStyle(.plain)
            }
        }
        .glassCard(padding: 16)
    }

    private func conditionBinding(_ i: Int) -> Binding<ConditionDevice> {
        Binding(
            get: {
                let rows = draft?.conditionDevices ?? []
                return i < rows.count ? rows[i] : ConditionDevice(raw: [:])
            },
            set: { v in
                guard var d = draft else { return }
                var rows = d.conditionDevices
                guard i < rows.count else { return }
                rows[i] = v
                d.conditionDevices = rows
                draft = d
            })
    }

    private func removeCondition(_ i: Int) {
        guard var d = draft else { return }
        var rows = d.conditionDevices
        guard i < rows.count else { return }
        rows.remove(at: i)
        d.conditionDevices = rows
        draft = d
    }

    // MARK: Don't repeat for

    private func cooldownSection(_ d: AutomationDraft) -> some View {
        var choices = AutomationDraft.cooldownChoices
        if let m = d.cooldownMinutes, !choices.contains(m) { choices.append(m) }
        return HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                sectionTitle("DON'T REPEAT FOR")
                Text("After it runs, ignore new triggers for a while")
                    .font(AppFont.manrope(13, .semibold)).foregroundColor(Theme.muted)
            }
            Spacer(minLength: 8)
            Menu {
                ForEach(Array(choices.enumerated()), id: \.offset) { _, m in
                    Button(AutomationDraft.cooldownText(m)) { draft?.cooldownMinutes = m }
                }
            } label: {
                Text(AutomationDraft.cooldownText(d.cooldownMinutes) + " ▾")
                    .font(AppFont.manrope(14, .heavy)).foregroundColor(Theme.text)
                    .padding(.horizontal, 14)
                    .frame(height: 38)
                    .background(RoundedRectangle(cornerRadius: 12).fill(Theme.control))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.controlBorder))
            }
        }
        .glassCard(padding: 16)
    }

    // MARK: choosing devices

    @ViewBuilder private func pickerSheet(_ target: AutomationPick) -> some View {
        let kinds = model.kinds
        switch target {
        case .trigger(let i):
            let item = triggerBinding(i).wrappedValue
            AutomationDevicePicker(title: "Which device?", multiple: item.type != "event", chosen: item.entities,
                                   include: { AutomationEditorScreen.fits(type: item.type, kinds[$0.kind], $0) }) { ids in
                var t = triggerBinding(i).wrappedValue
                t.entities = ids
                let b = triggerBinding(i)
                b.wrappedValue = t
            }
        case .newTrigger(let type):
            AutomationDevicePicker(title: "Which device?", multiple: type != "event", chosen: [],
                                   include: { AutomationEditorScreen.fits(type: type, kinds[$0.kind], $0) }) { ids in
                addTrigger(type, ids)
            }
        case .action(let i):
            let item = actionBinding(i).wrappedValue
            AutomationDevicePicker(title: "Which devices?", multiple: true, chosen: item.entities,
                                   include: { !(kinds[$0.kind]?.commandList.isEmpty ?? true) }) { ids in
                var a = actionBinding(i).wrappedValue
                a.entities = ids
                let shared = AutomationEditorScreen.sharedCommands(ids, model: model)
                if !shared.contains(where: { $0.id == a.command }), let c = shared.first {
                    a.command = c.id
                    a.value = AutomationEditorScreen.defaultValue(c)
                }
                let b = actionBinding(i)
                b.wrappedValue = a
            }
        case .newAction:
            AutomationDevicePicker(title: "Which devices?", multiple: true, chosen: [],
                                   include: { !(kinds[$0.kind]?.commandList.isEmpty ?? true) }) { ids in
                addAction(ids)
            }
        case .condition(let i):
            let row = conditionBinding(i).wrappedValue
            AutomationDevicePicker(title: "Which devices?", multiple: true, chosen: row.entities,
                                   include: { !(kinds[$0.kind]?.stateList.isEmpty ?? true) }) { ids in
                var r = conditionBinding(i).wrappedValue
                r.entities = ids
                let b = conditionBinding(i)
                b.wrappedValue = r
            }
        case .newCondition:
            AutomationDevicePicker(title: "Which devices?", multiple: true, chosen: [],
                                   include: { !(kinds[$0.kind]?.stateList.isEmpty ?? true) }) { ids in
                addCondition(ids)
            }
        }
    }

    private func addTrigger(_ type: String, _ ids: [String]) {
        guard let first = ids.first else { return }
        let e = model.entity(first)
        var t: TriggerItem
        switch type {
        case "event":
            t = TriggerItem.newEvent(first, event: model.kind(of: e)?.eventList.first?.id ?? "")
        case "property":
            let p = AutomationEditorScreen.readings(e).first
            t = TriggerItem.newProperty(first, property: p?.id ?? "", value: p?.value?.doubleValue ?? 0)
            t.entities = ids
        default:
            t = TriggerItem.newState(first, to: "any")
            t.entities = ids
        }
        appendTrigger(t)
    }

    private func addAction(_ ids: [String]) {
        guard !ids.isEmpty, var d = draft else { return }
        let c = AutomationEditorScreen.sharedCommands(ids, model: model).first
        var a = ActionItem.new(ids, command: c?.id ?? "")
        if let c { a.value = AutomationEditorScreen.defaultValue(c) }
        var list = d.actions
        list.append(a)
        d.actions = list
        draft = d
    }

    private func addCondition(_ ids: [String]) {
        guard let first = ids.first, var d = draft else { return }
        let e = model.entity(first)
        var r = ConditionDevice.new(first, state: e?.state ?? model.kind(of: e)?.stateList.first?.id ?? "")
        r.entities = ids
        var rows = d.conditionDevices
        rows.append(r)
        d.conditionDevices = rows
        draft = d
    }

    /// Which devices a trigger of this type can watch (from kinds metadata).
    static func fits(type: String, _ kind: Kind?, _ e: Entity) -> Bool {
        switch type {
        case "event": return !(kind?.eventList.isEmpty ?? true)
        case "property": return !readings(e).isEmpty
        default: return !(kind?.stateList.isEmpty ?? true)
        }
    }

    /// A device's number readings (temperature, humidity, battery…).
    static func readings(_ e: Entity?) -> [DeviceProperty] {
        (e?.properties ?? []).filter { ($0.type ?? "number") == "number" }
    }

    /// The commands every chosen device can take (by the kinds metadata), in the first one's order.
    static func sharedCommands(_ ids: [String], model: AppModel) -> [Command] {
        let kinds = ids.compactMap { model.kind(of: model.entity($0)) }
        guard let first = kinds.first else { return [] }
        return first.commandList.filter { c in kinds.allSatisfy { $0.command(c.id) != nil } }
    }

    static func defaultValue(_ c: Command) -> JSONValue? {
        if let v = c.defaultValue, !v.isNull { return v }
        switch c.resolvedValueType {
        case "percent": return JSONValue.number(c.max ?? 100)
        case "number": return JSONValue.number(c.min ?? 0)
        default: return nil
        }
    }
}

// MARK: - the parts

private struct ItemBox<Content: View>: View {
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) { content() }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Theme.control))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(Theme.controlBorder))
    }
}

private struct SmallButton: View {
    let text: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(text).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                .padding(.horizontal, 12).frame(height: 32)
                .background(RoundedRectangle(cornerRadius: 10).fill(Theme.background.opacity(0.5)))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.controlBorder))
        }
        .buttonStyle(.plain)
    }
}

private struct RemoveButton: View {
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "xmark").font(.system(size: 12, weight: .bold)).foregroundColor(Theme.dim)
                .frame(width: 32, height: 32)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }
}

/// One choice in a ChoiceMenu.
struct EditorChoice: Hashable {
    var id: String
    var label: String
}

/// A menu that looks like the editor's other controls.
private struct ChoiceMenu: View {
    let text: String
    let choices: [EditorChoice]
    let pick: (String) -> Void

    var body: some View {
        Menu {
            ForEach(choices, id: \.self) { c in
                Button(c.label) { pick(c.id) }
            }
        } label: {
            Text(text + " ▾").font(AppFont.manrope(14, .heavy)).foregroundColor(Theme.text)
                .padding(.horizontal, 12).frame(height: 36)
                .background(RoundedRectangle(cornerRadius: 10).fill(Theme.background.opacity(0.5)))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.controlBorder))
        }
    }
}

@MainActor
private enum EditorText {
    static func names(_ ids: [String], _ model: AppModel) -> String {
        if ids.isEmpty { return "Choose a device" }
        let names = ids.map { model.entity($0)?.name ?? $0 }
        return names.count <= 2 ? names.joined(separator: " or ") : "\(names[0]) and \(names.count - 1) more"
    }

    /// "Group · 3 lights" for one group.
    static func group(_ ids: [String], _ model: AppModel) -> String? {
        guard ids.count == 1, let e = model.entity(ids[0]) else { return nil }
        let n = LightRooms.members(e).count
        return n > 0 ? "Group · \(n) lights" : nil
    }

    static let forChoices: [Int?] = [nil, 1, 5, 10, 15, 30, 60]

    static func forText(_ m: Int?) -> String {
        guard let m else { return "Right away" }
        return m == 60 ? "For 1 hour" : "For \(m) min"
    }
}

private struct DeviceLine: View {
    @EnvironmentObject var model: AppModel
    let ids: [String]
    let onChange: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 1) {
                Text(EditorText.names(ids, model)).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                if let g = EditorText.group(ids, model) {
                    Text(g).font(AppFont.manrope(12, .semibold)).foregroundColor(Theme.muted)
                }
            }
            Spacer(minLength: 6)
            SmallButton(text: "Change", action: onChange)
        }
    }
}

struct TriggerEditor: View {
    @EnvironmentObject var model: AppModel
    @Binding var item: TriggerItem
    let canRemove: Bool
    let onChange: () -> Void
    let onRemove: () -> Void

    var body: some View {
        ItemBox {
            switch item.type {
            case "time": timeEditor
            case "state": stateEditor
            case "event": eventEditor
            case "property": propertyEditor
            default: unknownEditor
            }
        }
    }

    private var unknownEditor: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(DeviceLogic.humanize(item.type)).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                Text("Edit on the web · kept as it is").font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
            }
            Spacer()
            if canRemove { RemoveButton(label: "Remove", action: onRemove) }
        }
    }

    private var kinds: [Kind] { item.entities.compactMap { model.kind(of: model.entity($0)) } }

    private var timeBinding: Binding<Date> {
        Binding(
            get: {
                let hm = TriggerItem.hourMinute(item.time) ?? (7, 0)
                return Calendar.current.date(bySettingHour: hm.0, minute: hm.1, second: 0, of: Date()) ?? Date()
            },
            set: { d in
                let c = Calendar.current.dateComponents([.hour, .minute], from: d)
                item.time = TriggerItem.hhmm(c.hour ?? 0, c.minute ?? 0)
            })
    }

    private var timeEditor: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Text("At a time").font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                Spacer()
                DatePicker("Time", selection: timeBinding, displayedComponents: .hourAndMinute)
                    .labelsHidden()
                    .environment(\.colorScheme, .dark)
                if canRemove { RemoveButton(label: "Remove this time", action: onRemove) }
            }
            HStack(spacing: 6) {
                ForEach(TriggerItem.displayDays, id: \.self) { day in
                    dayChip(day)
                }
            }
            Text(TriggerItem.daysText(item.days)).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
        }
    }

    private func dayChip(_ day: Int) -> some View {
        let on = item.days.contains(day)
        return Button {
            var d = item.days
            if on { d.removeAll { $0 == day } } else { d.append(day) }
            item.days = d
        } label: {
            Text(TriggerItem.dayLetters[day])
                .font(AppFont.manrope(13, .heavy))
                .foregroundColor(on ? Theme.text : Theme.dim)
                .frame(maxWidth: .infinity, minHeight: 36)
                .background(Capsule().fill(on ? Theme.blue.opacity(0.35) : Color.clear))
                .overlay(Capsule().stroke(on ? Color.clear : Theme.controlBorder))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(TriggerItem.dayNames[day])
        .accessibilityValue(on ? "On" : "Off")
    }

    private var stateChoices: [EditorChoice] {
        var out = [EditorChoice(id: "any", label: "Anything")]
        for k in kinds {
            for s in k.stateList where !out.contains(where: { $0.id == s.id }) {
                out.append(EditorChoice(id: s.id, label: s.label))
            }
        }
        return out
    }

    private var stateEditor: some View {
        let choices = stateChoices
        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                DeviceLine(ids: item.entities, onChange: onChange)
                if canRemove { RemoveButton(label: "Remove", action: onRemove) }
            }
            HStack(spacing: 8) {
                Text("changes to").font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted)
                ChoiceMenu(text: choices.first { $0.id == item.to }?.label ?? DeviceLogic.humanize(item.to),
                           choices: choices) { item.to = $0 }
                Spacer(minLength: 0)
            }
            forRow
        }
    }

    private var eventEditor: some View {
        let events = kinds.first?.eventList ?? []
        let choices = events.map { EditorChoice(id: $0.id, label: $0.label ?? DeviceLogic.humanize($0.id)) }
        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                DeviceLine(ids: item.entities, onChange: onChange)
                if canRemove { RemoveButton(label: "Remove", action: onRemove) }
            }
            HStack(spacing: 8) {
                Text("does").font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted)
                ChoiceMenu(text: choices.first { $0.id == item.event }?.label ?? DeviceLogic.humanize(item.event),
                           choices: choices) { item.event = $0 }
                Spacer(minLength: 0)
            }
        }
    }

    private var valueText: Binding<String> {
        Binding(
            get: { JSONValue.format(item.value, maxDecimals: 2) },
            set: { s in
                if let v = Double(s.replacingOccurrences(of: ",", with: ".")) { item.value = v }
            })
    }

    private var propertyEditor: some View {
        let readings = AutomationEditorScreen.readings(item.entities.first.flatMap { model.entity($0) })
        let current = readings.first { $0.id == item.property }
        let choices = readings.map { EditorChoice(id: $0.id, label: $0.label ?? DeviceLogic.humanize($0.id)) }
        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                DeviceLine(ids: item.entities, onChange: onChange)
                if canRemove { RemoveButton(label: "Remove", action: onRemove) }
            }
            HStack(spacing: 8) {
                ChoiceMenu(text: current?.label ?? DeviceLogic.humanize(item.property), choices: choices) { item.property = $0 }
                ChoiceMenu(text: item.op == "below" ? "goes below" : "goes above",
                           choices: [EditorChoice(id: "above", label: "goes above"),
                                     EditorChoice(id: "below", label: "goes below")]) { item.op = $0 }
                Spacer(minLength: 0)
            }
            HStack(spacing: 8) {
                TextField("Value", text: valueText)
                    .keyboardType(.decimalPad)
                    .font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                    .padding(.horizontal, 10).frame(width: 90, height: 36)
                    .background(RoundedRectangle(cornerRadius: 10).fill(Theme.background.opacity(0.5)))
                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.controlBorder))
                    .accessibilityLabel("Value")
                if let u = current?.unit, !u.isEmpty {
                    Text(u).font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted)
                }
                Spacer(minLength: 0)
            }
            forRow
        }
    }

    private var forRow: some View {
        let choices = EditorText.forChoices.map { m in
            EditorChoice(id: m.map { String($0) } ?? "", label: EditorText.forText(m))
        }
        return HStack(spacing: 8) {
            ChoiceMenu(text: EditorText.forText(item.forMinutes), choices: choices) { s in
                item.forMinutes = Int(s)
            }
            Spacer(minLength: 0)
        }
    }
}

struct ActionEditor: View {
    @EnvironmentObject var model: AppModel
    @Binding var item: ActionItem
    let onChange: () -> Void
    let onRemove: () -> Void

    var body: some View {
        ItemBox {
            if item.isEditable {
                editor
            } else {
                unknown
            }
        }
    }

    private var unknown: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(item.raw["type"]?.stringValue.map { DeviceLogic.humanize($0) } ?? "Something set on the web")
                    .font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                Text("Edit on the web · kept as it is").font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
            }
            Spacer()
            RemoveButton(label: "Remove", action: onRemove)
        }
    }

    private var editor: some View {
        let commands = AutomationEditorScreen.sharedCommands(item.entities, model: model)
        let command = commands.first { $0.id == item.command }
        let choices = commands.map { EditorChoice(id: $0.id, label: $0.label) }
        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                DeviceLine(ids: item.entities, onChange: onChange)
                RemoveButton(label: "Remove", action: onRemove)
            }
            HStack(spacing: 8) {
                ChoiceMenu(text: command?.label ?? DeviceLogic.humanize(item.command), choices: choices) { id in
                    pickCommand(id, commands)
                }
                Spacer(minLength: 0)
            }
            if let command { valueControl(command) }
            if command?.resolvedValueType == "percent" || item.forMinutes != nil { putBackRow }
        }
    }

    private func pickCommand(_ id: String, _ commands: [Command]) {
        guard id != item.command, let c = commands.first(where: { $0.id == id }) else { return }
        var a = item
        a.command = id
        a.value = AutomationEditorScreen.defaultValue(c)
        if c.resolvedValueType != "percent" { a.forMinutes = nil }
        item = a
    }

    @ViewBuilder private func valueControl(_ c: Command) -> some View {
        switch c.resolvedValueType {
        case "percent", "number":
            numberControl(c)
        case "state":
            stateControl(c)
        case "":
            EmptyView()
        default:
            TextField("Value", text: Binding(
                get: { item.value?.displayText ?? "" },
                set: { item.value = JSONValue.string($0) }))
                .font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                .padding(.horizontal, 10).frame(height: 36)
                .background(RoundedRectangle(cornerRadius: 10).fill(Theme.background.opacity(0.5)))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.controlBorder))
                .accessibilityLabel(c.label)
        }
    }

    private func numberControl(_ c: Command) -> some View {
        let percent = c.resolvedValueType == "percent"
        let lo = c.min ?? (percent ? 1 : 0)
        let hi = Swift.max(c.max ?? 100, lo + 1)
        let step = Swift.max(c.step ?? 1, 0.01)
        let unit = percent ? "%" : (c.unit ?? "")
        let current = Swift.min(Swift.max(item.value?.doubleValue ?? lo, lo), hi)
        return HStack(spacing: 10) {
            Slider(value: Binding(
                get: { current },
                set: { item.value = JSONValue.number(($0 / step).rounded() * step) }),
                   in: lo...hi)
                .tint(Theme.amber)
                .accessibilityLabel(c.label)
            Text(JSONValue.format(current, maxDecimals: 1) + unit)
                .font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.amber)
                .frame(minWidth: 48, alignment: .trailing)
        }
    }

    private func stateControl(_ c: Command) -> some View {
        let states = model.kind(of: item.entities.first.flatMap { model.entity($0) })?.stateList ?? []
        let choices = states.map { EditorChoice(id: $0.id, label: $0.label) }
        let value = item.value?.stringValue
        return HStack {
            ChoiceMenu(text: choices.first { $0.id == value }?.label ?? (value ?? "Choose"), choices: choices) {
                item.value = JSONValue.string($0)
            }
            Spacer(minLength: 0)
        }
    }

    private var putBackRow: some View {
        VStack(spacing: 8) {
            Divider().overlay(Theme.controlBorder)
            Toggle(isOn: Binding(
                get: { item.forMinutes != nil },
                set: { item.forMinutes = $0 ? (item.forMinutes ?? 30) : nil })) {
                Text("Then put it back after").font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted)
            }
            .tint(Theme.blue)
            if let m = item.forMinutes {
                HStack(spacing: 10) {
                    Spacer()
                    stepButton("−", label: "Fewer minutes") { item.forMinutes = Swift.max(1, m - 5) }
                    Text("\(m) min").font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text).frame(minWidth: 70)
                    stepButton("+", label: "More minutes") { item.forMinutes = Swift.min(24 * 60, m + 5) }
                }
            }
        }
    }

    private func stepButton(_ text: String, label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(text).font(.system(size: 18, weight: .bold)).foregroundColor(Theme.text)
                .frame(width: 36, height: 36)
                .background(Circle().fill(Theme.background.opacity(0.5)))
                .overlay(Circle().stroke(Theme.controlBorder))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }
}

struct ConditionEditor: View {
    @EnvironmentObject var model: AppModel
    @Binding var row: ConditionDevice
    let onChange: () -> Void
    let onRemove: () -> Void

    var body: some View {
        let states = row.entities.compactMap { model.kind(of: model.entity($0)) }.first?.stateList ?? []
        let choices = states.map { EditorChoice(id: $0.id, label: $0.label) }
        return ItemBox {
            HStack(alignment: .top) {
                DeviceLine(ids: row.entities, onChange: onChange)
                RemoveButton(label: "Remove", action: onRemove)
            }
            HStack(spacing: 8) {
                Text("is").font(AppFont.manrope(14, .bold)).foregroundColor(Theme.muted)
                ChoiceMenu(text: choices.first { $0.id == row.state }?.label ?? DeviceLogic.humanize(row.state),
                           choices: choices) { row.state = $0 }
                Spacer(minLength: 0)
            }
        }
    }
}

/// Choose one or more devices (and groups) for the editor, searched by name.
struct AutomationDevicePicker: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let title: String
    let multiple: Bool
    let include: (Entity) -> Bool
    let onDone: ([String]) -> Void
    @State private var selected: [String]
    @State private var query = ""

    init(title: String, multiple: Bool, chosen: [String], include: @escaping (Entity) -> Bool,
         onDone: @escaping ([String]) -> Void) {
        self.title = title
        self.multiple = multiple
        self.include = include
        self.onDone = onDone
        _selected = State(initialValue: chosen)
    }

    var body: some View {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        let list = model.allEntities
            .filter { include($0) && (q.isEmpty || $0.name.lowercased().contains(q)) }
            .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
        return NavigationStack {
            List(list) { e in
                row(e)
            }
            .scrollContentBackground(.hidden)
            .background(Theme.background)
            .searchable(text: $query, prompt: "Search devices")
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                if multiple {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Done") {
                            onDone(selected)
                            dismiss()
                        }
                        .disabled(selected.isEmpty)
                    }
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private func row(_ e: Entity) -> some View {
        let on = selected.contains(e.id)
        return Button {
            if multiple {
                if on { selected.removeAll { $0 == e.id } } else { selected.append(e.id) }
            } else {
                onDone([e.id])
                dismiss()
            }
        } label: {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(e.name).font(AppFont.manrope(15, .heavy)).foregroundColor(Theme.text)
                    if let room = LightRooms.room(e) {
                        Text(room).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                    }
                }
                Spacer()
                if on {
                    Image(systemName: "checkmark").font(.system(size: 15, weight: .bold)).foregroundColor(Theme.blue)
                }
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .listRowBackground(Theme.card)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}
