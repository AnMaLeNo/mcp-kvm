// kvm-capture: hands out frames of a UVC capture dongle on request (macOS).
//
//   kvm-capture <device name> <width> <height> <frames per second>
//
// ffmpeg's avfoundation input cannot choose among formats of the same size and
// ends up on the dongle's uncompressed mode (5 frames/s). This helper selects
// the compressed mode, which runs at 30 to 60 frames/s with far less delay.
//
// Protocol, on stdin/stdout:
//   at start    "R <width> <height> <fps> <fourcc>\n" when frames are flowing,
//               or "E <reason>\n" and exit.
//   request     "g <max age in ms>\n": a frame that arrived at most that long
//               before the request; waits for the next one if none qualifies.
//   reply       "F <age in us> <width> <height> <bytes per row> <length>\n"
//               followed by <length> bytes of BGRA pixels, or "E <reason>\n".
import AVFoundation
import Foundation

let out = FileHandle.standardOutput

func say(_ text: String) {
    out.write((text + "\n").data(using: .utf8)!)
}

func fail(_ reason: String) -> Never {
    say("E " + reason)
    exit(1)
}

func fourcc(_ code: FourCharCode) -> String {
    let bytes = [UInt8((code >> 24) & 255), UInt8((code >> 16) & 255), UInt8((code >> 8) & 255), UInt8(code & 255)]
    return String(bytes: bytes, encoding: .ascii) ?? "\(code)"
}

final class Grabber: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    let cond = NSCondition()
    var latest: CVPixelBuffer?
    var arrived: UInt64 = 0  // uptime in nanoseconds when `latest` came in

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard let pixels = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        cond.lock()
        latest = pixels
        arrived = DispatchTime.now().uptimeNanoseconds
        cond.broadcast()
        cond.unlock()
    }
}

let args = CommandLine.arguments
guard args.count == 5, let width = Int32(args[2]), let height = Int32(args[3]), let fps = Double(args[4]) else {
    fail("usage: kvm-capture <device name> <width> <height> <fps>")
}
let deviceName = args[1]

if AVCaptureDevice.authorizationStatus(for: .video) == .notDetermined {
    let asked = DispatchSemaphore(value: 0)
    AVCaptureDevice.requestAccess(for: .video) { _ in asked.signal() }
    asked.wait()
}
guard AVCaptureDevice.authorizationStatus(for: .video) == .authorized else {
    fail("camera access denied: allow it for the application running this server in System Settings > Privacy & Security > Camera")
}

let devices = AVCaptureDevice.DiscoverySession(deviceTypes: [.external], mediaType: .video,
                                               position: .unspecified).devices
guard let device = devices.first(where: { $0.localizedName == deviceName }) else {
    let names = devices.map { "\"\($0.localizedName)\"" }.joined(separator: ", ")
    fail("capture device \"\(deviceName)\" not found; external cameras present: \(names.isEmpty ? "none" : names)")
}

// Among formats of the requested size, take the one that can run fastest: that
// is the compressed mode. Then run it at the requested rate, or its closest.
var best: (format: AVCaptureDevice.Format, rate: Double)?
for format in device.formats {
    let dims = CMVideoFormatDescriptionGetDimensions(format.formatDescription)
    guard dims.width == width, dims.height == height else { continue }
    let top = format.videoSupportedFrameRateRanges.map { $0.maxFrameRate }.max() ?? 0
    if best == nil || top > best!.rate { best = (format, top) }
}
guard let chosen = best else {
    fail("capture device \"\(deviceName)\" has no \(width)x\(height) mode")
}
// The dongle only accepts the exact frame durations it advertises.
guard let range = chosen.format.videoSupportedFrameRateRanges
    .min(by: { abs($0.maxFrameRate - fps) < abs($1.maxFrameRate - fps) }) else {
    fail("capture device \"\(deviceName)\" advertises no frame rate")
}
let rate = range.maxFrameRate

let grabber = Grabber()
let session = AVCaptureSession()
let output = AVCaptureVideoDataOutput()
output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
output.alwaysDiscardsLateVideoFrames = true
output.setSampleBufferDelegate(grabber, queue: DispatchQueue(label: "kvm-capture"))

do {
    let input = try AVCaptureDeviceInput(device: device)
    session.beginConfiguration()
    guard session.canAddInput(input), session.canAddOutput(output) else { fail("cannot attach to the capture device") }
    session.addInput(input)
    session.addOutput(output)
    session.commitConfiguration()
    // The device stays locked across startRunning, otherwise the session
    // replaces the chosen format with its own preset.
    try device.lockForConfiguration()
    device.activeFormat = chosen.format
    device.activeVideoMinFrameDuration = range.minFrameDuration
    device.activeVideoMaxFrameDuration = range.minFrameDuration
    session.startRunning()
    device.unlockForConfiguration()
} catch {
    fail("cannot open capture device \"\(deviceName)\": \(error.localizedDescription)")
}

grabber.cond.lock()
let firstFrameDeadline = Date().addingTimeInterval(8)
while grabber.latest == nil {
    if !grabber.cond.wait(until: firstFrameDeadline) { break }
}
let started = grabber.latest != nil
grabber.cond.unlock()
guard started else { fail("capture device \"\(deviceName)\" opened but sent no frame") }
say("R \(width) \(height) \(Int(rate)) \(fourcc(CMFormatDescriptionGetMediaSubType(chosen.format.formatDescription)))")

while let line = readLine() {
    let parts = line.split(separator: " ")
    guard parts.count == 2, parts[0] == "g", let maxAgeMs = UInt64(parts[1]) else {
        say("E bad request")
        continue
    }
    let now = DispatchTime.now().uptimeNanoseconds
    let maxAge = maxAgeMs * 1_000_000
    let oldest = now > maxAge ? now - maxAge : 0
    let deadline = Date().addingTimeInterval(5)
    grabber.cond.lock()
    while grabber.arrived < oldest {
        if !grabber.cond.wait(until: deadline) { break }
    }
    let pixels = grabber.arrived >= oldest ? grabber.latest : nil
    let arrived = grabber.arrived
    grabber.cond.unlock()
    guard let frame = pixels else {
        say("E no new frame within 5 s")
        continue
    }
    CVPixelBufferLockBaseAddress(frame, .readOnly)
    let rowBytes = CVPixelBufferGetBytesPerRow(frame)
    let rows = CVPixelBufferGetHeight(frame)
    let age = (DispatchTime.now().uptimeNanoseconds - arrived) / 1000
    say("F \(age) \(CVPixelBufferGetWidth(frame)) \(rows) \(rowBytes) \(rowBytes * rows)")
    if let base = CVPixelBufferGetBaseAddress(frame) {
        out.write(Data(bytesNoCopy: base, count: rowBytes * rows, deallocator: .none))
    }
    CVPixelBufferUnlockBaseAddress(frame, .readOnly)
}
