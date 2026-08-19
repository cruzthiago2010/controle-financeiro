package com.controlefinanceiro.app

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Locale

private const val CANAL_CONTAS = "contas_vencendo"
private const val PREFS = "controle_financeiro_prefs"
private const val CHAVE_NOTIFICADOS = "ids_notificados"

class BillCheckWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        try {
            // Endereço configurado pela pessoa; sem ele não há servidor para consultar.
            val base = Servidor.url(applicationContext) ?: return@withContext Result.success()
            val cookie = android.webkit.CookieManager.getInstance().getCookie(base)
            if (cookie.isNullOrBlank()) return@withContext Result.success()

            val mes = SimpleDateFormat("yyyy-MM", Locale.US).format(java.util.Date())
            val url = URL("$base/api/dashboard?mes=$mes")
            val conn = url.openConnection() as HttpURLConnection
            conn.setRequestProperty("Cookie", cookie)
            conn.connectTimeout = 15000
            conn.readTimeout = 15000

            if (conn.responseCode != 200) return@withContext Result.success()
            val corpo = conn.inputStream.bufferedReader().use { it.readText() }
            val json = JSONObject(corpo)
            val vencendo = json.optJSONArray("contas_vencendo") ?: JSONArray()

            val hoje = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(java.util.Date())
            val amanha = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(
                java.util.Date(System.currentTimeMillis() + 24 * 60 * 60 * 1000)
            )

            val prefs = applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            val jaNotificados = prefs.getStringSet(CHAVE_NOTIFICADOS, emptySet())!!.toMutableSet()
            criarCanalNotificacao()

            for (i in 0 until vencendo.length()) {
                val item = vencendo.getJSONObject(i)
                val vencimento = item.optString("vencimento")
                if (vencimento != hoje && vencimento != amanha) continue

                val chave = "${item.optInt("id")}-$vencimento"
                if (jaNotificados.contains(chave)) continue

                val descricao = item.optString("descricao", "Conta")
                val valor = item.optDouble("valor", 0.0)
                val quando = if (vencimento == hoje) "vence hoje" else "vence amanhã"
                notificar(item.optInt("id"), descricao, valor, quando)
                jaNotificados.add(chave)
            }

            prefs.edit().putStringSet(CHAVE_NOTIFICADOS, jaNotificados).apply()
            Result.success()
        } catch (e: Exception) {
            Result.success()
        }
    }

    private fun criarCanalNotificacao() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val canal = NotificationChannel(
                CANAL_CONTAS, "Contas vencendo", NotificationManager.IMPORTANCE_DEFAULT
            ).apply { description = "Avisos de contas que vencem hoje ou amanhã" }
            val gerenciador = applicationContext.getSystemService(NotificationManager::class.java)
            gerenciador.createNotificationChannel(canal)
        }
    }

    private fun notificar(id: Int, descricao: String, valor: Double, quando: String) {
        val valorFormatado = String.format(Locale("pt", "BR"), "R$ %,.2f", valor)
        val notificacao = NotificationCompat.Builder(applicationContext, CANAL_CONTAS)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("$descricao $quando")
            .setContentText(valorFormatado)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setAutoCancel(true)
            .build()
        try {
            NotificationManagerCompat.from(applicationContext).notify(id, notificacao)
        } catch (e: SecurityException) {
            // Usuário não concedeu permissão de notificação; ignora silenciosamente.
        }
    }
}
