import Foundation

// How a device reads and what it offers, worked out only from /api/meta (the kind's states,
// commands, activeState, secureState, transitional…). A port of web/src/model.js: no screen
// here knows about a particular kind of device.

enum DeviceLogic {
    // MARK: state

    static func isActive(_ e: Entity, _ kind: Kind?) -> Bool {
        guard let active = kind?.activeState else { return false }
        return e.state == active
    }

    static func stateLabel(_ kind: Kind?, _ stateId: String?) -> String {
        guard let stateId else { return "" }
        return kind?.stateList.first { $0.id == stateId }?.label ?? stateId
    }

    /// A device's state as the hub words it (entity `stateLabel`: the kind's notes, e.g. a wall
    /// tablet's "Screen off", else its state's label); worked out from the kind only when the hub
    /// sent none (older hubs).
    static func stateLabel(_ e: Entity, _ kind: Kind?) -> String {
        if let l = e.stateLabel, !l.isEmpty { return l }
        return stateLabel(kind, e.state)
    }

    static func activeLabel(_ kind: Kind?) -> String {
        guard let kind, let a = kind.activeState else { return "" }
        return stateLabel(kind, a)
    }

    static func isTransitional(_ e: Entity, _ kind: Kind?) -> Bool {
        guard let kind, let s = e.state else { return false }
        return (kind.transitional ?? []).contains(s)
    }

    /// Worth a look: the kind has a secure state and the device settled somewhere else.
    static func notSecure(_ e: Entity, _ kind: Kind?) -> Bool {
        guard let kind, let secure = kind.secureState, e.available != false, let s = e.state, !s.isEmpty else { return false }
        return s != secure && !isTransitional(e, kind)
    }

    static func isMode(_ kind: Kind?) -> Bool { kind?.isMode ?? false }

    /// "warn" away from the secure state or with a problem, "calm" in it, "" for kinds without one.
    enum Tone: String { case warn, calm, none }

    static func tone(_ e: Entity, _ kind: Kind?, problem: String?) -> Tone {
        if problem != nil || notSecure(e, kind) { return .warn }
        return kind?.secureState != nil ? .calm : .none
    }

    /// Lit like a light that's on: active, and not a secure/insecure kind.
    static func isLit(_ e: Entity, _ kind: Kind?) -> Bool {
        isActive(e, kind) && kind?.secureState == nil
    }

    // MARK: events / remotes

    static func eventsFor(_ e: Entity, _ kind: Kind?) -> [KindEvent] {
        let all = kind?.eventList ?? []
        guard let own = e.attr("events")?.arrayValue?.compactMap({ $0.stringValue }) else { return all }
        let unknown = own.filter { id in !all.contains { $0.id == id } }
            .map { KindEvent(id: $0, label: humanize($0)) }
        return all.filter { own.contains($0.id) } + unknown
    }

    static func isRemote(_ e: Entity, _ kind: Kind?) -> Bool {
        guard e.attr("events")?.arrayValue != nil else { return false }
        return (kind?.commandList ?? []).isEmpty && (kind?.stateList.count ?? 0) <= 1
    }

    static func shortEventLabel(_ label: String) -> String {
        let s = label
        // "“Button 1” is pressed" / "“Button 1” is long-pressed" -> "Button 1"
        if let open = s.first, (open == "“" || open == "\""), s.lowercased().hasSuffix("pressed") {
            let body = s.index(after: s.startIndex)
            if let close = s[body...].firstIndex(where: { $0 == "”" || $0 == "\"" }), close > body,
               s[s.index(after: close)...].lowercased().hasPrefix(" is ") {
                return String(s[body..<close])
            }
        }
        for suffix in [" pressed", " detected", " entered", " comes in"] {
            if s.lowercased().hasSuffix(suffix) {
                let out = String(s.dropLast(suffix.count))
                return out.isEmpty ? label : out
            }
        }
        return s
    }

    static func countLabel(_ label: String, _ n: Int) -> String {
        let plural = ["person": "people", "bus": "buses", "license_plate": "license plates", "face": "faces", "package": "packages"]
        let name = label.replacingOccurrences(of: "_", with: " ")
        return "\(n) \(n == 1 ? name : (plural[label] ?? name + "s"))"
    }

    /// Non-zero detection counts as ["1 person", "2 cars"].
    static func countList(_ counts: [String: JSONValue]?) -> [String] {
        guard let counts else { return [] }
        return counts.keys.sorted().compactMap { k in
            guard let n = counts[k]?.doubleValue, n > 0 else { return nil }
            return countLabel(k, Int(n))
        }
    }

    // MARK: state text

    static func fmtTemp(_ v: Double) -> String { String(format: "%.1f", (v * 10).rounded() / 10) }

    /// Every state in plain words (model.js entityStateText):
    /// lock "Locked" · light "On · 60%" · garage "Opening…" · thermostat "Cool · 22.6 °C → 22.5".
    static func stateText(_ e: Entity, _ kind: Kind?, long: Bool = false) -> String {
        if !e.isAvailable { return "Offline" }
        let hubLabel = stateLabel(e, kind)
        let label = hubLabel.isEmpty ? "—" : hubLabel

        if isRemote(e, kind) {
            var names: [String] = []  // a long press is the same button
            for n in eventsFor(e, kind).map({ shortEventLabel($0.label ?? $0.id) }) where !names.contains(n) { names.append(n) }
            let allPress = eventsFor(e, kind).allSatisfy { ($0.label ?? "").lowercased().contains("pressed") }
            let word = allPress ? "button" : "event"
            if names.isEmpty { return "No \(word)s" }
            if long || names.count <= 2 { return word.prefix(1).uppercased() + word.dropFirst() + "s: " + names.joined(separator: ", ") }
            return "\(names.count) \(word)s (\(names.prefix(2).joined(separator: ", ")), …)"
        }
        if let counts = e.attr("counts")?.objectValue {
            let list = countList(counts)
            let act = activeLabel(kind)
            if list.isEmpty { return act.isEmpty ? "Clear" : label }
            return (act.isEmpty ? list : [act] + list).joined(separator: " · ")
        }
        if let current = e.attr("currentTemp")?.doubleValue, e.attr("currentTemp")?.stringValue == nil {
            let unit = e.attr("unit")?.stringValue ?? kind?.unit.flatMap { $0.isEmpty ? nil : $0 } ?? "°C"
            let now = "\(fmtTemp(current)) \(unit)"
            if e.state == "off" { return "\(label) · \(now)" }
            guard let target = e.attr("targetTemp")?.doubleValue else { return "\(label) · \(now)" }
            return "\(label) · \(now) → \(fmtTemp(target))"
        }
        let reading = readingText(e, kind)
        if !reading.isEmpty { return reading }
        if let kind, kind.stateList.isEmpty, !kind.commandList.isEmpty, (e.state ?? "").isEmpty { return "" }
        if isTransitional(e, kind) {
            var l = label
            if l.hasSuffix("…") { l.removeLast() }
            return l + "…"
        }
        if let kind, let s = e.state, kind.eventList.contains(where: { $0.id == s }) { return "\(label) just now" }
        if let track = e.attr("track")?.stringValue, !track.isEmpty {
            let artist = e.attr("artist")?.stringValue ?? ""
            return "\(label) · \(track)" + (artist.isEmpty ? "" : " — \(artist)")
        }
        if let b = e.attr("brightness")?.doubleValue, e.attr("brightness")?.stringValue == nil {
            let on = kind?.activeState != nil ? e.state == kind?.activeState : e.state != "off"
            if on && e.attr("dimmable")?.boolValue != false { return "\(label) · \(Int(b.rounded()))%" }
        }
        return label
    }

    /// For kinds without states whose state is a number: "Humidity · 64 %".
    static func readingText(_ e: Entity, _ kind: Kind?, withLabel: Bool = true) -> String {
        guard let kind, kind.stateList.isEmpty, let raw = e.state, let v = Double(raw) else { return "" }
        let prop = (e.properties ?? []).first { ($0.attr ?? "").isEmpty }
        var unit = e.attr("unitOfMeasurement")?.stringValue ?? ""
        if unit.isEmpty { unit = prop?.unit ?? "" }
        if unit.isEmpty { unit = kind.unit ?? "" }
        let num = abs(v) >= 100 ? JSONValue.format(v.rounded()) : JSONValue.format((v * 10).rounded() / 10)
        let text = unit.isEmpty ? num : "\(num) \(unit)"
        if withLabel, let cls = e.attr("deviceClass")?.stringValue, !cls.isEmpty {
            let c = cls.replacingOccurrences(of: "_", with: " ")
            return c.prefix(1).uppercased() + c.dropFirst() + " · " + text
        }
        return text
    }

    // MARK: commands

    static func plainCommands(_ kind: Kind?) -> [Command] {
        (kind?.commandList ?? []).filter { !($0.takesValue ?? false) }
    }

    static func valueCommands(_ kind: Kind?) -> [Command] {
        (kind?.commandList ?? []).filter { $0.takesValue ?? false }
    }

    static func choiceCommands(_ kind: Kind?) -> [Command] {
        (kind?.commandList ?? []).filter { $0.resolvedValueType == "choices" }
    }

    /// The action a list row offers: a command that isn't the current state (Lock/Unlock).
    static func nextAction(_ e: Entity, _ kind: Kind?) -> Command? {
        let plain = plainCommands(kind)
        if let c = plain.first(where: { ($0.confirmsTo ?? "").isEmpty == false && $0.confirmsTo != e.state }) { return c }
        return plain.count == 1 ? plain[0] : nil
    }

    /// Tap on a device card: its on/off pair, else the next action.
    static func tapAction(_ e: Entity, _ kind: Kind?) -> Command? {
        let plain = plainCommands(kind)
        if let kind, let active = kind.activeState, plain.count == 2, !plain.contains(where: { $0.isSensitive }) {
            let on = plain.first { $0.confirmsTo == active }
            let off = plain.first { $0 != on && !($0.confirmsTo ?? "").isEmpty }
            if let on, let off { return isActive(e, kind) ? off : on }
        }
        return nextAction(e, kind)
    }

    /// A start/stop duo among several buttons: only the one that makes sense now is shown.
    static func toggleDuo(_ kind: Kind?, _ plain: [Command]) -> (on: Command, off: Command)? {
        guard let kind, let active = kind.activeState, plain.count > 2 else { return nil }
        if kind.commandList.contains(where: { $0.resolvedValueType == "percent" }) { return nil }
        guard let on = plain.first(where: { $0.confirmsTo == active }),
              let off = plain.first(where: { $0 != on && !($0.confirmsTo ?? "").isEmpty && $0.confirmsTo != active }) else { return nil }
        return (on, off)
    }

    /// Actions beyond the main tap: only for devices with more than an on/off pair.
    static func extraActions(_ e: Entity, _ kind: Kind?, main: Command?) -> [Command] {
        let plain = plainCommands(kind)
        let choices = choiceCommands(kind)
        if plain.count <= 2 && choices.isEmpty { return [] }
        let duo = toggleDuo(kind, plain)
        let rest = plain.filter { c in c != main && (duo == nil || (c != duo!.on && c != duo!.off)) }
        return rest + choices
    }

    /// The states a "state" command can set: the kind's states without the momentary ones.
    static func stateChoices(_ kind: Kind?) -> [KindState] {
        let tr = kind?.transitional ?? []
        return (kind?.stateList ?? []).filter { !tr.contains($0.id) }
    }

    /// A value command's current value: attrs[cmd.attr]; a "state" command without an attr uses the state.
    static func commandCurrent(_ e: Entity, _ cmd: Command) -> JSONValue? {
        if let a = cmd.attr, !a.isEmpty { return e.attr(a) }
        if cmd.resolvedValueType == "state", let s = e.state { return .string(s) }
        return nil
    }

    struct ChoiceItem: Hashable, Identifiable {
        var id: JSONValue
        var name: String
    }

    /// What a "choices" command can pick from: attrs[cmd.attr] as [{id, name}].
    static func choiceItems(_ e: Entity, _ cmd: Command) -> [ChoiceItem] {
        guard let a = cmd.attr, let list = e.attr(a)?.arrayValue else { return [] }
        return list.compactMap { x in
            guard let id = x["id"], !id.isNull else { return nil }
            let name = x["name"]?.stringValue ?? id.displayText
            return ChoiceItem(id: id, name: name.isEmpty ? id.displayText : name)
        }
    }

    /// "80%", "21.5 °C", "Heat", "Kitchen and Hall" for a command value.
    static func valueText(_ cmd: Command, _ kind: Kind?, _ v: JSONValue?, entity: Entity? = nil) -> String {
        if cmd.resolvedValueType == "choices" {
            let ids: [JSONValue]
            if let a = v?.arrayValue { ids = a } else if let v, !v.isNull, v.displayText != "" { ids = [v] } else { ids = [] }
            if ids.isEmpty { return "nothing chosen" }
            let items = entity.map { choiceItems($0, cmd) } ?? []
            let names = ids.map { id in items.first { $0.id.displayText == id.displayText }?.name ?? id.displayText }
            if names.count <= 3 { return names.joined(separator: names.count == 2 ? " and " : ", ") }
            return "\(names.prefix(2).joined(separator: ", ")) and \(names.count - 2) more"
        }
        guard let v, !v.isNull, v.displayText != "" else { return "?" }
        if cmd.resolvedValueType == "state" { return stateLabel(kind, v.displayText) }
        let u = cmd.unit ?? ""
        if u.isEmpty { return v.displayText }
        return u == "%" ? "\(v.displayText)%" : "\(v.displayText) \(u)"
    }

    // MARK: sensitive

    /// Unlock, disarm, open the garage, sound a siren: the phone confirms it's you first
    /// (unless the person turned that off in Me).
    static func needsAuthentication(_ cmd: Command, biometricsEnabled: Bool) -> Bool {
        cmd.isSensitive && biometricsEnabled
    }

    // MARK: readings

    /// A reading for display (automation.js formatPropValue).
    static func formatProp(_ p: DeviceProperty?, _ v: JSONValue?) -> String {
        guard let v, !v.isNull, v.displayText != "" else { return "—" }
        guard let p else { return v.displayText }
        switch p.type ?? "" {
        case "number":
            let num: String
            if let n = v.doubleValue { num = JSONValue.format((n * 100).rounded() / 100) } else { num = v.displayText }
            let u = p.unit ?? ""
            if u.isEmpty { return num }
            return u == "%" ? "\(num)%" : "\(num) \(u)"
        case "boolean":
            if let b = v.boolValue { return b ? "yes" : "no" }
            return ["true", "1", "yes", "on"].contains(v.displayText.lowercased()) ? "yes" : "no"
        default:
            if let o = (p.options ?? []).first(where: { $0.value?.displayText == v.displayText }), let l = o.label { return l }
            let s = v.displayText.replacingOccurrences(of: "_", with: " ")
            return s.prefix(1).uppercased() + s.dropFirst()
        }
    }

    /// Battery % and a reported problem, from the device's properties (any kind).
    static func vitals(_ e: Entity) -> (battery: Int?, problem: String?) {
        let props = e.properties ?? []
        var battery: Int?
        if let b = props.first(where: { $0.id == "battery" })?.value, b.stringValue == nil, let n = b.doubleValue {
            battery = Int(n.rounded())
        }
        var problem: String?
        if let p = props.first(where: { $0.id == "problem" })?.value, !p.isNull, p.displayText != "" { problem = p.displayText }
        return (battery, problem)
    }

    static func humanize(_ key: String) -> String {
        var out = ""
        var prev: Character?
        for ch in key {
            if ch.isUppercase, let p = prev, p.isLowercase || p.isNumber { out.append(" ") }
            out.append(ch == "_" || ch == "." ? " " : ch)
            prev = ch
        }
        let words = out.split(separator: " ").joined(separator: " ").lowercased()
        return words.prefix(1).uppercased() + words.dropFirst()
    }

    // MARK: icons

    /// The icon name for a kind, from its metadata only: a camera, its first command's icon, its own id
    /// when that's an icon name, a secure state, events, a measurement unit - else its id (drawn with the
    /// neutral symbol). Never by category: a new category or kind needs no change here. (Names match
    /// web/src/icons.jsx; IconNames maps them to SF Symbols.)
    static func kindIcon(_ kind: Kind?) -> String {
        guard let kind else { return "dot" }
        if kind.hasCamera ?? false { return "camera" }
        if let c = kind.commandList.first(where: { !($0.icon ?? "").isEmpty }), let icon = c.icon { return icon }
        if IconNames.knows(kind.id) { return kind.id }
        if kind.secureState != nil { return "shield" }
        if !kind.eventList.isEmpty { return "bell" }
        if !(kind.unit ?? "").isEmpty { return "thermometer" }
        return kind.id
    }

    /// The icon a device is drawn with: the one set on it in Newman (an icon name, or a kind's id - that
    /// kind's icon), else its kind's.
    static func icon(_ e: Entity, _ kind: Kind?, kinds: [String: Kind] = [:]) -> String {
        if let set = e.icon?.trimmingCharacters(in: .whitespaces), !set.isEmpty {
            if let other = kinds[set] { return kindIcon(other) }
            return set
        }
        return kindIcon(kind)
    }

    // MARK: optimistic updates

    /// What the device will look like once a command lands, for an optimistic UI:
    /// a plain command's confirmsTo state; a value command's attr (or state).
    static func optimistic(_ e: Entity, _ kind: Kind?, _ cmd: Command, value: JSONValue?) -> Entity {
        var out = e
        if let value, cmd.needsValue {
            if let a = cmd.attr, !a.isEmpty {
                var attrs = out.attrs ?? [:]
                attrs[a] = value
                out.attrs = attrs
                if let target = cmd.confirmsTo, !target.isEmpty {
                    out.state = target
                    out.stateLabel = stateLabel(kind, target)
                }
            } else if cmd.resolvedValueType == "state", let s = value.stringValue {
                out.state = s
                out.stateLabel = stateLabel(kind, s)
            }
        } else if let target = cmd.confirmsTo, !target.isEmpty {
            out.state = target
            out.stateLabel = stateLabel(kind, target)
        }
        return out
    }
}
