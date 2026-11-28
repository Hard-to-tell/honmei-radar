plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "honmei.app"
    compileSdk = 35
    defaultConfig {
        applicationId = "io.github.hardtotell.honmei"
        minSdk = 26
        targetSdk = 35
        versionCode = (System.getenv("HONMEI_VERSION_CODE") ?: "1").toInt()
        versionName = System.getenv("HONMEI_VERSION_NAME") ?: "0.1.0"
    }
    signingConfigs {
        create("release") {
            val ks = System.getenv("HONMEI_KEYSTORE")
            if (ks != null && file(ks).exists()) {
                storeFile = file(ks)
                storePassword = System.getenv("HONMEI_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("HONMEI_KEY_ALIAS") ?: "honmei"
                keyPassword = System.getenv("HONMEI_KEY_PASSWORD") ?: System.getenv("HONMEI_KEYSTORE_PASSWORD")
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            val rel = signingConfigs.getByName("release")
            signingConfig = if (rel.storeFile != null) rel else signingConfigs.getByName("debug")
        }
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    packaging { resources { excludes += setOf("META-INF/AL2.0", "META-INF/LGPL2.1", "META-INF/*.kotlin_module", "META-INF/versions/**") } }
}

dependencies {
    implementation(project(":core"))
}
