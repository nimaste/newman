import SwiftUI
import WidgetKit

/// The NewmanWidgets extension (iOS 26): home-screen device widgets (tiles and rows), the Home status
/// widget, Lock Screen device widgets, Control Center controls and the live status Live Activity. The
/// widgets take WidgetKit push
/// updates: the hub pushes when devices change (NewmanWidgetPushHandler registers the token); they
/// also refresh right after an action, when the app reloads them, and every 15 minutes. Live
/// statuses come by ActivityKit push (the app registers those tokens: LiveActivities).
@main
struct NewmanWidgetsBundle: WidgetBundle {
    var body: some Widget {
        DevicesWidgetPushed()
        DeviceRowsWidget()
        HomeStatusWidget()
        DeviceAccessoryWidgetPushed()
        DeviceToggleControl()
        DeviceButtonControl()
        LiveStatusActivity()
    }
}

/// WidgetKit's push token for this phone's widgets: kept in the App Group (the app re-sends it
/// after signing in) and sent to the hub now (POST /api/widgets/push-token, kind "ios-widget").
struct NewmanWidgetPushHandler: WidgetPushHandler {
    func pushTokenDidChange(_ pushInfo: WidgetPushInfo, widgets: [WidgetInfo]) {
        let token = pushInfo.token
        Task { await WidgetHub.pushTokenChanged(token) }
    }
}
