import ActivityKit
import Foundation

/// A live status as an ActivityKit Live Activity (Lock Screen + Dynamic Island). Compiled into the
/// app (which registers the push tokens and starts ones the hub couldn't start by push) and the
/// widget extension (which draws them: LiveStatusActivity).
///
/// The hub's push-to-start names this type: "attributes-type": "NewmanLiveAttributes", with
/// "attributes": {"key", "title"} - so the type name and these fields must not change. The
/// content-state is LiveStatusState (Shared/Core/LiveStatus.swift).
struct NewmanLiveAttributes: ActivityAttributes {
    typealias ContentState = LiveStatusState

    /// The hub's key for this live status (the same on every phone it's shown on).
    var key: String
    /// The title it started with (the content-state's title can change later).
    var title: String

    enum CodingKeys: String, CodingKey { case key, title }
}

extension NewmanLiveAttributes {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decode(String.self, forKey: .key)
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? ""
    }
}
