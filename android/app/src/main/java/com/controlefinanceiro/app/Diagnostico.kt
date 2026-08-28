package com.controlefinanceiro.app

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.net.URL
import java.net.UnknownHostException
import javax.net.ssl.SSLException

private const val TEMPO_LIMITE = 8000
private const val MAXIMO_SALTOS = 4

/**
 * Teste de comunicação com o servidor, etapa por etapa.
 *
 * Dizer só "não foi possível falar com esse endereço" não ajuda quem está
 * instalando o app: o endereço pode estar certo e o problema ser o nome que
 * não resolve, a porta fechada, o certificado, um redirecionamento ou outro
 * site qualquer respondendo ali. Cada etapa é testada separadamente e todas
 * aparecem na tela, inclusive quando dão certo — ver "porta aberta, servidor
 * respondeu 200" é o que permite descartar a rede e olhar para o resto.
 *
 * O botão Conectar usa o mesmo caminho: o que decide se dá para salvar o
 * endereço é este relatório, então a mensagem de erro é sempre a etapa exata
 * que parou.
 */
object Diagnostico {

    enum class Situacao { OK, AVISO, FALHA }

    data class Etapa(val situacao: Situacao, val titulo: String, val detalhe: String)

    data class Relatorio(val etapas: List<Etapa>, val endereco: String?) {
        val passou: Boolean get() = endereco != null && etapas.none { it.situacao == Situacao.FALHA }
    }

    /** Roda o teste inteiro. Faz rede: chame fora da thread principal. */
    fun testar(context: Context, digitado: String): Relatorio {
        val etapas = mutableListOf<Etapa>()

        val endereco = Servidor.normalizar(digitado)
        if (endereco == null) {
            etapas += Etapa(
                Situacao.FALHA, "Endereço",
                "Não deu para entender o que foi digitado. Escreva algo como " +
                    "financeiro.seusite.com.br ou 192.168.1.10:8420."
            )
            return Relatorio(etapas, null)
        }
        etapas += Etapa(Situacao.OK, "Endereço", endereco)
        etapas += etapaDeRede(context)

        val url = URL(endereco)
        val host = url.host
        val porta = if (url.port != -1) url.port else url.defaultPort

        // Nome -> IP
        val ips = try {
            InetAddress.getAllByName(host).mapNotNull { it.hostAddress }
        } catch (e: UnknownHostException) {
            etapas += Etapa(
                Situacao.FALHA, "Nome (DNS)",
                "O nome \"$host\" não foi encontrado. Confira se está escrito certo. " +
                    "Se o servidor só existe na rede de casa, use o IP dele."
            )
            return Relatorio(etapas, null)
        } catch (e: Exception) {
            etapas += Etapa(Situacao.FALHA, "Nome (DNS)", descrever(e))
            return Relatorio(etapas, null)
        }
        etapas += Etapa(Situacao.OK, "Nome (DNS)", "$host → ${ips.joinToString(", ")}")

        // Porta aberta
        val inicio = System.currentTimeMillis()
        try {
            Socket().use { it.connect(InetSocketAddress(host, porta), TEMPO_LIMITE) }
        } catch (e: Exception) {
            etapas += Etapa(
                Situacao.FALHA, "Porta $porta",
                "Não abriu (${descrever(e)}). O servidor pode estar desligado, a porta " +
                    "pode ser outra, ou este celular pode não estar na mesma rede."
            )
            return Relatorio(etapas, null)
        }
        etapas += Etapa(
            Situacao.OK, "Porta $porta", "Aberta em ${System.currentTimeMillis() - inicio} ms"
        )

        // Resposta do servidor
        etapas += buscarLogin(endereco)
        if (etapas.any { it.situacao == Situacao.FALHA }) return Relatorio(etapas, null)

        versaoDoServidor(endereco)?.let {
            etapas += Etapa(Situacao.OK, "Versão do servidor", it)
        }

        return Relatorio(etapas, endereco)
    }

    // ---- etapas ----

    private fun etapaDeRede(context: Context): Etapa {
        val cm = context.getSystemService(ConnectivityManager::class.java)
            ?: return Etapa(Situacao.AVISO, "Rede do celular", "Não deu para consultar.")
        val rede = cm.activeNetwork
            ?: return Etapa(
                Situacao.FALHA, "Rede do celular",
                "O celular está sem rede. Ligue o Wi-Fi ou os dados."
            )
        val cap = cm.getNetworkCapabilities(rede)
            ?: return Etapa(Situacao.AVISO, "Rede do celular", "Estado desconhecido.")

        val tipo = when {
            cap.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "Wi-Fi"
            cap.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "dados móveis"
            cap.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "cabo"
            else -> "conectado"
        }
        // Rede local sem saída para a internet é normal quando o servidor é de
        // casa, então isso é aviso, não falha.
        return if (cap.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)) {
            Etapa(Situacao.OK, "Rede do celular", "$tipo, com internet")
        } else {
            Etapa(Situacao.AVISO, "Rede do celular", "$tipo, sem internet confirmada")
        }
    }

    /**
     * Busca /login seguindo os redirecionamentos na mão.
     *
     * O HttpURLConnection não segue sozinho quando o destino troca de http
     * para https — que é justamente o que um servidor atrás de proxy costuma
     * fazer — e devolveria um 301 seco como se fosse erro.
     */
    private fun buscarLogin(endereco: String): List<Etapa> {
        val etapas = mutableListOf<Etapa>()
        var alvo = "$endereco/login"
        var saltos = 0

        while (true) {
            val conexao: HttpURLConnection
            val codigo: Int
            try {
                conexao = (URL(alvo).openConnection() as HttpURLConnection).apply {
                    connectTimeout = TEMPO_LIMITE
                    readTimeout = TEMPO_LIMITE
                    instanceFollowRedirects = false
                    requestMethod = "GET"
                }
                codigo = conexao.responseCode
            } catch (e: SSLException) {
                etapas += Etapa(
                    Situacao.FALHA, "Certificado (HTTPS)",
                    "O celular não aceitou o certificado deste endereço (${descrever(e)}). " +
                        "Se o servidor é da rede local e não tem certificado, use http:// " +
                        "no começo do endereço."
                )
                return etapas
            } catch (e: SocketTimeoutException) {
                etapas += Etapa(
                    Situacao.FALHA, "Resposta",
                    "A porta abriu mas nada respondeu em ${TEMPO_LIMITE / 1000} segundos."
                )
                return etapas
            } catch (e: Exception) {
                etapas += Etapa(Situacao.FALHA, "Resposta", descrever(e))
                return etapas
            }

            if (codigo in 300..399) {
                val destino = conexao.getHeaderField("Location")
                conexao.disconnect()
                saltos++
                if (destino == null || destino.isBlank() || saltos > MAXIMO_SALTOS) {
                    etapas += Etapa(
                        Situacao.FALHA, "Redirecionamento",
                        "O servidor fica mandando para outro lugar e o caminho não termina."
                    )
                    return etapas
                }
                alvo = if (destino.startsWith("http", true)) destino
                else URL(URL(alvo), destino).toString()
                continue
            }

            val corpo = try {
                val fonte = if (codigo < 400) conexao.inputStream else conexao.errorStream
                fonte?.bufferedReader()?.use { it.readText().take(8000) } ?: ""
            } catch (e: Exception) {
                ""
            }
            conexao.disconnect()

            if (saltos > 0) {
                etapas += Etapa(Situacao.OK, "Redirecionamento", "$saltos salto(s), até $alvo")
            }

            when {
                codigo in 200..299 ->
                    etapas += Etapa(Situacao.OK, "Resposta do servidor", "$codigo em /login")
                codigo == 401 || codigo == 403 ->
                    etapas += Etapa(
                        Situacao.FALHA, "Resposta do servidor",
                        "$codigo: alguma coisa na frente do servidor (proxy, Cloudflare " +
                            "Access, senha do próprio servidor) está barrando antes da " +
                            "tela de entrada do FinanCerto."
                    )
                codigo == 404 ->
                    etapas += Etapa(
                        Situacao.FALHA, "Resposta do servidor",
                        "404: respondeu, mas não existe /login aqui. O endereço leva a " +
                            "outro serviço, ou falta o caminho do FinanCerto."
                    )
                codigo >= 500 ->
                    etapas += Etapa(
                        Situacao.FALHA, "Resposta do servidor",
                        "$codigo: o servidor está no ar mas com erro interno."
                    )
                else ->
                    etapas += Etapa(
                        Situacao.FALHA, "Resposta do servidor", "Resposta inesperada ($codigo)."
                    )
            }
            if (etapas.any { it.situacao == Situacao.FALHA }) return etapas

            // Confere que é o FinanCerto, e não outro site que por acaso
            // responde nesse endereço.
            val ehOApp = corpo.contains("FinanCerto", true) || corpo.contains("loginForm", true)
            etapas += if (ehOApp) {
                Etapa(Situacao.OK, "É o FinanCerto", "Tela de entrada reconhecida")
            } else {
                Etapa(
                    Situacao.FALHA, "É o FinanCerto",
                    "Respondeu, mas a página não é a do FinanCerto" +
                        (titulo(corpo)?.let { " (a página se chama \"$it\")" } ?: "") + "."
                )
            }
            return etapas
        }
    }

    /** Informativo: some em silêncio se o servidor não responder isso. */
    private fun versaoDoServidor(endereco: String): String? = try {
        val conexao = (URL("$endereco/api/versao-app").openConnection() as HttpURLConnection).apply {
            connectTimeout = TEMPO_LIMITE
            readTimeout = TEMPO_LIMITE
            requestMethod = "GET"
        }
        val texto = if (conexao.responseCode in 200..299) {
            conexao.inputStream.bufferedReader().use { it.readText() }
        } else null
        conexao.disconnect()
        texto?.let { org.json.JSONObject(it).optString("versao_atual").ifBlank { null } }
            ?.let { "app publicado: $it" }
    } catch (e: Exception) {
        null
    }

    private fun titulo(corpo: String): String? =
        Regex("<title[^>]*>(.*?)</title>", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
            .find(corpo)?.groupValues?.get(1)?.trim()?.take(60)?.ifBlank { null }

    private fun descrever(e: Exception): String = when (e) {
        is SocketTimeoutException -> "demorou demais para responder"
        is UnknownHostException -> "nome não encontrado"
        is SSLException -> e.message ?: "falha no certificado"
        else -> e.message ?: e.javaClass.simpleName
    }
}
