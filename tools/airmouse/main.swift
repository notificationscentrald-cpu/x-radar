// AirMouse: rotate your AirPods (or the open case holding them) to move the Mac cursor.
// Tap the case to click. Menu-bar app, no Xcode needed: see build.sh.
import AppKit
import CoreMotion

final class AirMouse: NSObject, NSApplicationDelegate, CMHeadphoneMotionManagerDelegate {
    let motion = CMHeadphoneMotionManager()
    var statusItem: NSStatusItem!
    var enabled = false
    var sensitivity: Double = 900          // pixels per radian/second of rotation
    var invertX = false, invertY = false
    var swapAxes = false
    var tapToClick = true
    var lastTap = Date.distantPast
    var samples = 0
    var lastLog = Date()
    var vx = 0.0, vy = 0.0                 // smoothed velocity

    func applicationDidFinishLaunching(_ note: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "🎧"
        rebuildMenu()
        motion.delegate = self
        log("Headphone motion available: \(motion.isDeviceMotionAvailable)")
        if !AXIsProcessTrusted() {
            let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue(): true] as CFDictionary
            AXIsProcessTrustedWithOptions(opts)
            log("Accessibility permission requested (needed for clicks).")
        }
        start()
    }

    func rebuildMenu() {
        let m = NSMenu()
        let toggle = NSMenuItem(title: enabled ? "Stop (⌥⌘A)" : "Start (⌥⌘A)", action: #selector(toggleEnabled), keyEquivalent: "a")
        toggle.keyEquivalentModifierMask = [.option, .command]
        m.addItem(toggle)
        m.addItem(NSMenuItem(title: "Samples received: \(samples)", action: nil, keyEquivalent: ""))
        m.addItem(.separator())
        m.addItem(check("Tap case to click", tapToClick, #selector(toggleTap)))
        m.addItem(check("Invert horizontal", invertX, #selector(toggleInvX)))
        m.addItem(check("Invert vertical", invertY, #selector(toggleInvY)))
        m.addItem(check("Swap axes", swapAxes, #selector(toggleSwap)))
        m.addItem(.separator())
        m.addItem(NSMenuItem(title: "Sensitivity + (⌥⌘=)", action: #selector(sensUp), keyEquivalent: "="))
        m.addItem(NSMenuItem(title: "Sensitivity − (⌥⌘-)", action: #selector(sensDown), keyEquivalent: "-"))
        m.items.suffix(2).forEach { $0.keyEquivalentModifierMask = [.option, .command] }
        m.addItem(NSMenuItem(title: "Current: \(Int(sensitivity))", action: nil, keyEquivalent: ""))
        m.addItem(.separator())
        m.addItem(NSMenuItem(title: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        statusItem.menu = m
    }
    func check(_ t: String, _ on: Bool, _ sel: Selector) -> NSMenuItem {
        let i = NSMenuItem(title: t, action: sel, keyEquivalent: ""); i.state = on ? .on : .off; return i
    }
    @objc func toggleEnabled() { enabled ? stop() : start() }
    @objc func toggleTap() { tapToClick.toggle(); rebuildMenu() }
    @objc func toggleInvX() { invertX.toggle(); rebuildMenu() }
    @objc func toggleInvY() { invertY.toggle(); rebuildMenu() }
    @objc func toggleSwap() { swapAxes.toggle(); rebuildMenu() }
    @objc func sensUp() { sensitivity *= 1.25; rebuildMenu() }
    @objc func sensDown() { sensitivity /= 1.25; rebuildMenu() }

    func start() {
        guard motion.isDeviceMotionAvailable else {
            log("No headphone motion source. Connect AirPods (Pro/3/Max/4) to this Mac.")
            return
        }
        enabled = true
        statusItem.button?.title = "🎧●"
        motion.startDeviceMotionUpdates(to: .main) { [weak self] dm, err in
            if let err = err { self?.log("Motion error: \(err.localizedDescription)"); return }
            guard let self = self, let dm = dm else { return }
            self.handle(dm)
        }
        rebuildMenu()
        log("Started. Move the AirPods / case to move the cursor.")
    }
    func stop() {
        enabled = false
        motion.stopDeviceMotionUpdates()
        statusItem.button?.title = "🎧"
        rebuildMenu()
        log("Stopped.")
    }

    func headphoneMotionManagerDidConnect(_ m: CMHeadphoneMotionManager) { log("AirPods connected") }
    func headphoneMotionManagerDidDisconnect(_ m: CMHeadphoneMotionManager) { log("AirPods disconnected") }

    func handle(_ dm: CMDeviceMotion) {
        samples += 1
        if Date().timeIntervalSince(lastLog) > 5 {
            lastLog = Date(); rebuildMenu()
            log(String(format: "samples=%d rot(x=%.2f y=%.2f z=%.2f) acc=%.2f", samples,
                       dm.rotationRate.x, dm.rotationRate.y, dm.rotationRate.z, mag(dm.userAcceleration)))
        }
        let r = dm.rotationRate
        // Yaw (z) -> horizontal, pitch (x) -> vertical. Deadzone kills sensor drift.
        var h = swapAxes ? r.x : r.z
        var v = swapAxes ? r.z : r.x
        if abs(h) < 0.03 { h = 0 }
        if abs(v) < 0.03 { v = 0 }
        if invertX { h = -h }
        if invertY { v = -v }
        // dt ~ 1/100 s on AirPods; smooth a little so it feels like a trackpad
        vx = vx * 0.5 + (-h * sensitivity / 100) * 0.5
        vy = vy * 0.5 + (-v * sensitivity / 100) * 0.5
        moveCursor(dx: vx, dy: vy)

        if tapToClick, mag(dm.userAcceleration) > 1.4,
           Date().timeIntervalSince(lastTap) > 0.45 {
            lastTap = Date()
            click()
        }
    }
    func mag(_ a: CMAcceleration) -> Double { (a.x * a.x + a.y * a.y + a.z * a.z).squareRoot() }

    func moveCursor(dx: Double, dy: Double) {
        guard abs(dx) + abs(dy) > 0.01 else { return }
        let p = NSEvent.mouseLocation   // bottom-left origin
        guard let screen = NSScreen.screens.first(where: { NSMouseInRect(p, $0.frame, false) }) ?? NSScreen.main else { return }
        let h = NSScreen.screens[0].frame.maxY  // primary screen height; CGEvent uses top-left origin
        var x = p.x + dx, y = h - p.y + dy
        x = min(max(x, screen.frame.minX), screen.frame.maxX - 1)
        y = min(max(y, h - screen.frame.maxY), h - screen.frame.minY - 1)
        let pt = CGPoint(x: x, y: y)
        if let ev = CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: pt, mouseButton: .left) {
            ev.post(tap: .cghidEventTap)
        } else {
            CGWarpMouseCursorPosition(pt)
        }
    }
    func click() {
        let p = NSEvent.mouseLocation
        let pt = CGPoint(x: p.x, y: NSScreen.screens[0].frame.maxY - p.y)
        CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: pt, mouseButton: .left)?.post(tap: .cghidEventTap)
        CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: pt, mouseButton: .left)?.post(tap: .cghidEventTap)
        log("click")
    }
    func log(_ s: String) { print("[AirMouse] \(s)"); fflush(stdout) }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = AirMouse()
app.delegate = delegate
app.run()
