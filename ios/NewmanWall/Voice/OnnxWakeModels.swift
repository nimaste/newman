import Foundation
#if canImport(OnnxRuntimeBindings)
import OnnxRuntimeBindings // the Swift package (microsoft/onnxruntime-swift-package-manager)
#else
import onnxruntime_objc // the CocoaPods name of the same Objective-C API
#endif

/// openWakeWord's three ONNX models on the iPad (ONNX Runtime's iOS package, CPU, one thread -
/// as Android's OnnxWakeModels.kt). The files come from the hub (WakeModelStore). Used only on
/// WallMic.queue once made.
///
/// ONNX Runtime's Objective-C API doesn't expose input shapes, so the wake model's frame count
/// (16 for openWakeWord's models, read from the shape on Android) is found by trying 16 and, if
/// the model wants another, reading it from the error (OnnxShape).
final class OnnxWakeModels: WakeModels, @unchecked Sendable {
    private let env: ORTEnv
    private let mel: ORTSession
    private let emb: ORTSession
    private let wake: ORTSession
    private let melIn: String, embIn: String, wakeIn: String
    private let melOut: String, embOut: String, wakeOut: String
    private(set) var featureFrames = 16

    enum Failure: Error { case noInput(String), noOutput(String) }

    init(directory dir: URL) throws {
        let environment = try ORTEnv(loggingLevel: .warning)
        env = environment
        let opts = try ORTSessionOptions()
        try opts.setIntraOpNumThreads(1)
        try opts.setGraphOptimizationLevel(.all)
        func session(_ name: String) throws -> ORTSession {
            try ORTSession(env: environment, modelPath: dir.appendingPathComponent("\(name).onnx").path, sessionOptions: opts)
        }
        mel = try session("melspectrogram")
        emb = try session("embedding_model")
        wake = try session("wakeword")
        func first(_ names: [String], _ what: String) throws -> String {
            guard let n = names.first else { throw Failure.noInput(what) }
            return n
        }
        melIn = try first(mel.inputNames(), "melspectrogram")
        embIn = try first(emb.inputNames(), "embedding_model")
        wakeIn = try first(wake.inputNames(), "wakeword")
        melOut = try first(mel.outputNames(), "melspectrogram")
        embOut = try first(emb.outputNames(), "embedding_model")
        wakeOut = try first(wake.outputNames(), "wakeword")
        featureFrames = probeFeatureFrames()
    }

    /// 16 unless the wake model says otherwise.
    private func probeFeatureFrames() -> Int {
        let zeros = Array(repeating: [Float](repeating: 0, count: 96), count: 16)
        do {
            _ = try run(wake, wakeIn, wakeOut, flat(zeros), shape: [1, 16, 96])
            return 16
        } catch {
            let n = OnnxShape.expectedDimension(from: "\(error)") ?? OnnxShape.expectedDimension(from: error.localizedDescription)
            WallMic.log.info("Wake model: \(n ?? 16, privacy: .public) frames (\(error.localizedDescription, privacy: .public))")
            return n ?? 16
        }
    }

    func melspectrogram(_ samples: [Float]) throws -> [[Float]] {
        // (1, 1, frames, 32)
        let out = try run(mel, melIn, melOut, samples, shape: [1, samples.count])
        let bands = WakeFeatures.melBands
        return stride(from: 0, to: out.count - out.count % bands, by: bands).map { Array(out[$0..<($0 + bands)]) }
    }

    func embedding(_ mel: [[Float]]) throws -> [Float] {
        // (1, 76, 32, 1) -> (1, 1, 1, 96)
        try run(emb, embIn, embOut, flat(mel), shape: [1, mel.count, mel.first?.count ?? WakeFeatures.melBands, 1])
    }

    func score(_ features: [[Float]]) throws -> Float {
        // (1, 16, 96) -> (1, 1)
        try run(wake, wakeIn, wakeOut, flat(features), shape: [1, features.count, features.first?.count ?? 96]).first ?? 0
    }

    private func flat(_ rows: [[Float]]) -> [Float] {
        var out: [Float] = []
        out.reserveCapacity(rows.count * (rows.first?.count ?? 0))
        for r in rows { out.append(contentsOf: r) }
        return out
    }

    private func run(_ s: ORTSession, _ input: String, _ output: String, _ values: [Float], shape: [Int]) throws -> [Float] {
        let data = values.withUnsafeBufferPointer { NSMutableData(bytes: $0.baseAddress, length: $0.count * MemoryLayout<Float>.stride) }
        let tensor = try ORTValue(tensorData: data, elementType: .float, shape: shape.map { NSNumber(value: $0) })
        let outputs = try s.run(withInputs: [input: tensor], outputNames: [output], runOptions: nil)
        guard let value = outputs[output] else { throw Failure.noOutput(output) }
        let raw = try value.tensorData() as Data
        return raw.withUnsafeBytes { Array($0.bindMemory(to: Float.self)) }
    }
}
