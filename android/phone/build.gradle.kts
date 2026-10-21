import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ksp)
    alias(libs.plugins.hilt)
}

// Push (FCM) is on only when phone/google-services.json is in place; without it the app builds
// and runs, just without notifications.
val fcmEnabled = file("google-services.json").exists()
if (fcmEnabled) {
    apply(plugin = "com.google.gms.google-services")
}

// Release signing from the environment or android/keystore.properties (never committed).
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
    namespace = "ca.nimaste.newman"
    compileSdk = 35

    defaultConfig {
        applicationId = "ca.nimaste.newman"
        minSdk = 26
        targetSdk = 35
        versionCode = resolvedVersionCode
        versionName = resolvedVersionName
        buildConfigField("boolean", "FCM_ENABLED", fcmEnabled.toString())
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
            resValue("string", "app_name", "Newman Dev")
        }
        release {
            isMinifyEnabled = false
            resValue("string", "app_name", "Newman")
            signingConfig = signingConfigs.findByName("release")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    // The live camera player page is shared with the iOS app: packaged from its one copy in
    // mobile-shared/ (served to the WebView through WebViewAssetLoader).
    sourceSets["main"].assets.srcDir(rootProject.file("../mobile-shared"))

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

    // Compose semantics tests (TalkBack labels, touch targets) run on the JVM with Robolectric.
    testOptions {
        unitTests.isIncludeAndroidResources = true
    }
}

dependencies {
    implementation(project(":core"))

    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.graphics)
    implementation(libs.compose.foundation)
    implementation(libs.compose.material3)
    implementation(libs.compose.ui.tooling.preview)
    debugImplementation(libs.compose.ui.tooling)

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.fragment.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.process)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.hilt.navigation.compose)
    implementation(libs.androidx.biometric)
    implementation(libs.androidx.webkit)

    // Home-screen widgets and Quick Settings tiles (widgets/): Glance, refreshed by WorkManager.
    implementation(libs.androidx.glance.appwidget)
    implementation(libs.androidx.glance.material3)
    implementation(libs.androidx.work.runtime)

    implementation(libs.hilt.android)
    ksp(libs.hilt.compiler)

    implementation(libs.coil.compose)
    implementation(libs.coil.network.okhttp)

    implementation(platform(libs.firebase.bom))
    implementation(libs.firebase.messaging)

    // Location sharing: the fused provider where Google Play services is there (LocationManager
    // otherwise), and OpenStreetMap maps (no API key, no Google needed).
    implementation(libs.play.services.location)
    implementation(libs.osmdroid)

    // Scanning the hub's QR code (ZXing; no Google Play services).
    implementation(libs.zxing.embedded)

    // Alert videos: streamed with seeking through the hub's OkHttp client (certificate, headers).
    implementation(libs.media3.exoplayer)
    implementation(libs.media3.ui)
    implementation(libs.media3.datasource.okhttp)

    testImplementation(libs.junit)
    testImplementation(libs.robolectric)
    testImplementation(platform(libs.compose.bom))
    testImplementation(libs.compose.ui.test.junit4)
    debugImplementation(libs.compose.ui.test.manifest)
}
