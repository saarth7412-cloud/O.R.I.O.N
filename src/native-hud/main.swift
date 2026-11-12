import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {

    private let statusModel = HUDStatusModel()
    private let keyListener = KeyListener()
    private var eventClient: EventClient?
    private var hudPanel: FloatingHUDPanel?
    private var statusItem: NSStatusItem?
    private var toggleObserver: NSObjectProtocol?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let panel = FloatingHUDPanel()
        panel.setHUDContent(NSHostingView(rootView: HUDView(model: statusModel)))
        hudPanel = panel

        toggleObserver = NotificationCenter.default.addObserver(
            forName: .orionToggleHUD,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            self?.toggleHUD()
        }

        keyListener.start()
        buildStatusItem()

        let client = EventClient(model: statusModel)
        client.start()
        eventClient = client

        panel.present()
    }

    func applicationWillTerminate(_ notification: Notification) {
        keyListener.stop()
        eventClient?.stop()
        if let observer = toggleObserver {
            NotificationCenter.default.removeObserver(observer)
        }
    }

    /// Keeps the app alive after the HUD is hidden.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    private func toggleHUD() {
        hudPanel?.toggleVisibility()
    }

    private func buildStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "⌘"

        let menu = NSMenu()

        let toggleItem = NSMenuItem(
            title: "Toggle HUD",
            action: #selector(toggleFromMenu),
            keyEquivalent: ""
        )
        toggleItem.target = self
        menu.addItem(toggleItem)

        menu.addItem(.separator())

        let quitItem = NSMenuItem(
            title: "Quit O.R.I.O.N.",
            action: #selector(quitFromMenu),
            keyEquivalent: "q"
        )
        quitItem.target = self
        menu.addItem(quitItem)

        item.menu = menu
        statusItem = item
    }

    @objc private func toggleFromMenu() {
        toggleHUD()
    }

    @objc private func quitFromMenu() {
        NSApplication.shared.terminate(nil)
    }
}

// MARK: - Entry point

@MainActor
func runOrion() {
    let app = NSApplication.shared
    let appDelegate = AppDelegate()
    app.delegate = appDelegate
    app.setActivationPolicy(.accessory)
    app.run()
}

// The top-level script runs on the main thread, which is the main actor.
MainActor.assumeIsolated {
    runOrion()
}
