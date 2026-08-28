package com.controlefinanceiro.app

import android.app.Application

/**
 * Existe só para instalar o registro de falhas antes de qualquer tela abrir.
 * Sem isso, uma falha na própria abertura não deixaria rastro nenhum.
 */
class App : Application() {
    override fun onCreate() {
        super.onCreate()
        RegistroDeFalhas.instalar(this)
    }
}
