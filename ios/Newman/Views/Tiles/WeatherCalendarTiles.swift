import SwiftUI

/// Weather now (icon, temperature, H/L, rain), plus the week ahead or the next hours when wide.
/// The place is the tile's device, else any device reporting a forecast.
struct WeatherTile: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.tileScale) var scale
    let tile: Tile
    let wide: Bool
    @State private var alertsOpen = false

    var body: some View {
        Group {
            if let e = WeatherModel.pick(tile: tile, entities: model.allEntities) {
                let w = WeatherModel(entity: e, kind: model.kind(of: e))
                if w.temperature == nil {
                    Text(e.isAvailable ? "Waiting for the weather…" : "Weather unavailable right now.")
                        .font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted)
                } else if oneRow {
                    row(w)
                } else {
                    content(w)
                }
            } else {
                Text("Add a Weather connection in the Newman dashboard.").font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted)
            }
        }
        .glassCard(padding: oneRow ? 14 : 12)
    }

    /// The whole width of the phone but one row of cells high: everything on one line.
    private var oneRow: Bool {
        wide && model.phoneLayout != nil && PhoneGrid.clamp(tile).h <= 1
    }

    /// Icon, the temperature, the sky and today's high and low; rain or snow on the right.
    private func row(_ w: WeatherModel) -> some View {
        HStack(spacing: 12) {
            Image(systemName: Symbols.weather(w.condition, isDay: w.isDay)).font(.system(size: 30)).foregroundColor(Color(hex: 0xC9D4F5))
                .accessibilityHidden(true)
            Text(WeatherModel.deg(w.temperature)).font(AppFont.sora(30 * scale)).foregroundColor(Theme.text)
                .lineLimit(1).layoutPriority(1)
            VStack(alignment: .leading, spacing: 1) {
                Text(w.label).font(AppFont.manrope(14 * scale, .heavy)).foregroundColor(Theme.text).lineLimit(1)
                Text("H \(WeatherModel.deg(w.high)) · L \(WeatherModel.deg(w.low))").font(AppFont.manrope(12 * scale, .bold))
                    .foregroundColor(Theme.muted).lineLimit(1)
            }
            .minimumScaleFactor(0.8)
            Spacer(minLength: 4)
            if !w.alerts.isEmpty {
                Button { alertsOpen = true } label: {
                    Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 16)).foregroundColor(Theme.amber)
                        .frame(width: 36, height: 36)
                        .background(Circle().fill(Theme.amber.opacity(0.2)))
                }
                .buttonStyle(.plain)
                .accessibilityLabel(w.alerts.first?["name"]?.stringValue ?? "Weather alert")
                .accessibilityHint("Shows the weather alerts")
                .sheet(isPresented: $alertsOpen) { WeatherAlertsSheet(place: w.entity.name, alerts: w.alerts) }
            } else {
                VStack(alignment: .trailing, spacing: 1) {
                    ForEach(w.wetToday(), id: \.self) { s in
                        Text(s).font(AppFont.manrope(12 * scale, .bold)).foregroundColor(Theme.muted).lineLimit(1)
                    }
                }
            }
        }
        .frame(maxHeight: .infinity)
        .accessibilityElement(children: .contain)
    }

    @ViewBuilder private func content(_ w: WeatherModel) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if !w.alerts.isEmpty {
                Button { alertsOpen = true } label: {
                    Label(w.alerts.first?["name"]?.stringValue ?? "Weather alert", systemImage: "exclamationmark.triangle.fill")
                        .font(AppFont.manrope(12 * scale, .heavy)).lineLimit(1)
                        .padding(.horizontal, 10).frame(height: 28)
                        .background(Capsule().fill(Theme.amber.opacity(0.25)))
                        .foregroundColor(Theme.text)
                }
                .buttonStyle(.plain)
                .accessibilityHint("Shows the weather alerts")
                .sheet(isPresented: $alertsOpen) { WeatherAlertsSheet(place: w.entity.name, alerts: w.alerts) }
            }
            HStack(spacing: 8) {
                Image(systemName: Symbols.weather(w.condition, isDay: w.isDay)).font(.system(size: 30)).foregroundColor(Color(hex: 0xC9D4F5))
                    .accessibilityHidden(true)
                Text(WeatherModel.deg(w.temperature)).font(AppFont.sora(30 * scale)).foregroundColor(Theme.text)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Weather in \(w.entity.name), \(WeatherModel.deg(w.temperature))")
            .accessibilityAddTraits(.isHeader)
            Text("\(w.label) · \(w.highLowLine())").font(AppFont.manrope(13 * scale, .bold)).foregroundColor(Theme.text).lineLimit(2)
            if wide {
                if tile.string("show") == "hours" {
                    hours(w)
                } else {
                    days(w)
                }
            } else if let wet = w.nextWetDay() {
                Text(wet).font(AppFont.manrope(12 * scale, .bold)).foregroundColor(Theme.muted)
            }
        }
    }

    private func days(_ w: WeatherModel) -> some View {
        let list = Array(w.nextDays().prefix(6))
        let lo = list.compactMap { $0.low }.min() ?? 0
        let hi = list.compactMap { $0.high }.max() ?? 1
        let span = max(1, hi - lo)
        return VStack(spacing: 6) {
            ForEach(list, id: \.date) { d in
                HStack(spacing: 8) {
                    Text(d.day).frame(width: 40, alignment: .leading)
                    Image(systemName: Symbols.weather(d.condition)).frame(width: 24)
                    Text(d.precipChance.map { "\(Int($0.rounded()))%" } ?? "").frame(width: 38, alignment: .trailing)
                        .foregroundColor((d.precipChance ?? 0) < 10 ? Theme.dim : Theme.blue)
                    Text(WeatherModel.deg(d.low)).frame(width: 34, alignment: .trailing).foregroundColor(Theme.muted)
                    GeometryReader { g in
                        let l = ((d.low ?? lo) - lo) / span, h = ((d.high ?? hi) - lo) / span
                        Capsule().fill(Color.white.opacity(0.10))
                            .overlay(alignment: .leading) {
                                Capsule().fill(LinearGradient(colors: [Theme.blue, Theme.amber], startPoint: .leading, endPoint: .trailing))
                                    .frame(width: max(6, g.size.width * (h - l))).offset(x: g.size.width * l)
                            }
                    }
                    .frame(height: 6)
                    Text(WeatherModel.deg(d.high)).frame(width: 34, alignment: .trailing)
                }
                .font(AppFont.manrope(13 * scale, .bold))
                .foregroundColor(Theme.text)
                // "Tue: low 3°, high 9°, 40% chance of rain" (the bar only draws it).
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(["\(d.day): low \(WeatherModel.deg(d.low)), high \(WeatherModel.deg(d.high))",
                                     d.precipChance.map { "\(Int($0.rounded()))% chance of rain" }]
                    .compactMap { $0 }.joined(separator: ", "))
            }
        }
        .padding(.top, 4)
    }

    private func hours(_ w: WeatherModel) -> some View {
        let now = Date()
        let hourly = (w.entity.attr("hourly")?.arrayValue ?? []).compactMap { h -> (Date, JSONValue)? in
            guard let d = HubDates.parse(h["time"]?.stringValue), d.addingTimeInterval(3600) > now else { return nil }
            return (d, h)
        }.prefix(6)
        return HStack(spacing: 0) {
            ForEach(Array(hourly.enumerated()), id: \.offset) { i, item in
                VStack(spacing: 4) {
                    Text(i == 0 ? "Now" : item.0.formatted(.dateTime.hour())).foregroundColor(Theme.muted)
                    Image(systemName: Symbols.weather(item.1["condition"]?.stringValue)).font(.system(size: 18))
                        .accessibilityHidden(true)
                    Text(WeatherModel.deg(item.1["temperature"]?.doubleValue))
                }
                .frame(maxWidth: .infinity)
                .accessibilityElement(children: .combine)
            }
        }
        .font(AppFont.manrope(12 * scale, .bold))
        .foregroundColor(Theme.text)
        .padding(.top, 4)
    }
}

struct WeatherAlertsSheet: View {
    let place: String
    let alerts: [[String: JSONValue]]

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Weather alerts for \(place)").font(AppFont.sora(22)).foregroundColor(Theme.text)
                        .accessibilityAddTraits(.isHeader)
                    ForEach(Array(alerts.enumerated()), id: \.offset) { _, a in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(a["name"]?.stringValue ?? "Weather alert").font(AppFont.manrope(16, .heavy))
                                .accessibilityAddTraits(.isHeader)
                            Text([a["colour"]?.stringValue?.capitalized, a["type"]?.stringValue?.capitalized, a["area"]?.stringValue]
                                .compactMap { $0 }.joined(separator: " · ")).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                            Text(a["text"]?.stringValue ?? "No details given.").font(AppFont.manrope(14, .medium))
                        }
                        .foregroundColor(Theme.text)
                        .glassCard(radius: 18, padding: 14)
                    }
                }
                .padding(16)
            }
        }
        .presentationDetents([.medium, .large])
        .fullDynamicType()
    }
}

/// "Coming up": events from the calendars (the tile's, or all), in their colours.
struct CalendarTile: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.tileScale) var scale
    let tile: Tile

    var body: some View {
        let all = model.allEntities
        let items = AgendaModel.items(tile: tile, entities: all)
        let shown = Array(items.prefix(8))
        return VStack(alignment: .leading, spacing: 8) {
            TileTitle(text: "Coming up", note: items.count > shown.count ? "+\(items.count - shown.count) more" : nil)
            if !all.contains(where: AgendaModel.hasAgenda) {
                Text("Add a Calendars connection in the Newman dashboard.").font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted)
            } else if items.isEmpty {
                Text("Nothing coming up.").font(AppFont.manrope(13, .bold)).foregroundColor(Theme.muted)
            }
            ForEach(shown) { ev in
                HStack(spacing: 10) {
                    RoundedRectangle(cornerRadius: 2).fill(Color(hexString: ev.colourHex) ?? Theme.teal).frame(width: 4)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 1) {
                        HStack(spacing: 6) {
                            if ev.isNow {
                                Text("Now").font(AppFont.manrope(11 * scale, .heavy)).foregroundColor(Theme.background)
                                    .padding(.horizontal, 6).background(Capsule().fill(Theme.teal))
                            }
                            Text(ev.title).font(AppFont.manrope(14 * scale, .heavy)).foregroundColor(Theme.text).lineLimit(1)
                        }
                        Text(dayText(ev) + " · " + AgendaModel.timeText(ev) + (ev.location.isEmpty ? "" : " · " + ev.location))
                            .font(AppFont.manrope(12 * scale, .bold)).foregroundColor(Theme.muted).lineLimit(1)
                    }
                }
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityElement(children: .combine)
            }
        }
        .glassCard(padding: 14)
    }

    private func dayText(_ ev: AgendaItem) -> String {
        let start = ev.start < Calendar.current.startOfDay(for: Date()) ? Date() : ev.start
        return HubDates.dayLabel(start)
    }
}
