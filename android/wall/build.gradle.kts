import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

// The wall tablet app. No Google Play services and no Firebase anywhere in this module or its
// dependencies: Amazon Fire tablets (Fire OS 8) are the first target.

val keystoreProps = Properties().apply {
    rootProject.file("keystore.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) }
}
fun signingValue(env: String, key: String): String? = System.getenv(env) ?: keystoreProps.getProperty(key)
val releaseStore = signingValue("ANDROID_KEYSTORE_PATH", "storeFile")

// Derived straight from the git tag at build time so this can never drift
// out of sync with a GitHub release -- CI's release build runs on the exact
// tag commit, so `git describe --exact-match` resolves it there; anywhere
// else (local dev builds, a non-tag commit) it falls back to "dev"/1.
fun releaseVersion(): Pair<String, Int> {
    val tag = try {
        val process = ProcessBuilder("git", "describe", "--tags", "--exact-match")
            .directory(rootDir)
            .redirectErrorStream(true)
            .start()
        val output = process.inputStream.bufferedReader().readText().trim()
        if (process.waitFor() == 0) output else null
    } catch (e: Exception) {
        null
    }?.removePrefix("v")

    // A pre-release tag (2026.10.4-rc1) gets a lower versionCode than its
    // release: the version's number times 100, plus the rc number for a
    // pre-release and 99 for the release, so rc1 < rc2 < 2026.10.4.
    val rc = tag?.substringAfter("-rc", "")?.toIntOrNull()
    val parts = tag?.substringBefore("-")?.split(".")?.mapNotNull { it.toIntOrNull() }
    if (tag != null && parts?.size == 3 && (rc != null || !tag.contains("-"))) {
        val (major, minor, patch) = parts
        return tag to ((major * 10000 + minor * 100 + patch) * 100 + (rc?.coerceAtMost(98) ?: 99))
    }
    return "dev" to 1
}
val (resolvedVersionName, resolvedVersionCode) = releaseVersion()

android {
    namespace = "ca.nimaste.newman.wall"
    compileSdk = 35

    defaultConfig {
        applicationId = "ca.nimaste.newman.wall"
        minSdk = 26
        targetSdk = 35
        versionCode = resolvedVersionCode
        versionName = resolvedVersionName
        // ONNX Runtime (voice) ships native code per CPU type: ARM tablets only keeps the APK small.
        ndk { abiFilters += listOf("arm64-v8a", "armeabi-v7a") }
    }

    signingConfigs {
        if (releaseStore != null) {
            create("release") {
                storeFile = rootProject.file(releaseStore)
                storePassword = signingValue("ANDROID_KEYSTORE_PASSWORD", "storePassword")
                keyAlias = signingValue("ANDROID_KEY_ALIAS", "keyAlias")
                keyPassword = signingValue("ANDROID_KEY_PASSWORD", "keyPassword")
                    ?: signingValue("ANDROID_KEYSTORE_PASSWORD", "storePassword")
            }
        }
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".dev"
            versionNameSuffix = "-dev"
            resValue("string", "app_name", "Newman Wall Dev")
        }
        release {
            isMinifyEnabled = false
            resValue("string", "app_name", "Newman Wall")
            signingConfig = signingConfigs.findByName("release")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    lint {
        abortOnError = false
    }
}

dependencies {
    implementation(project(":core"))

    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.foundation)
    implementation(libs.compose.material3)

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.webkit)
    // The battery report with the screen off (BatteryWork): no Google Play services needed.
    implementation(libs.androidx.work.runtime)
    // "Hey Newman" on the tablet: openWakeWord's ONNX models (no Google Play services involved).
    implementation("com.microsoft.onnxruntime:onnxruntime-android:1.20.0")
    // Scanning the hub's QR code: ZXing with its own Camera1 screen - works on Fire OS (no Google Play services).
    implementation(libs.zxing.embedded)
}

// Guard: fail the build if anything pulls Google Play services or Firebase into the wall app.
afterEvaluate {
    tasks.matching { it.name == "preBuild" }.configureEach {
        doFirst {
            val bad = configurations.getByName("releaseRuntimeClasspath").incoming.resolutionResult.allComponents
                .mapNotNull { (it.id as? org.gradle.api.artifacts.component.ModuleComponentIdentifier)?.group }
                .filter { it.startsWith("com.google.android.gms") || it.startsWith("com.google.firebase") }
                .distinct()
            if (bad.isNotEmpty()) throw GradleException("The wall app must not depend on Google Play services / Firebase: $bad")
        }
    }
}
