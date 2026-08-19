package com.controlefinanceiro.app

import android.content.Context

/**
 * Endereço do servidor onde o FinanCerto está rodando.
 *
 * O app é distribuído sem endereço nenhum: cada pessoa hospeda o seu, então o
 * endereço é perguntado na primeira abertura e guardado no aparelho. Os
 * trabalhos em segundo plano (aviso de contas e widget de saldo) leem daqui
 * também, para não existir um endereço escrito em dois lugares.
 */
object Servidor {

    private const val PREFS = "financerto"
    private const val CHAVE_URL = "servidor_url"

    /** Endereço salvo, ou null se ainda não foi configurado. */
    fun url(context: Context): String? =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(CHAVE_URL, null)

    fun salvar(context: Context, url: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(CHAVE_URL, url).apply()
    }

    fun limpar(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().remove(CHAVE_URL).apply()
    }

    /**
     * Arruma o que a pessoa digitou e devolve uma URL utilizável, ou null se
     * não der para aproveitar.
     *
     * Aceita as formas que alguém escreveria naturalmente:
     *   financeiro.exemplo.com.br     -> https://financeiro.exemplo.com.br
     *   192.168.1.5:8420              -> http://192.168.1.5:8420
     *   http://meuservidor.local/     -> http://meuservidor.local
     *
     * Sem esquema, endereço de IP ou host local vira http (rede interna quase
     * nunca tem certificado) e o resto vira https.
     */
    fun normalizar(digitado: String): String? {
        var texto = digitado.trim().trimEnd('/')
        if (texto.isEmpty()) return null

        val temEsquema = texto.startsWith("http://", true) || texto.startsWith("https://", true)
        if (!temEsquema) {
            val semPorta = texto.substringBefore(':').substringBefore('/')
            val ehIp = Regex("""^\d{1,3}(\.\d{1,3}){3}$""").matches(semPorta)
            val ehLocal = semPorta.endsWith(".local", true) ||
                semPorta.equals("localhost", true)
            texto = if (ehIp || ehLocal) "http://$texto" else "https://$texto"
        }

        // Precisa ter um host de verdade — "https://" sozinho não serve.
        val host = texto.removePrefix("https://").removePrefix("http://")
            .substringBefore(':').substringBefore('/')
        if (host.isBlank() || !host.contains(Regex("""[A-Za-z0-9]"""))) return null

        return texto
    }
}
