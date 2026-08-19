package com.controlefinanceiro.app

import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Locale

class SaldoWidgetWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val gerenciador = AppWidgetManager.getInstance(applicationContext)
        val componente = ComponentName(applicationContext, SaldoWidgetProvider::class.java)
        val ids = gerenciador.getAppWidgetIds(componente)
        if (ids.isEmpty()) return@withContext Result.success()

        val views = RemoteViews(applicationContext.packageName, R.layout.widget_saldo)
        val intent = Intent(applicationContext, MainActivity::class.java)
        val pendingIntent = android.app.PendingIntent.getActivity(
            applicationContext, 0, intent,
            android.app.PendingIntent.FLAG_UPDATE_CURRENT or android.app.PendingIntent.FLAG_IMMUTABLE
        )
        views.setOnClickPendingIntent(R.id.widgetValor, pendingIntent)
        views.setOnClickPendingIntent(R.id.widgetLabel, pendingIntent)

        try {
            // Endereço configurado pela pessoa; sem ele não há servidor para consultar.
            val base = Servidor.url(applicationContext) ?: return@withContext Result.success()
            val cookie = android.webkit.CookieManager.getInstance().getCookie(base)
            if (cookie.isNullOrBlank()) {
                views.setTextViewText(R.id.widgetValor, "Abra o app")
                gerenciador.updateAppWidget(componente, views)
                return@withContext Result.success()
            }

            val mes = SimpleDateFormat("yyyy-MM", Locale.US).format(java.util.Date())
            val url = URL("$base/api/dashboard?mes=$mes")
            val conn = url.openConnection() as HttpURLConnection
            conn.setRequestProperty("Cookie", cookie)
            conn.connectTimeout = 15000
            conn.readTimeout = 15000

            if (conn.responseCode != 200) {
                gerenciador.updateAppWidget(componente, views)
                return@withContext Result.success()
            }
            val corpo = conn.inputStream.bufferedReader().use { it.readText() }
            val json = JSONObject(corpo)
            val saldo = json.optDouble("saldo_atual", 0.0)
            views.setTextViewText(R.id.widgetValor, String.format(Locale("pt", "BR"), "R$ %,.2f", saldo))
            gerenciador.updateAppWidget(componente, views)
            Result.success()
        } catch (e: Exception) {
            gerenciador.updateAppWidget(componente, views)
            Result.success()
        }
    }
}
