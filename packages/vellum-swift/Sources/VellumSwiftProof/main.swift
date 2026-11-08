import AppKit
import CoreGraphics
import Darwin
import Foundation
import SwiftUI
import VellumHost

struct ProofFailure: Error, CustomStringConvertible {
    let description: String
}

func require(_ condition: Bool, _ message: @autoclosure () -> String) throws {
    if !condition { throw ProofFailure(description: message()) }
}

func processStart() -> Date? {
    var info = kinfo_proc()
    var size = MemoryLayout<kinfo_proc>.stride
    var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, getpid()]
    guard sysctl(&mib, u_int(mib.count), &info, &size, nil, 0) == 0 else { return nil }
    let started = info.kp_proc.p_un.__p_starttime
    return Date(timeIntervalSince1970: TimeInterval(started.tv_sec) +
                TimeInterval(started.tv_usec) / 1_000_000)
}

func milliseconds(since start: Date) -> Double {
    (Date().timeIntervalSince(start) * 1_000_000).rounded() / 1000
}

@MainActor
func runProof(bundlePath: String, launched: Date, entered: Date) throws -> [String: Any] {
    try require(VellumHost.abiVersion == VellumHost.expectedABIVersion,
                "C ABI version \(VellumHost.abiVersion) is not \(VellumHost.expectedABIVersion)")
    let bundle = try String(contentsOfFile: bundlePath, encoding: .utf8)
    var application: VellumApplication? = try VellumApplication(bundle: bundle)
    let viewport = application?.viewport ?? .zero
    try require(viewport.width >= 1 && viewport.height >= 1, "empty viewport \(viewport)")
    var surface: VellumSurface? = try VellumSurface(
        width: UInt32(viewport.width), height: UInt32(viewport.height), scale: 1)
    var report = try exercise(app: application!, target: surface!, viewport: viewport,
                              launched: launched, entered: entered)
    let teardownStart = Date()
    surface = nil
    application = nil
    report["teardown_ms"] = milliseconds(since: teardownStart)
    return report
}

@MainActor
func exercise(app: VellumApplication, target: VellumSurface, viewport: CGSize,
              launched: Date, entered: Date) throws -> [String: Any] {
    let info = try target.info
    try require(info.available && !info.nativeSurface && !info.fallback,
                "surface is not an offscreen GPU target without fallback")
    try require(info.renderer == "Skia Graphite" && info.backend == "Metal",
                "renderer is \(info.renderer)/\(info.backend)")

    try target.render(app)
    let first = try target.captureRGBA()
    let launchToFirstFrame = milliseconds(since: launched)
    let entryToFirstFrame = milliseconds(since: entered)
    try require(first.width == UInt32(viewport.width) && first.height == UInt32(viewport.height),
                "capture is \(first.width)x\(first.height)")
    let stats = try first.contentStats()
    try require(stats.passesContentFloor,
                "first frame failed the content floor: colors=\(stats.uniqueColors)")
    let blank = try VellumContentFloor.analyze(
        rgba: [UInt8](repeating: 0, count: first.pixels.count),
        width: first.width, height: first.height)
    try require(!blank.passesContentFloor, "blank negative control passed the content floor")

    try target.render(app)
    let repeated = try target.captureRGBA()
    try require(repeated.pixels == first.pixels, "rendering the same state twice changed the frame")

    let nodes = try app.accessibilityNodes()
    guard let button = nodes.first(where: { $0.role == "button" }) else {
        throw ProofFailure(description: "no button in the accessibility projection")
    }
    let press = try app.pointer(.down, at: CGPoint(x: button.bounds.midX, y: button.bounds.midY))
    try require(press == .dispatched, "pointer press was \(press)")
    let pressed = try app.accessibilityNodes().first { $0.nodeID == button.nodeID }
    try require(pressed?.label != button.label, "pointer press did not change the button label")

    guard let field = try app.textInputs().first else {
        throw ProofFailure(description: "no text input projection")
    }
    let focus = try app.pointer(.down, at: CGPoint(x: field.bounds.minX + 4, y: field.bounds.midY))
    try require(focus == .focused, "text input focus was \(focus)")
    let typed = "Swift \u{2713}"
    try require(try app.insertText(typed) == .dispatched, "text insert was ignored")
    try require(try app.textInputs().first?.value == typed, "text input value did not update")
    try require(try app.key("Enter") == .dispatched, "Enter was ignored")

    try target.render(app)
    let second = try target.captureRGBA()
    try require(try second.contentStats().passesContentFloor, "second frame failed the content floor")
    try require(second.pixels != first.pixels, "interaction did not change the rendered frame")

    return [
        "ok": true,
        "abi_version": Int(VellumHost.abiVersion),
        "renderer": info.renderer,
        "backend": info.backend,
        "native_surface": info.nativeSurface,
        "fallback": info.fallback,
        "width": Int(first.width),
        "height": Int(first.height),
        "unique_colors": stats.uniqueColors,
        "non_background_pixels": stats.nonBackgroundPixels,
        "luminance_standard_deviation": stats.luminanceStandardDeviation,
        "accessibility_nodes": nodes.count,
        "deterministic_repeat_capture": true,
        "text_inputs": try app.textInputs().count,
        "launch_to_first_frame_ms": launchToFirstFrame,
        "entry_to_first_frame_ms": entryToFirstFrame,
    ]
}

func descendant<T: NSView>(of view: NSView, as type: T.Type) -> T? {
    if let match = view as? T { return match }
    for child in view.subviews {
        if let match = descendant(of: child, as: type) { return match }
    }
    return nil
}

func hasWindowServer() -> Bool {
    CGSessionCopyCurrentDictionary() != nil
}

@MainActor
func click(_ view: NSView, at point: CGPoint, in window: NSWindow) throws {
    let location = view.convert(point, to: nil)
    for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
        guard let event = NSEvent.mouseEvent(
            with: type, location: location, modifierFlags: [], timestamp: 0,
            windowNumber: window.windowNumber, context: nil, eventNumber: 0,
            clickCount: 1, pressure: 1) else {
            throw ProofFailure(description: "could not synthesize a mouse event")
        }
        if type == .leftMouseDown { view.mouseDown(with: event) } else { view.mouseUp(with: event) }
    }
}

@MainActor
func exerciseNativeHosts(bundle: String) throws -> [String: Any] {
    guard hasWindowServer() else { return ["native_hosts": "skipped: no window-server session"] }
    _ = NSApplication.shared
    NSApplication.shared.setActivationPolicy(.prohibited)
    let frame = NSRect(x: 0, y: 0, width: 360, height: 240)
    let window = NSWindow(contentRect: frame, styleMask: [.borderless], backing: .buffered,
                          defer: false)
    window.isReleasedWhenClosed = false
    let view = VellumHostView(application: try VellumApplication(bundle: bundle))
    window.contentView = view
    try require(view.lastError == nil, "native host failed: \(String(describing: view.lastError))")
    guard let surface = view.surface else { throw ProofFailure(description: "no native surface") }
    let info = try surface.info
    try require(info.available && info.nativeSurface && !info.fallback,
                "native host surface is not a CAMetalLayer target without fallback")

    let elements = view.accessibilityChildren() ?? []
    let nodes = try view.application.accessibilityNodes()
    try require(elements.count == nodes.count, "accessibility children do not match the projection")
    guard let button = nodes.first(where: { $0.role == "button" }) else {
        throw ProofFailure(description: "native host has no button")
    }
    try click(view, at: CGPoint(x: button.bounds.midX, y: button.bounds.midY), in: window)
    try require(view.lastError == nil, "native click failed")
    let pressed = try view.application.accessibilityNodes().first { $0.nodeID == button.nodeID }
    try require(pressed?.label != button.label, "native click did not reach the application")
    let again = view.accessibilityChildren() ?? []
    try require(zip(elements, again).allSatisfy { ($0 as AnyObject) === ($1 as AnyObject) },
                "accessibility elements lost their identity across a refresh")

    view.layer?.contentsScale = 2
    view.viewDidChangeBackingProperties()
    try require(view.lastError == nil, "backing-scale change failed")
    view.setFrameSize(NSSize(width: 300, height: 200))
    try require(view.lastError == nil, "native resize failed")

    let hosting = NSHostingView(rootView: VellumView(bundle: bundle).frame(width: 360, height: 240))
    hosting.frame = frame
    let swiftUIWindow = NSWindow(contentRect: frame, styleMask: [.borderless], backing: .buffered,
                                 defer: false)
    swiftUIWindow.isReleasedWhenClosed = false
    swiftUIWindow.contentView = hosting
    hosting.layoutSubtreeIfNeeded()
    guard let container = descendant(of: hosting, as: VellumViewContainer.self),
          let hosted = container.hostView else {
        throw ProofFailure(description: "VellumView did not host a VellumHostView")
    }
    try require(container.lastError == nil && hosted.lastError == nil, "VellumView failed to load")
    try require(hosted.surface != nil, "VellumView has no native surface")
    let hostedInfo = try hosted.surface!.info
    try require(hostedInfo.nativeSurface && !hostedInfo.fallback, "VellumView surface fell back")

    window.contentView = nil
    swiftUIWindow.contentView = nil
    window.close()
    swiftUIWindow.close()
    return [
        "native_hosts": "passed",
        "native_renderer": info.renderer,
        "native_backend": info.backend,
        "native_accessibility_children": elements.count,
    ]
}

let entered = Date()
let launched = processStart() ?? entered
let arguments = CommandLine.arguments
guard arguments.count == 3, arguments[1] == "--bundle" else {
    FileHandle.standardError.write(Data("usage: vellum-swift-proof --bundle APP.js\n".utf8))
    exit(2)
}
do {
    var report = try runProof(bundlePath: arguments[2], launched: launched, entered: entered)
    let source = try String(contentsOfFile: arguments[2], encoding: .utf8)
    report.merge(try exerciseNativeHosts(bundle: source)) { _, native in native }
    let data = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
    print(String(decoding: data, as: UTF8.self))
} catch {
    FileHandle.standardError.write(Data("vellum-swift-proof: \(error)\n".utf8))
    exit(1)
}
