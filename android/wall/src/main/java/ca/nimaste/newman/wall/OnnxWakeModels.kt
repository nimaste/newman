package ca.nimaste.newman.wall

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import ca.nimaste.newman.core.voice.WakeModels
import java.io.File
import java.nio.FloatBuffer

/**
 * openWakeWord's three ONNX models on the tablet (ONNX Runtime, CPU, one thread each - about
 * 1-2 ms per 80 ms frame on a mid-range tablet). The files come from the hub (WakeModelStore).
 */
class OnnxWakeModels(dir: File) : WakeModels, AutoCloseable {
    private val env = OrtEnvironment.getEnvironment()
    private val opts = OrtSession.SessionOptions().apply {
        setIntraOpNumThreads(1)
        setInterOpNumThreads(1)
    }
    private val mel = env.createSession(File(dir, "melspectrogram.onnx").absolutePath, opts)
    private val emb = env.createSession(File(dir, "embedding_model.onnx").absolutePath, opts)
    private val wake = env.createSession(File(dir, "wakeword.onnx").absolutePath, opts)
    private val melIn = mel.inputNames.first()
    private val embIn = emb.inputNames.first()
    private val wakeIn = wake.inputNames.first()

    override val featureFrames: Int =
        ((wake.inputInfo[wakeIn]?.info as? ai.onnxruntime.TensorInfo)?.shape?.getOrNull(1)?.toInt())
            ?.takeIf { it > 0 } ?: 16

    override fun melspectrogram(samples: FloatArray): Array<FloatArray> {
        OnnxTensor.createTensor(env, FloatBuffer.wrap(samples), longArrayOf(1, samples.size.toLong())).use { t ->
            mel.run(mapOf(melIn to t)).use { r ->
                // (1, 1, frames, 32)
                @Suppress("UNCHECKED_CAST")
                val out = r[0].value as Array<Array<Array<FloatArray>>>
                return out[0][0]
            }
        }
    }

    override fun embedding(mel: Array<FloatArray>): FloatArray {
        val flat = FloatArray(mel.size * mel[0].size)
        var i = 0
        for (row in mel) for (v in row) flat[i++] = v
        OnnxTensor.createTensor(env, FloatBuffer.wrap(flat), longArrayOf(1, mel.size.toLong(), mel[0].size.toLong(), 1)).use { t ->
            emb.run(mapOf(embIn to t)).use { r ->
                // (1, 1, 1, 96)
                @Suppress("UNCHECKED_CAST")
                val out = r[0].value as Array<Array<Array<FloatArray>>>
                return out[0][0][0]
            }
        }
    }

    override fun score(features: Array<FloatArray>): Float {
        val flat = FloatArray(features.size * features[0].size)
        var i = 0
        for (row in features) for (v in row) flat[i++] = v
        OnnxTensor.createTensor(env, FloatBuffer.wrap(flat), longArrayOf(1, features.size.toLong(), features[0].size.toLong())).use { t ->
            wake.run(mapOf(wakeIn to t)).use { r ->
                val v = r[0].value
                @Suppress("UNCHECKED_CAST")
                return when (v) {
                    is Array<*> -> (v as Array<FloatArray>)[0][0]
                    is FloatArray -> v[0]
                    else -> 0f
                }
            }
        }
    }

    override fun close() {
        mel.close()
        emb.close()
        wake.close()
        opts.close()
    }
}
