package com.controlefinanceiro.app

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.core.content.FileProvider
import android.webkit.CookieManager
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Baixa o APK novo e abre o instalador do Android.
 *
 * Antes o aviso de atualização só abria o navegador na página de releases, e o
 * usuário tinha que achar o arquivo, baixar e instalar à mão — pior ainda
 * quando a release publicada estava atrás da versão que o servidor anunciava.
 *
 * Quem decide de ONDE baixar é o servidor, não o app: /api/versao-app devolve
 * o campo `apk`, e é ele que chega aqui. Por padrão aponta para o arquivo da
 * release no GitHub, montado a partir da versão anunciada — o que é anunciado
 * e o que é entregue são sempre o mesmo arquivo. Quem hospeda pode apontar
 * para outro lugar com APP_ANDROID_APK_URL, inclusive para o próprio servidor.
 *
 * O app não tem endereço de repositório embutido, de propósito: cada instalação
 * aponta para o servidor de quem a hospeda, e trocar o repositório do projeto
 * não pode exigir um APK novo de todo mundo.
 *
 * Importante: o Android NÃO permite instalar sem o usuário confirmar. Nenhum
 * app comum pode — só quem é dono do dispositivo (MDM) ou tem root. O que dá
 * para eliminar é o trabalho de achar e baixar o arquivo; a tela final de
 * confirmação do sistema é obrigatória, e é uma proteção, não um defeito.
 */
object AtualizadorApp {

    /**
     * Baixa o APK e devolve o arquivo, ou null se falhar.
     *
     * O cookie do WebView vai junto quando o endereço aceita um — é o caso de
     * quem serve o APK do próprio servidor por APP_ANDROID_APK_URL, onde a rota
     * exige sessão e sem o cookie viria uma página de login em vez do arquivo.
     * No caminho padrão, o GitHub, não há cookie para aquele domínio e nada é
     * enviado; é por isso que a checagem de Content-Type abaixo existe de
     * qualquer forma.
     */
    fun baixar(context: Context, url: String): File? {
        return try {
            val destino = File(context.cacheDir, "atualizacoes").apply { mkdirs() }
            val arquivo = File(destino, "FinanCerto-novo.apk")
            if (arquivo.exists()) arquivo.delete()

            val conexao = (URL(url).openConnection() as HttpURLConnection).apply {
                connectTimeout = 30_000
                readTimeout = 120_000
                instanceFollowRedirects = true
                CookieManager.getInstance().getCookie(url)?.let {
                    setRequestProperty("Cookie", it)
                }
            }

            if (conexao.responseCode != HttpURLConnection.HTTP_OK) {
                conexao.disconnect()
                return null
            }

            // Se o servidor devolver HTML (login, erro), não é APK — instalar
            // isso falharia com uma mensagem confusa do sistema.
            val tipo = conexao.contentType ?: ""
            if (tipo.startsWith("text/")) {
                conexao.disconnect()
                return null
            }

            conexao.inputStream.use { entrada ->
                arquivo.outputStream().use { saida -> entrada.copyTo(saida) }
            }
            conexao.disconnect()

            if (arquivo.length() < 100_000) null else arquivo
        } catch (e: Exception) {
            null
        }
    }

    /** Abre o instalador do Android para o APK baixado. */
    fun instalar(context: Context, arquivo: File): Boolean {
        return try {
            val uri: Uri = FileProvider.getUriForFile(
                context, "${context.packageName}.fileprovider", arquivo
            )
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            context.startActivity(intent)
            true
        } catch (e: Exception) {
            false
        }
    }

    /**
     * Se o Android já deixa este app instalar pacotes. A partir do Android 8
     * a permissão é por app e o usuário concede nas configurações; sem ela o
     * instalador abre direto na tela de permissão.
     */
    fun podeInstalar(context: Context): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.packageManager.canRequestPackageInstalls()
        } else {
            true
        }
    }
}
