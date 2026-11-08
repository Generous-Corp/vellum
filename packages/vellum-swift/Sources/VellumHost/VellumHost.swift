import CVellumHostC
import CoreGraphics
import QuartzCore

public struct VellumHostError: Error, CustomStringConvertible, Sendable {
    public let status: Int32
    public let message: String

    public var description: String { "vellum host status \(status): \(message)" }
}

func check(_ status: vellum_host_status) throws {
    guard status == VELLUM_HOST_OK else {
        throw VellumHostError(status: status, message: String(cString: vellum_host_last_error()))
    }
}

func structSize<T>(_: T.Type) -> UInt32 {
    UInt32(MemoryLayout<T>.size)
}

func string(_ pointer: UnsafePointer<CChar>?) -> String {
    pointer.map { String(cString: $0) } ?? ""
}

func cgRect(_ rect: vellum_host_rect) -> CGRect {
    CGRect(x: CGFloat(rect.x), y: CGFloat(rect.y),
           width: CGFloat(rect.width), height: CGFloat(rect.height))
}

public enum VellumHost {
    public static var abiVersion: UInt32 { vellum_host_abi_version() }
    public static let expectedABIVersion: UInt32 = VELLUM_HOST_ABI_VERSION
}

public enum EventOutcome: UInt32, Sendable {
    case ignored = 0
    case dispatched = 1
    case focused = 2
    case unfocused = 3
}

public enum PointerPhase: UInt32, Sendable {
    case down = 1
    case up = 2
    case move = 3
    case cancel = 4
}

public enum PointerDevice: UInt32, Sendable {
    case mouse = 1
    case touch = 2
    case pen = 3
}

public struct PumpResult: Sendable {
    public let rendered: Bool
    public let idle: Bool
    public let tasksExecuted: Int
}

public struct AccessibilityNode: Sendable, Equatable {
    public let nodeID: String
    public let role: String
    public let label: String
    public let value: String
    public let state: UInt32
    public let actions: [String]
    public let bounds: CGRect

    public var isDisabled: Bool { state & VELLUM_HOST_A11Y_DISABLED != 0 }
}

public struct TextInput: Sendable, Equatable {
    public let nodeID: String
    public let value: String
    public let placeholder: String
    public let selection: Range<Int>
    public let focused: Bool
    public let bounds: CGRect
}

public struct ContentStats: Sendable {
    public let passesContentFloor: Bool
    public let pixelCount: Int
    public let uniqueColors: Int
    public let opaquePixels: Int
    public let nonBackgroundPixels: Int
    public let luminanceStandardDeviation: Double
}

public enum VellumContentFloor {
    public static func analyze(rgba: [UInt8], width: UInt32, height: UInt32) throws -> ContentStats {
        var stats = vellum_host_content_stats()
        stats.struct_size = structSize(vellum_host_content_stats.self)
        try rgba.withUnsafeBufferPointer { buffer in
            try check(vellum_host_content_floor(
                buffer.baseAddress, buffer.count, width, height, &stats))
        }
        return ContentStats(
            passesContentFloor: stats.passes_content_floor != 0,
            pixelCount: stats.pixel_count,
            uniqueColors: stats.unique_colors,
            opaquePixels: stats.opaque_pixels,
            nonBackgroundPixels: stats.non_background_pixels,
            luminanceStandardDeviation: stats.luminance_standard_deviation)
    }
}

public struct CapturedImage: Sendable {
    public let width: UInt32
    public let height: UInt32
    public let rowBytes: UInt32
    public let pixels: [UInt8]

    public func contentStats() throws -> ContentStats {
        try VellumContentFloor.analyze(rgba: pixels, width: width, height: height)
    }
}

public struct SurfaceInfo: Sendable {
    public let available: Bool
    public let nativeSurface: Bool
    public let fallback: Bool
    public let renderer: String
    public let backend: String
    public let adapter: String
    public let textureFormat: String
}

@MainActor
public final class VellumApplication {
    let handle: OpaquePointer

    public init(bundle: String, settleMaxTasks: UInt32 = 0) throws {
        var created: OpaquePointer?
        var bytes = Array(bundle.utf8)
        bytes.append(0)
        try bytes.withUnsafeBufferPointer { buffer in
            try buffer.withMemoryRebound(to: CChar.self) { characters in
                var config = vellum_host_app_config()
                config.struct_size = structSize(vellum_host_app_config.self)
                config.bundle = characters.baseAddress
                config.bundle_length = characters.count - 1
                config.settle_max_tasks = settleMaxTasks
                try check(vellum_host_app_create(&config, &created))
            }
        }
        guard let created else {
            throw VellumHostError(status: VELLUM_HOST_ERROR_FAILED, message: "no app handle")
        }
        handle = created
    }

    isolated deinit {
        _ = vellum_host_app_destroy(handle)
    }

    public var viewport: CGSize {
        var width: Float = 0
        var height: Float = 0
        guard vellum_host_app_viewport(handle, &width, &height) == VELLUM_HOST_OK else {
            return .zero
        }
        return CGSize(width: CGFloat(width), height: CGFloat(height))
    }

    @discardableResult
    public func pump(advanceMilliseconds: UInt64 = 16, maxTasks: UInt32 = 1024) throws -> PumpResult {
        var result = vellum_host_pump_result()
        result.struct_size = structSize(vellum_host_pump_result.self)
        try check(vellum_host_app_pump(handle, advanceMilliseconds, maxTasks, &result))
        return PumpResult(rendered: result.rendered != 0, idle: result.idle != 0,
                          tasksExecuted: result.tasks_executed)
    }

    public func dispatch(action: String, payloadJSON: String? = nil) throws {
        if let payloadJSON {
            try check(vellum_host_app_dispatch(handle, action, payloadJSON))
        } else {
            try check(vellum_host_app_dispatch(handle, action, nil))
        }
    }

    @discardableResult
    public func pointer(_ phase: PointerPhase, at point: CGPoint,
                        device: PointerDevice = .mouse, button: UInt32 = 0) throws -> EventOutcome {
        var event = vellum_host_pointer_event()
        event.struct_size = structSize(vellum_host_pointer_event.self)
        event.phase = phase.rawValue
        event.device = device.rawValue
        event.button = button
        event.x = Float(point.x)
        event.y = Float(point.y)
        var outcome: UInt32 = 0
        try check(vellum_host_app_pointer(handle, &event, &outcome))
        return EventOutcome(rawValue: outcome) ?? .ignored
    }

    public func focus(nodeID: String?) throws {
        if let nodeID {
            try check(vellum_host_app_focus(handle, nodeID))
        } else {
            try check(vellum_host_app_focus(handle, nil))
        }
    }

    @discardableResult
    public func key(_ key: String, isRepeat: Bool = false) throws -> EventOutcome {
        var outcome: UInt32 = 0
        try key.withCString { name in
            var event = vellum_host_key_event()
            event.struct_size = structSize(vellum_host_key_event.self)
            event.key = name
            event.repeat = isRepeat ? 1 : 0
            try check(vellum_host_app_key(handle, &event, &outcome))
        }
        return EventOutcome(rawValue: outcome) ?? .ignored
    }

    @discardableResult
    public func insertText(_ text: String) throws -> EventOutcome {
        var outcome: UInt32 = 0
        try check(vellum_host_app_insert_text(handle, text, &outcome))
        return EventOutcome(rawValue: outcome) ?? .ignored
    }

    public func performAccessibilityAction(nodeID: String, action: String) throws {
        try check(vellum_host_app_accessibility_perform(handle, nodeID, action))
    }

    public func accessibilityNodes() throws -> [AccessibilityNode] {
        var count = 0
        try check(vellum_host_app_accessibility_count(handle, &count))
        return try (0..<count).map { index in
            var node = vellum_host_accessibility_node()
            node.struct_size = structSize(vellum_host_accessibility_node.self)
            try check(vellum_host_app_accessibility_node(handle, index, &node))
            let actions = try (0..<node.action_count).map { actionIndex in
                var action: UnsafePointer<CChar>?
                try check(vellum_host_app_accessibility_action(handle, index, actionIndex, &action))
                return string(action)
            }
            return AccessibilityNode(
                nodeID: string(node.node_id), role: string(node.role),
                label: string(node.label), value: string(node.value),
                state: node.state, actions: actions, bounds: cgRect(node.bounds))
        }
    }

    public func textInputs() throws -> [TextInput] {
        var count = 0
        try check(vellum_host_app_text_input_count(handle, &count))
        return try (0..<count).map { index in
            var input = vellum_host_text_input()
            input.struct_size = structSize(vellum_host_text_input.self)
            try check(vellum_host_app_text_input(handle, index, &input))
            let start = Int(input.selection_start)
            let end = max(start, Int(input.selection_end))
            return TextInput(
                nodeID: string(input.node_id), value: string(input.value),
                placeholder: string(input.placeholder), selection: start..<end,
                focused: input.focused != 0, bounds: cgRect(input.bounds))
        }
    }
}

@MainActor
public final class VellumSurface {
    let handle: OpaquePointer
    private let metalLayer: CAMetalLayer?

    public init(width: UInt32, height: UInt32, scale: Float = 1,
                metalLayer: CAMetalLayer? = nil, fontDirectory: String? = nil,
                vsync: Bool = true) throws {
        var created: OpaquePointer?
        var config = vellum_host_surface_config()
        config.struct_size = structSize(vellum_host_surface_config.self)
        config.width = width
        config.height = height
        config.scale = scale
        config.metal_layer = metalLayer.map { Unmanaged.passUnretained($0).toOpaque() }
        config.vsync = vsync ? 1 : 0
        if let fontDirectory {
            try fontDirectory.withCString { directory in
                config.font_directory = directory
                try check(vellum_host_surface_create(&config, &created))
            }
        } else {
            try check(vellum_host_surface_create(&config, &created))
        }
        guard let created else {
            throw VellumHostError(status: VELLUM_HOST_ERROR_FAILED, message: "no surface handle")
        }
        handle = created
        self.metalLayer = metalLayer
    }

    isolated deinit {
        _ = vellum_host_surface_destroy(handle)
    }

    public var info: SurfaceInfo {
        get throws {
            var info = vellum_host_surface_info()
            info.struct_size = structSize(vellum_host_surface_info.self)
            try check(vellum_host_surface_info_get(handle, &info))
            return SurfaceInfo(
                available: info.available != 0, nativeSurface: info.native_surface != 0,
                fallback: info.fallback != 0, renderer: string(info.renderer),
                backend: string(info.backend), adapter: string(info.adapter),
                textureFormat: string(info.texture_format))
        }
    }

    public func resize(width: UInt32, height: UInt32, scale: Float) throws {
        try check(vellum_host_surface_resize(handle, width, height, scale))
    }

    public func render(_ application: VellumApplication) throws {
        try check(vellum_host_surface_render(handle, application.handle))
    }

    public func captureRGBA() throws -> CapturedImage {
        var image = vellum_host_image()
        image.struct_size = structSize(vellum_host_image.self)
        try check(vellum_host_surface_capture_rgba(handle, &image))
        let pixels = image.pixels.map { Array(UnsafeBufferPointer(start: $0, count: image.byte_count)) } ?? []
        return CapturedImage(width: image.width, height: image.height,
                             rowBytes: image.row_bytes, pixels: pixels)
    }
}
