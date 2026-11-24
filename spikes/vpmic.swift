import AVFoundation
import Foundation

setvbuf(stdout, nil, _IONBF, 0)
let processing = !CommandLine.arguments.contains("--raw")
let engine = AVAudioEngine()
let input = engine.inputNode
do {
    try input.setVoiceProcessingEnabled(processing)
} catch {
    FileHandle.standardError.write("voice processing failed: \(error)\n".data(using: .utf8)!)
    exit(2)
}
if processing {
    input.voiceProcessingOtherAudioDuckingConfiguration =
        AVAudioVoiceProcessingOtherAudioDuckingConfiguration(enableAdvancedDucking: false, duckingLevel: .min)
}
_ = engine.outputNode
let inFormat = input.outputFormat(forBus: 0)
FileHandle.standardError.write("input \(inFormat)\n".data(using: .utf8)!)
let mono = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: inFormat.sampleRate, channels: 1, interleaved: false)!
let out = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!
let converter = AVAudioConverter(from: mono, to: out)!
input.installTap(onBus: 0, bufferSize: 960, format: inFormat) { buffer, _ in
    let frames = buffer.frameLength
    guard let src = buffer.floatChannelData, let first = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: frames) else { return }
    first.frameLength = frames
    first.floatChannelData![0].update(from: src[0], count: Int(frames))
    let capacity = AVAudioFrameCount(Double(frames) * 16000 / inFormat.sampleRate) + 16
    guard let converted = AVAudioPCMBuffer(pcmFormat: out, frameCapacity: capacity) else { return }
    var fed = false
    var err: NSError?
    converter.convert(to: converted, error: &err) { _, status in
        if fed { status.pointee = .noDataNow; return nil }
        fed = true
        status.pointee = .haveData
        return first
    }
    let n = Int(converted.frameLength) * 2
    if n > 0 { FileHandle.standardOutput.write(Data(bytes: converted.int16ChannelData![0], count: n)) }
}
do {
    try engine.start()
} catch {
    FileHandle.standardError.write("engine failed: \(error)\n".data(using: .utf8)!)
    exit(3)
}
signal(SIGINT) { _ in exit(0) }
signal(SIGTERM) { _ in exit(0) }
RunLoop.main.run()
