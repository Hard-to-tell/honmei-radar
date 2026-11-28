import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins { `java-library`; id("org.jetbrains.kotlin.jvm") }

java { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
kotlin { compilerOptions { jvmTarget.set(JvmTarget.JVM_17) } }

dependencies {
    api("com.squareup.okhttp3:okhttp:4.12.0")
    api("org.nanohttpd:nanohttpd:2.3.1")
    testImplementation(kotlin("test"))
    testImplementation("org.xerial:sqlite-jdbc:3.46.1.3")
}

tasks.test { useJUnitPlatform(); maxHeapSize = "2g"; testLogging { events("failed"); showStandardStreams = true } }

tasks.register<JavaExec>("serveDev") {
    classpath = sourceSets["test"].runtimeClasspath
    mainClass.set("honmei.ServeMainKt")
}
