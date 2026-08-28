package com.controlefinanceiro.app

import android.app.Application
import android.content.Context
import android.os.Build
import android.os.Process
import java.io.PrintWriter
import java.io.StringWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.system.exitProcess

private const val PREFS = "financerto_falhas"
private const val CHAVE_TEXTO = "ultima_falha"
private const val CHAVE_NA_ABERTURA = "falhou_na_abertura"
private const val CHAVE_ABRINDO = "abrindo"

/**
 * Guarda o motivo da última vez que o app fechou sozinho.
 *
 * Quando isso acontece no celular de outra pessoa não há como olhar o log do
 * Android, e o relato que chega é sempre o mesmo: "abre e fecha". O motivo
 * fica gravado aqui e aparece na tela de endereço na abertura seguinte, que é
 * uma tela onde dá para ler com calma e tirar um print.
 *
 * A gravação é síncrona de propósito: o processo morre logo depois, e um
 * `apply()` pode não chegar ao disco a tempo.
 */
object RegistroDeFalhas {

    fun instalar(app: Application) {
        val anterior = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, erro ->
            try {
                guardar(app, erro)
            } catch (e: Throwable) {
                // Registrar a falha nunca pode virar uma segunda falha.
            }
            if (anterior != null) {
                anterior.uncaughtException(thread, erro)
            } else {
                Process.killProcess(Process.myPid())
                exitProcess(10)
            }
        }
    }

    /**
     * A MainActivity avisa quando começa a abrir e quando a página terminou de
     * carregar. Se a falha cai no meio disso, abrir de novo daria no mesmo, e
     * quem instalou fica preso num app que abre e fecha sem nunca mostrar nada.
     */
    fun marcarAbrindo(context: Context) = prefs(context).edit().putBoolean(CHAVE_ABRINDO, true).commit()

    fun marcarAberto(context: Context) = prefs(context).edit().putBoolean(CHAVE_ABRINDO, false).commit()

    fun falhouNaAbertura(context: Context): Boolean =
        prefs(context).getBoolean(CHAVE_NA_ABERTURA, false)

    /** Texto para mostrar na tela, ou null se a última abertura foi normal. */
    fun ultima(context: Context): String? =
        prefs(context).getString(CHAVE_TEXTO, null)?.ifBlank { null }

    fun limpar(context: Context) {
        prefs(context).edit()
            .remove(CHAVE_TEXTO)
            .remove(CHAVE_NA_ABERTURA)
            .putBoolean(CHAVE_ABRINDO, false)
            .commit()
    }

    private fun guardar(context: Context, erro: Throwable) {
        val quando = SimpleDateFormat("dd/MM/yyyy HH:mm", Locale.getDefault()).format(Date())
        val versao = try {
            context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "?"
        } catch (e: Exception) {
            "?"
        }
        val pilha = StringWriter().also { erro.printStackTrace(PrintWriter(it)) }.toString()
        // Só as primeiras linhas: o suficiente para saber onde quebrou, e curto
        // o bastante para caber num print de celular.
        val resumo = pilha.lineSequence()
            .filter { it.isNotBlank() }
            .take(12)
            .joinToString("\n") { it.trim() }

        val texto = buildString {
            appendLine("$quando — app $versao")
            appendLine("${Build.MANUFACTURER} ${Build.MODEL}, Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})")
            appendLine()
            append(resumo)
        }

        prefs(context).edit()
            .putString(CHAVE_TEXTO, texto)
            .putBoolean(CHAVE_NA_ABERTURA, prefs(context).getBoolean(CHAVE_ABRINDO, false))
            .commit()
    }

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
