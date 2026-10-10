import Foundation

/// When the phone moves between the hub's home and outside addresses (HubRoute runs it; pure, so
/// the Linux checks cover it). A hub restart makes the home address miss a probe or two: that
/// alone must never move the phone to the outside address. So:
///  - home -> outside only after home has failed continuously for `homeGraceSeconds` (or the
///    network itself changed - Wi-Fi <-> cellular, another Wi-Fi), AND the outside address
///    answered GET /api/hello as the same hub;
///  - while outside, home is probed every `awayRecheckSeconds` and used again as soon as it answers.
/// The wall tablet never runs this: it stays on the address chosen on its Server screen.
struct HubRoutePolicy: Equatable {
    static let homeGraceSeconds: Double = 120
    static let awayRecheckSeconds: Double = 30
    /// While home is failing (and not yet given up on), how often it is probed again.
    static let failingRecheckSeconds: Double = 15

    enum Using: Equatable { case home, outside }

    enum Step: Equatable {
        /// Nothing to change.
        case stay
        /// Home answered while outside: use home again.
        case useHome
        /// Home has been failing long enough (or the network changed): ask the outside address
        /// and call `outside(sameHub:)` with its answer.
        case probeOutside
    }

    var using: Using
    /// When home started failing (nil while it answers).
    var homeFailingSince: Double?
    var lastHomeFailure: Double?
    /// The network (HubRoute's path summary) home last answered on, if known.
    var homeNetwork: String?
    /// The network changed since home last answered.
    var networkChanged = false

    init(using: Using, homeNetwork: String? = nil) {
        self.using = using
        self.homeNetwork = homeNetwork
    }

    /// The phone's network path changed.
    mutating func pathChanged() { networkChanged = true }

    /// The home address answered as this hub (`answered`) or didn't, at `now`, on `network`.
    mutating func home(answered: Bool, now: Double, network: String? = nil) -> Step {
        if answered {
            homeFailingSince = nil
            lastHomeFailure = nil
            networkChanged = false
            if let network { homeNetwork = network }
            if using == .outside {
                using = .home
                return .useHome
            }
            return .stay
        }
        // "Continuously": a failure long after the previous one (the app was in the background
        // meanwhile) starts the clock again.
        if homeFailingSince == nil || now - (lastHomeFailure ?? now) > Self.homeGraceSeconds { homeFailingSince = now }
        lastHomeFailure = now
        guard using == .home else { return .stay }
        // On cellular, or on another network than home last answered on: not a hub restart.
        let elsewhere = networkChanged || (network?.hasPrefix("cellular") ?? false)
            || (network != nil && homeNetwork != nil && network != homeNetwork)
        if elsewhere || now - (homeFailingSince ?? now) >= Self.homeGraceSeconds { return .probeOutside }
        return .stay
    }

    /// The outside address's answer after `.probeOutside`: true when the phone should switch to it
    /// (it answered with the saved hub's id).
    mutating func outside(sameHub: Bool) -> Bool {
        guard using == .home, sameHub else { return false }
        using = .outside
        networkChanged = false
        return true
    }

    /// How long until the home address should be probed again (nil: only on launch, foreground
    /// and network changes).
    var nextProbe: Double? {
        if using == .outside { return Self.awayRecheckSeconds }
        return homeFailingSince != nil ? Self.failingRecheckSeconds : nil
    }
}
