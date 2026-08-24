package com.controlefinanceiro.app

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import android.webkit.JavascriptInterface
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat

private const val CANAL_APP = "avisos_app"

/**
 * Ponte entre a página e o app.
 *
 * Dentro do WebView a API de notificação do navegador não existe, então a
 * página achava que "este navegador não faz notificações" — justamente onde os
 * avisos funcionam melhor, porque o app confere as contas em segundo plano.
 * Com esta ponte a página descobre que está rodando dentro do app e usa a
 * notificação nativa.
 *
 * A superfície é de propósito mínima: só informar que é o app e disparar um
 * aviso local. Nada aqui lê dados nem mexe em arquivos.
 */
class PonteApp(private val context: Context) {

    @JavascriptInterface
    fun ehApp(): Boolean = true

    @JavascriptInterface
    fun versao(): String = try {
        context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: ""
    } catch (e: Exception) { "" }

    /** Dispara uma notificação local. Devolve false se o Android recusou. */
    @JavascriptInterface
    fun notificar(titulo: String, texto: String): Boolean {
        criarCanal()
        val notificacao = NotificationCompat.Builder(context, CANAL_APP)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(titulo)
            .setContentText(texto)
            .setStyle(NotificationCompat.BigTextStyle().bigText(texto))
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setAutoCancel(true)
            .build()
        return try {
            NotificationManagerCompat.from(context)
                .notify(titulo.hashCode(), notificacao)
            true
        } catch (e: SecurityException) {
            // Permissão de notificação negada nas configurações do Android.
            false
        }
    }

    /**
     * Baixa a atualização do próprio servidor e abre o instalador.
     *
     * Roda numa thread porque o WebView chama isto na thread principal e
     * baixar 5 MB ali travaria a tela. Devolve na hora: quem acompanha o
     * resultado é a notificação e o instalador que abre sozinho.
     */
    @JavascriptInterface
    fun baixarEInstalar(url: String): Boolean {
        Thread {
            val arquivo = AtualizadorApp.baixar(context, url)
            if (arquivo == null) {
                notificar("Atualização", "Não foi possível baixar a atualização.")
            } else {
                AtualizadorApp.instalar(context, arquivo)
            }
        }.start()
        return true
    }

    /** Se o Android já deixa este app instalar pacotes. */
    @JavascriptInterface
    fun podeInstalar(): Boolean = AtualizadorApp.podeInstalar(context)

    /** Se o Android já autoriza notificações deste app. */
    @JavascriptInterface
    fun podeNotificar(): Boolean =
        NotificationManagerCompat.from(context).areNotificationsEnabled()

    private fun criarCanal() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val canal = NotificationChannel(
                CANAL_APP, "Avisos do FinanCerto", NotificationManager.IMPORTANCE_DEFAULT
            ).apply { description = "Avisos de contas, cartões e saldo" }
            context.getSystemService(NotificationManager::class.java)
                .createNotificationChannel(canal)
        }
    }
}
