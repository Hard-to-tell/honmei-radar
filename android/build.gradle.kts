// Kotlin 插件和安卓插件放在同一个类加载器里（否则 Kotlin 插件找不到安卓插件）
buildscript {
    repositories { google(); mavenCentral(); gradlePluginPortal() }
    dependencies {
        classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:2.0.21")
        val hasSdk = System.getenv("ANDROID_HOME") != null || System.getenv("ANDROID_SDK_ROOT") != null || file("local.properties").exists()
        if (hasSdk) classpath("com.android.tools.build:gradle:8.7.3")
    }
}
