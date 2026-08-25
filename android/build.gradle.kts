// O plugin org.jetbrains.kotlin.android não aparece aqui de propósito: do
// AGP 9 em diante o suporte a Kotlin vem embutido, e declarar o plugin faz o
// build falhar com "no longer required for Kotlin support since AGP 9.0".
// Quem escolhe a versão do Kotlin agora é o AGP.
plugins {
    id("com.android.application") version "9.3.2" apply false
}
