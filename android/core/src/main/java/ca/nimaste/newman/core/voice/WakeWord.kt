package ca.nimaste.newman.core.voice

/**
 * The three openWakeWord models, behind an interface so the buffering here can be tested without
 * ONNX Runtime (the wall app's OnnxWakeModels implements it):
 *   melspectrogram  raw samples (int16 values as floats) -> frames x 32 mel bands
 *   embedding       76 mel frames -> one 96-number embedding
 *   wake word       the last [featureFrames] embeddings -> a score 0..1 ("hey newman" or not)
 */
interface WakeModels {
    fun melspectrogram(samples: FloatArray): Array<FloatArray>
    fun embedding(mel: Array<FloatArray>): FloatArray
    fun score(features: Array<FloatArray>): Float
    val featureFrames: Int
}

/**
 * openWakeWord's streaming feature pipeline (openwakeword/utils.py AudioFeatures), one 80 ms
 * frame at a time: each frame (plus the 480 samples before it) adds mel frames, every frame adds
 * an embedding from the last 76 mel frames, and the wake-word model scores the last 16 embeddings.
 */
class WakeFeatures(private val models: WakeModels) {
    private val raw = FloatArray(Audio.FRAME + CONTEXT)
    private val mel = ArrayDeque<FloatArray>()
    private val features = ArrayDeque<FloatArray>()

    init {
        reset()
    }

    fun reset() {
        raw.fill(0f)
        mel.clear()
        repeat(MEL_WINDOW) { mel.addLast(FloatArray(MEL_BANDS) { 1f }) } // as openWakeWord starts (ones)
        features.clear()
    }

    /** One 1280-sample frame in; the wake-word score once enough has been heard, else null. */
    fun push(frame: ShortArray): Float? {
        require(frame.size == Audio.FRAME) { "frames are ${Audio.FRAME} samples" }
        System.arraycopy(raw, Audio.FRAME, raw, 0, CONTEXT)
        for (i in frame.indices) raw[CONTEXT + i] = frame[i].toFloat()
        for (row in models.melspectrogram(raw)) {
            mel.addLast(FloatArray(row.size) { row[it] / 10f + 2f }) // openWakeWord's melspec transform
            if (mel.size > MEL_MAX) mel.removeFirst()
        }
        if (mel.size >= MEL_WINDOW) {
            val window = Array(MEL_WINDOW) { mel[mel.size - MEL_WINDOW + it] }
            features.addLast(models.embedding(window))
            if (features.size > FEATURES_MAX) features.removeFirst()
        }
        val n = models.featureFrames
        if (features.size < n) return null
        return models.score(Array(n) { features[features.size - n + it] })
    }

    val embeddings: Int get() = features.size

    companion object {
        const val CONTEXT = 160 * 3
        const val MEL_BANDS = 32
        const val MEL_WINDOW = 76
        const val MEL_MAX = 10 * 97
        const val FEATURES_MAX = 120
    }
}

/**
 * When a stream of scores means "Hey Newman": a frame at or over the threshold right after one
 * nearly there (two frames in a row - one stray frame isn't enough), or one well over it. Then
 * quiet for [refractoryFrames] (2 s) so one phrase is one wake-up. The first [warmupFrames]
 * scores after a (re)start are ignored, as openWakeWord does.
 */
class WakeTrigger(
    var threshold: Float = 0.5f,
    private val refractoryFrames: Int = 25,
    private val warmupFrames: Int = 5,
) {
    private var previous = 0f
    private var quietFor = 0
    private var seen = 0

    fun reset() {
        previous = 0f
        quietFor = 0
        seen = 0
    }

    fun push(score: Float): Boolean {
        seen++
        if (seen <= warmupFrames) return false
        if (quietFor > 0) {
            quietFor--
            previous = 0f
            return false
        }
        val strong = minOf(0.95f, threshold + 0.25f)
        val hit = score >= strong || (score >= threshold && previous >= threshold * 0.8f)
        previous = score
        if (hit) {
            quietFor = refractoryFrames
            previous = 0f
        }
        return hit
    }
}
