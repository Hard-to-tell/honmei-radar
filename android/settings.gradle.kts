pluginManagement { repositories { gradlePluginPortal(); google(); mavenCentral() } }
dependencyResolutionManagement { repositories { google(); mavenCentral() } }
rootProject.name = "honmei-android"
include(":core")
// 安卓壳只在装了 Android SDK 的机器上构建（GitHub Actions）；本地只编译、测试 core
if (System.getenv("ANDROID_HOME") != null || System.getenv("ANDROID_SDK_ROOT") != null || file("local.properties").exists()) {
    include(":app")
}
