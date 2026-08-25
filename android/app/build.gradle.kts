plugins {
    id("com.android.application")
}

android {
    namespace = "com.controlefinanceiro.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.controlefinanceiro.app"
        minSdk = 24
        targetSdk = 34
        versionCode = 10
        versionName = "3.4"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.8.0")
    implementation("com.google.android.material:material:1.14.0")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.2.0")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("androidx.biometric:biometric:1.1.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
}

// O `kotlinOptions { jvmTarget = "17" }` de antes deixou de existir: passar a
// versão como texto virou erro no Kotlin 2.x, e a configuração mudou para cá.
// O bloco continua valendo com o Kotlin embutido do AGP 9 — o que mudou foi
// quem fornece o compilador, não onde se configura o alvo da JVM.
kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}
