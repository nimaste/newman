import MapKit
import SwiftUI

/// One person on a full-screen map (MapKit): their dot, the accuracy circle, "Home" / place /
/// distance and when. The refresh button (and the tap that opened it) asks their phone for ONE fresh
/// fix ("Updating…" until it arrives); updates arrive over the live connection, with a 30 s re-read of
/// the hub as a fallback (that never wakes their phone). `light`: the standard map (a People tile's "Map look"); otherwise the dark one.
struct PersonMapView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let personId: String
    var light = false
    @State private var camera: MapCameraPosition = .automatic
    @State private var centred: CLLocationCoordinate2D?
    @State private var now = Date().timeIntervalSince1970
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var person: Person? { model.person(personId) }
    private var index: Int { model.people.firstIndex { $0.id == personId } ?? 0 }
    private var colour: Color { Color(hex: PersonLook.rgb(person ?? Person(id: personId), index: index)) }
    private var refresh: PersonRefresh.State { model.refreshState(personId) }

    /// Places the hub has a centre for (Home's is null until it knows where home is).
    private var placed: [Place] { model.places.filter { $0.lat != nil && $0.lon != nil } }

    private var coordinate: CLLocationCoordinate2D? {
        guard let p = person, let lat = p.lat, let lon = p.lon else { return nil }
        return CLLocationCoordinate2D(latitude: lat, longitude: lon)
    }

    var body: some View {
        ZStack(alignment: .top) {
            Map(position: $camera) {
                ForEach(placed) { pl in
                    MapCircle(center: CLLocationCoordinate2D(latitude: pl.lat ?? 0, longitude: pl.lon ?? 0), radius: pl.radiusM ?? 150)
                        .foregroundStyle((light ? Theme.background : Color.white).opacity(0.08))
                        .stroke((light ? Theme.background : Color.white).opacity(0.25), lineWidth: 1)
                }
                if let c = coordinate, let p = person {
                    MapCircle(center: c, radius: max(15, p.accuracyM ?? 0))
                        .foregroundStyle(colour.opacity(0.18))
                        .stroke(colour.opacity(0.6), lineWidth: 2)
                    Annotation(p.name, coordinate: c, anchor: .center) {
                        PersonAvatar(person: p, index: index, size: 40)
                            .overlay(Circle().stroke(Color.white, lineWidth: 3))
                            .shadow(radius: 4)
                            .accessibilityLabel("\(p.name), \(PeopleText.whereText(p))")
                    }
                    .annotationTitles(.hidden)
                }
            }
            .mapStyle(.standard(pointsOfInterest: .excludingAll))
            .preferredColorScheme(light ? .light : .dark)
            .ignoresSafeArea()

            HStack {
                Button { dismiss() } label: { Label("Back", systemImage: "chevron.left") }.buttonStyle(PillButtonStyle())
                Spacer()
            }
            .padding(.horizontal, 14)
            .padding(.top, 8)

            VStack {
                Spacer()
                HStack(spacing: 12) {
                    PersonAvatar(person: person, index: index).accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(person?.name ?? "…").font(AppFont.sora(20)).foregroundColor(Theme.text)
                            .accessibilityAddTraits(.isHeader)
                        Text(person.map { PeopleText.whereText($0) } ?? "")
                            .font(AppFont.manrope(13, .bold)).foregroundColor(person?.home == true ? Theme.teal : Theme.text.opacity(0.85))
                        if refresh.phase != .idle {
                            RefreshNote(state: refresh, now: Date().timeIntervalSince1970)
                        } else if let u = person.flatMap({ PeopleText.updated($0.at, now: now) }) {
                            Text(u).font(AppFont.manrope(12, .bold)).foregroundColor(Theme.muted)
                        }
                    }
                    .accessibilityElement(children: .combine)
                    Spacer()
                    if person?.sharing == true {
                        Button { model.refreshPerson(personId) } label: {
                            Image(systemName: "arrow.clockwise").font(.system(size: 16, weight: .bold)).foregroundColor(Theme.text)
                                .frame(width: 42, height: 42)
                                .background(Circle().fill(Color.white.opacity(0.10)))
                        }
                        .buttonStyle(.plain)
                        .disabled(refresh.phase == .updating)
                        .accessibilityLabel("Get a fresh location")
                    }
                }
                .padding(14)
                .background(RoundedRectangle(cornerRadius: 22, style: .continuous).fill(Color(hex: 0x141A33, alpha: 0.92)))
                .padding(12)
            }
        }
        .background(Theme.background)
        .fullDynamicType()
        .onAppear { recentre(animated: false) }
        .onChange(of: person?.lat) { recentre(animated: true) }
        .onChange(of: person?.lon) { recentre(animated: true) }
        .task { await model.reloadPlaces() }
        .task {
            // Fallback refresh (live "person" messages do the real work).
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: UInt64(ReportSchedule.peopleRefreshSeconds) * 1_000_000_000)
                await model.reloadPeople()
                now = Date().timeIntervalSince1970
            }
        }
        .task {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 15_000_000_000)
                now = Date().timeIntervalSince1970
            }
        }
    }

    private func recentre(animated: Bool) {
        guard let c = coordinate else { return }
        if let old = centred, old.latitude == c.latitude, old.longitude == c.longitude { return }
        centred = c
        let region = MKCoordinateRegion(center: c, latitudinalMeters: 1200, longitudinalMeters: 1200)
        if animated && !reduceMotion {
            withAnimation(.easeInOut(duration: 0.6)) { camera = .region(region) }
        } else {
            camera = .region(region)
        }
    }
}
