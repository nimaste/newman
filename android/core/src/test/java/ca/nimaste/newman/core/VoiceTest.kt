package ca.nimaste.newman.core

import ca.nimaste.newman.core.voice.Audio
import ca.nimaste.newman.core.voice.Endpointer
import ca.nimaste.newman.core.voice.Framer
import ca.nimaste.newman.core.voice.NoiseFloor
import ca.nimaste.newman.core.voice.Utterance
import ca.nimaste.newman.core.voice.VoicePageConfig
import ca.nimaste.newman.core.voice.WakeConfig
import ca.nimaste.newman.core.voice.WakeFeatures
import ca.nimaste.newman.core.voice.WakeModels
import ca.nimaste.newman.core.voice.WakeTrigger
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.PI
import kotlin.math.sin
import kotlin.random.Random

/** The wall's voice: framing, loudness, end-of-speech detection, wake-word buffering and triggering. */
class VoiceTest {

    private fun tone(n: Int, amp: Double, freq: Double = 220.0) =
        ShortArray(n) { (sin(2 * PI * freq * it / Audio.RATE) * amp).toInt().toShort() }

    private fun noise(n: Int, amp: Int, seed: Int = 1): ShortArray {
        val r = Random(seed)
        return ShortArray(n) { (r.nextInt(-amp, amp + 1)).toShort() }
    }

    @Test
    fun framesComeOutWholeWhateverTheReadSizes() {
        val f = Framer(1280)
        val out = mutableListOf<ShortArray>()
        val data = ShortArray(5000) { it.toShort() }
        var i = 0
        for (size in listOf(100, 1500, 7, 2000, 1393)) {
            val chunk = data.copyOfRange(i, i + size)
            f.push(chunk) { out += it }
            i += size
        }
        assertEquals(3, out.size) // 5000 = 3 x 1280 + 1160 kept
        assertEquals(0.toShort(), out[0][0])
        assertEquals(1280.toShort(), out[1][0])
        assertEquals(3839.toShort(), out[2][1279])
        f.push(ShortArray(120) { 7 }) { out += it }
        assertEquals(4, out.size)
        assertEquals(3840.toShort(), out[3][0])
        assertEquals(7.toShort(), out[3][1279])
    }

    @Test
    fun pcmIsLittleEndianAndLevelsAreSensible() {
        assertArrayEquals(byteArrayOf(0x34, 0x12, 0xFF.toByte(), 0xFF.toByte()), Audio.pcm16le(shortArrayOf(0x1234, -1)))
        assertEquals(0f, Audio.level(ShortArray(1280)))
        val loud = Audio.level(tone(1280, 16000.0))
        val quiet = Audio.level(tone(1280, 300.0))
        assertTrue(loud > 0.8f)
        assertTrue(quiet in 0.1f..0.4f)
        assertTrue(Audio.zeroCrossings(noise(1280, 3000)) > 0.4)
        assertTrue(Audio.zeroCrossings(tone(1280, 3000.0)) < 0.05)
        val u = Utterance(maxSeconds = 1)
        repeat(20) { u.add(ShortArray(1280) { 1 }) }
        assertEquals(16000, u.size) // capped
        assertEquals(32000, u.bytes().size)
    }

    @Test
    fun theNoiseFloorIsTheQuietEndOfTheRoom() {
        val nf = NoiseFloor(10)
        assertEquals(200.0, nf.floor(), 0.0)
        listOf(100.0, 120.0, 90.0, 3000.0, 2500.0, 110.0, 95.0, 4000.0, 105.0, 115.0).forEach(nf::add)
        assertTrue(nf.floor() in 90.0..110.0)
    }

    @Test
    fun endpointingEndsAfterSpeechThenSilence() {
        val e = Endpointer(noiseFloor = 150.0)
        val results = mutableListOf<Endpointer.Result>()
        repeat(3) { results += e.push(noise(1280, 200, it)) } // room noise
        repeat(15) { results += e.push(tone(1280, 4000.0)) } // 1.2 s of "speech"
        assertFalse(results.any { it != Endpointer.Result.CONTINUE })
        assertTrue(e.started)
        var r = Endpointer.Result.CONTINUE
        var quietFrames = 0
        while (r == Endpointer.Result.CONTINUE && quietFrames < 50) {
            r = e.push(noise(1280, 200, quietFrames))
            quietFrames++
        }
        assertEquals(Endpointer.Result.END, r)
        assertEquals(10, quietFrames) // 800 ms of quiet
    }

    @Test
    fun nobodySpeaksOrSomeoneNeverStops() {
        val quiet = Endpointer(noiseFloor = 150.0)
        var r = Endpointer.Result.CONTINUE
        var n = 0
        while (r == Endpointer.Result.CONTINUE) {
            r = quiet.push(noise(1280, 200, n++))
        }
        assertEquals(Endpointer.Result.NO_SPEECH, r)
        assertEquals(63, n) // 5 s
        val talker = Endpointer(noiseFloor = 150.0)
        r = Endpointer.Result.CONTINUE
        n = 0
        while (r == Endpointer.Result.CONTINUE) {
            r = talker.push(tone(1280, 5000.0))
            n++
        }
        assertEquals(Endpointer.Result.MAX, r)
        assertEquals(100, n) // 8 s
    }

    @Test
    fun clicksAndHissAreNotSpeech() {
        val e = Endpointer(noiseFloor = 150.0)
        // one loud frame between quiet ones never "starts" speech
        repeat(6) { i -> e.push(if (i % 3 == 0) tone(1280, 6000.0) else noise(1280, 150, i)) }
        assertFalse(e.started)
        // a hiss just over the threshold (high zero-crossing rate) isn't a voice
        assertFalse(e.isSpeech(e.threshold * 1.3, 0.6))
        assertTrue(e.isSpeech(e.threshold * 1.3, 0.1))
        assertTrue(e.isSpeech(e.threshold * 3, 0.6)) // but a loud one is
        // in a loud room the threshold follows the floor
        assertTrue(Endpointer(noiseFloor = 2000.0).threshold >= 4000)
        assertEquals(150.0, Endpointer(noiseFloor = 10.0).threshold, 0.0)
    }

    @Test
    fun theTriggerNeedsTwoFramesOrOneStrongOne() {
        val t = WakeTrigger(threshold = 0.5f, refractoryFrames = 3, warmupFrames = 2)
        assertFalse(t.push(0.99f)) // warm-up ignored
        assertFalse(t.push(0.99f))
        assertFalse(t.push(0.6f)) // one frame over: not yet
        assertTrue(t.push(0.55f)) // two in a row
        assertFalse(t.push(0.99f)) // refractory
        assertFalse(t.push(0.99f))
        assertFalse(t.push(0.99f))
        assertTrue(t.push(0.9f)) // one strong frame is enough
        repeat(3) { t.push(0f) }
        assertFalse(t.push(0.3f))
        assertFalse(t.push(0.6f)) // previous 0.3 < 0.4
        repeat(3) { t.push(0f) }
        t.threshold = 0.35f // a "high" sensitivity tablet
        assertFalse(t.push(0.3f))
        assertTrue(t.push(0.36f))
    }

    /** Fake models: mel = 8 frames of the frame's mean level; embedding = the window's mean; score = last feature. */
    private class FakeModels(override val featureFrames: Int = 16) : WakeModels {
        var melCalls = 0
        val melInputSizes = mutableListOf<Int>()
        override fun melspectrogram(samples: FloatArray): Array<FloatArray> {
            melCalls++
            melInputSizes += samples.size
            val level = samples.takeLast(1280).map { kotlin.math.abs(it) }.average().toFloat()
            return Array(8) { FloatArray(32) { level } }
        }

        override fun embedding(mel: Array<FloatArray>): FloatArray {
            assertEquals(76, mel.size)
            return FloatArray(96) { mel.last()[0] }
        }

        override fun score(features: Array<FloatArray>): Float {
            assertEquals(featureFrames, features.size)
            return features.last()[0]
        }
    }

    @Test
    fun wakeFeaturesBufferLikeOpenWakeWord() {
        val models = FakeModels()
        val w = WakeFeatures(models)
        val scores = (1..20).map { w.push(ShortArray(1280) { 100 }) }
        // an embedding per frame from the first (the mel buffer starts full of ones), a score from the 16th
        assertTrue(scores.take(15).all { it == null })
        assertTrue(scores.drop(15).all { it != null })
        assertTrue(models.melInputSizes.all { it == 1280 + 480 }) // each frame with the 30 ms before it
        assertEquals(100f / 10f + 2f, scores.last()!!, 0.001f) // openWakeWord's x/10 + 2 transform
        assertNull(WakeFeatures(FakeModels(featureFrames = 28)).push(ShortArray(1280)))
        w.reset()
        assertEquals(0, w.embeddings)
    }

    @Test
    fun configsParse() {
        val c = WakeConfig.parse("""{"name":"hey_newman","version":"ab12","threshold":0.45,"enabled":true,
            "files":{"melspectrogram":"api/voice/wakeword/melspectrogram.onnx","embedding_model":"api/voice/wakeword/embedding_model.onnx",
            "wakeword":"api/voice/wakeword/hey_newman.onnx"},"extra":1}""")
        assertTrue(c.complete)
        assertEquals(0.45f, c.threshold, 0f)
        assertFalse(WakeConfig.parse("""{"version":"x","files":{}}""").complete)
        assertEquals(VoicePageConfig(true, 0.6f, "v1"), VoicePageConfig.parse("""{"enabled":true,"threshold":0.6,"version":"v1"}"""))
        assertEquals(VoicePageConfig(), VoicePageConfig.parse("not json"))
    }
}
