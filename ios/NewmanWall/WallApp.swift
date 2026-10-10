import SwiftUI
import UIKit

@main
struct NewmanWallApp: App {
    @UIApplicationDelegateAdaptor(WallAppDelegate.self) private var delegate
    @StateObject private var model = WallModel.shared
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            WallRootView()
                .environmentObject(model)
                .preferredColorScheme(.dark)
                .onOpenURL { model.open($0) } // newman://connect?...
        }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active: model.becameActive()
            case .background, .inactive: model.resignedActive()
            @unknown default: break
            }
        }
    }
}

final class WallAppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        LocalNetworkProbe.run()
        // No HubRoute here: the wall never switches address by itself. It stays on the one chosen
        // on its Server screen and keeps retrying it (a hub restart must not move it outside).
        return true
    }
}

/// Full-screen kiosk: the hub's wall page with no system bars, a veil while dimmed that wakes on the
/// first touch (that touch doesn't reach the page), and settings behind a 3-second corner hold. While
/// dimmed the page draws its own dimmed screen; the veil only darkens a page that doesn't.
struct WallRootView: View {
    @EnvironmentObject var model: WallModel

    var body: some View {
        let shell = model.shell
        ZStack {
            // The page's theme colour (NewmanWall.setTheme), so loading and errors match it.
            WallModel.color(shell.background).ignoresSafeArea()
            if model.serverURL == nil {
                // First run: connect to the hub before anything else (the page signs in after).
                ServerSetupView(link: $model.incomingLink) { model.serverChanged() }
                    .frame(maxWidth: 640)
                    .frame(maxWidth: .infinity)
            } else {
                // The page's sign-in has its own Server button (NewmanWall.openServer()).
                WallWebView(model: model)
                    .ignoresSafeArea()
                if let err = model.loadError {
                    VStack(spacing: 14) {
                        Image(systemName: "wifi.exclamationmark").font(.system(size: 44)).foregroundColor(WallModel.color(shell.ink))
                        Text("Can't reach Newman").font(AppFont.sora(26)).foregroundColor(WallModel.color(shell.strongInk))
                        Text(err).font(AppFont.manrope(15, .bold)).foregroundColor(WallModel.color(shell.ink)).multilineTextAlignment(.center)
                        Text("Trying again every 30 seconds. Hold the top-left corner for settings.")
                            .font(AppFont.manrope(13, .bold)).foregroundColor(WallModel.color(shell.ink).opacity(0.8))
                        HStack(spacing: 12) {
                            Button("Try again now") { model.loadHome() }.buttonStyle(PillButtonStyle(height: 48))
                            Button { model.serverOpen = true } label: { Label("Server", systemImage: "server.rack") }
                                .buttonStyle(PillButtonStyle(height: 48))
                        }
                    }
                    .padding(40)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(WallModel.color(shell.background).opacity(0.94))
                    .allowsHitTesting(true)
                }
            }
            if model.dimmed {
                // Swallows the waking touch so it can't press anything on the page.
                Color.black.opacity(model.fallbackDim ? 0.55 : 0.001)
                    .ignoresSafeArea()
                    .contentShape(Rectangle())
                    .onTapGesture { model.wake() }
                    .gesture(DragGesture(minimumDistance: 0).onChanged { _ in model.wake() })
            }
        }
        .statusBarHidden(true)
        .persistentSystemOverlays(.hidden)
        .onAppear { model.becameActive() }  // scenePhase doesn't report the first .active
        .sheet(isPresented: $model.settingsOpen) {
            WallSettingsView().environmentObject(model)
        }
        .background {
            // A separate host for the Server screen (one sheet per view).
            Color.clear.sheet(isPresented: $model.serverOpen) {
                ServerSheet(link: $model.incomingLink, onSaved: { model.serverChanged() }) { model.serverOpen = false }
            }
        }
    }
}