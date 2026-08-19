package com.controlefinanceiro.app

import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager

class SaldoWidgetProvider : AppWidgetProvider() {
    override fun onUpdate(context: Context, appWidgetManager: AppWidgetManager, appWidgetIds: IntArray) {
        val pedido = OneTimeWorkRequestBuilder<SaldoWidgetWorker>().build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            "atualizar_widget_saldo", ExistingWorkPolicy.REPLACE, pedido
        )
    }
}
