import AppKit

extension Notification.Name {
    /// Posted when a clean double-tap of the Command key is detected system-wide.
    static let orionToggleHUD = Notification.Name("ai.orion.toggleHUD")
}

/// Detects a double-tap of the Command key, whether pressed in our app or any other.
/// Global monitoring requires Accessibility and/or Input Monitoring permission.
/// All handlers run on the main thread.
final class KeyListener {

    /// Maximum gap between the start of the first tap and the start of the second.
    static let doubleTapThreshold: TimeInterval = 0.35

    /// Maximum time Command may be held for a press to count as a tap.
    static let maxTapHoldDuration: TimeInterval = 0.25

    private static let disqualifyingModifiers: NSEvent.ModifierFlags = [.shift, .option, .control, .function]

    private var globalFlagsMonitor: Any?
    private var localFlagsMonitor: Any?
    private var globalKeyMonitor: Any?
    private var localKeyMonitor: Any?

    private(set) var isRunning = false

    private var commandDown = false
    private var pressIsCleanTap = false
    private var pressStartedAt: TimeInterval = 0
    private var lastTapStartedAt: TimeInterval?

    // MARK: - Lifecycle

    func start() {
        guard !isRunning else { return }

        globalFlagsMonitor = NSEvent.addGlobalMonitorForEvents(matching: .flagsChanged) { [weak self] event in
            self?.handleFlagsChanged(event)
        }
        localFlagsMonitor = NSEvent.addLocalMonitorForEvents(matching: .flagsChanged) { [weak self] event in
            self?.handleFlagsChanged(event)
            return event
        }

        // Any ordinary key press, in any app, breaks a pending double-tap.
        globalKeyMonitor = NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] _ in
            self?.invalidatePendingTap()
        }
        localKeyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            self?.invalidatePendingTap()
            return event
        }

        if globalFlagsMonitor == nil {
            NSLog("[O.R.I.O.N.] KeyListener: global monitor unavailable. Grant Accessibility and Input Monitoring permission.")
        }

        isRunning = true
    }

    func stop() {
        [globalFlagsMonitor, localFlagsMonitor, globalKeyMonitor, localKeyMonitor]
            .compactMap { $0 }
            .forEach { NSEvent.removeMonitor($0) }

        globalFlagsMonitor = nil
        localFlagsMonitor = nil
        globalKeyMonitor = nil
        localKeyMonitor = nil

        resetState()
        isRunning = false
    }

    // MARK: - Detection

    private func handleFlagsChanged(_ event: NSEvent) {
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        let commandNow = flags.contains(.command)
        let now = ProcessInfo.processInfo.systemUptime

        if commandNow && !commandDown {
            // Command pressed.
            commandDown = true
            pressStartedAt = now
            pressIsCleanTap = flags.intersection(Self.disqualifyingModifiers).isEmpty

        } else if !commandNow && commandDown {
            // Command released.
            commandDown = false
            let wasCleanTap = pressIsCleanTap
            pressIsCleanTap = false

            let heldBriefly = now - pressStartedAt <= Self.maxTapHoldDuration
            guard wasCleanTap, heldBriefly else {
                lastTapStartedAt = nil
                return
            }
            registerTap(startedAt: pressStartedAt)

        } else if commandNow && commandDown {
            // Another modifier joined while Command was held: not a lone tap.
            if !flags.intersection(Self.disqualifyingModifiers).isEmpty {
                pressIsCleanTap = false
            }
        }
    }

    private func registerTap(startedAt: TimeInterval) {
        if let previous = lastTapStartedAt,
           startedAt - previous <= Self.doubleTapThreshold {
            lastTapStartedAt = nil
            NotificationCenter.default.post(name: .orionToggleHUD, object: nil)
        } else {
            lastTapStartedAt = startedAt
        }
    }

    // MARK: - State helpers

    private func invalidatePendingTap() {
        pressIsCleanTap = false
        lastTapStartedAt = nil
    }

    private func resetState() {
        commandDown = false
        pressIsCleanTap = false
        pressStartedAt = 0
        lastTapStartedAt = nil
    }
}
