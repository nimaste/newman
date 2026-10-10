plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.serialization)
}

// Shared by the phone and the wall app. No Google Play services / Firebase here: the wall app
// runs on Amazon Fire tablets.
android {
    namespace = "ca.nimaste.newman.core"
    compileSdk = 35

    defaultConfig {
        minSdk = 26
        consumerProguardFiles("consumer-rules.pro")
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
        unitTests.all {
            // Optional read-only check against a real hub: -Pnewman.liveHub=http://10.1.1.2:8093
            val hub = project.findProperty("newman.liveHub") as String?
            if (hub != null) it.systemProperty("newman.liveHub", hub)
            // On the hub's own machine only: its maintenance token for read-only GETs.
            val localToken = project.findProperty("newman.localTokenFile") as String?
            if (localToken != null) it.systemProperty("newman.localTokenFile", localToken)
        }
    }
}

dependencies {
    api(libs.kotlinx.serialization.json)
    api(libs.kotlinx.coroutines.android)
    api(libs.okhttp)
    api(libs.retrofit)
    api(libs.javax.inject)
    implementation(libs.retrofit.kotlinx.serialization)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.security.crypto)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver)
}
